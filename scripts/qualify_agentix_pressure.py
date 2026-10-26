#!/usr/bin/env python3
"""Exercise finite Agentix backing with actual small native GPU/Host pools."""

from __future__ import annotations

import argparse
from array import array
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.runtime.agentix_capacity import AgentixHostBacking, AgentixNativeCapacity
from beliefkv.runtime.agentix_swap import AgentixNativeSwap
from scripts.qualify_agentix_swap import gpu_processes, poll_until
from scripts import run_causal_comparison as controls
from scripts.run_agentx_baseline import freeze, predecessor_finished, prefix_qualification_environment


SOURCES = (
    "scripts/qualify_agentix_pressure.py", "scripts/qualify_agentix_swap.py",
    "scripts/run_agentx_baseline.py", "scripts/run_causal_comparison.py",
    "beliefkv/runtime/agentix_capacity.py", "beliefkv/runtime/agentix_swap.py",
    "beliefkv/runtime/paper_prefix_transfers.py",
)
PROPERTIES = (
    "actual_finite_host_and_device_pools",
    "backing_gate_deferred_unstarted_work",
    "observed_decode_growth_triggered_backing_drain",
    "selected_live_call_kept_progress_until_completion",
    "completion_reopened_backing_admission",
    "real_host_exhaustion_refused_backup_without_device_release",
    "paused_host_ownership_survived_real_reclaim",
    "backup_retried_after_real_host_capacity_release",
    "all_restored_key_and_value_bytes_preserved",
    "all_request_inputs_outputs_and_samplers_preserved",
    "all_paused_requests_restored_and_host_ownership_released",
)


def validate_report(report, *, engine_tree, patch_sha256):
    swap = report.get("swap") or {}
    growth, refusal = report.get("backing_growth") or {}, report.get("host_refusal") or {}
    if (
        report.get("status") != "agentix_native_pressure_components_qualified"
        or report.get("native_engine", {}).get("actual_tree") != engine_tree
        or report.get("native_engine", {}).get("sha256") != patch_sha256
        or any(report.get(name) is not True for name in PROPERTIES)
        or report.get("device_tokens") != 128 or report.get("host_tokens") != 257
        or report.get("layers") != 2
        or growth.get("initial", {}).get("normal_live_budget_tokens") != 193
        or growth.get("initial", {}).get("draining") is not False
        or growth.get("after_completion", {}).get("draining") is not False
        or growth.get("selected_growth_tokens_after_drain") != 34
        or refusal.get("refused", {}).get("status") != "backup_declined"
        or refusal.get("refused", {}).get("host_capacity_refused") is not True
        or type(refusal.get("actual_host_blocker_slots_released")) is not int
        or refusal["actual_host_blocker_slots_released"] <= 0
        or swap.get("pending_requests") != 0 or swap.get("paused_host_pins") != 0
        or swap.get("counts", {}).get("backup_declined") != 1
        or swap.get("counts", {}).get("paused_requests") != 6
        or swap.get("counts", {}).get("resumed_requests") != 6
        or len(report.get("remaining_paused_requests_restored", ())) != 4
        or any(
            report.get(name) is not False for name in (
                "whole_llm_pressure_progress_qualified", "model_numerical_equivalence_verified",
                "complete_baseline_qualified", "performance_accepted",
            )
        )
        or any(
            report.get("source_sha256", {}).get(name) != sha256_file(ROOT / name)
            for name in SOURCES
        )
    ):
        raise ValueError("native Agentix pressure evidence is missing, stale or overclaimed")


class NativePressureFixture:
    """Small real pools; explicit fixture drains are not scheduler accounting."""

    context_limit = 64

    def __init__(self, engine):
        sys.path.insert(0, str(engine / "python"))
        import torch
        from sglang.srt.mem_cache.allocator import TokenToKVPoolAllocator
        from sglang.srt.mem_cache.cache_init_params import CacheInitParams
        from sglang.srt.mem_cache.memory_pool import MHATokenToKVPool, ReqToTokenPool
        from sglang.srt.mem_cache.unified_cache.component_type import ComponentType
        from sglang.srt.mem_cache.unified_radix_cache import UnifiedRadixCache
        from sglang.srt.server_args import ServerArgs, set_global_server_args_for_scheduler

        if torch.cuda.device_count() != 1:
            raise ValueError("Agentix pressure fixture requires one explicit visible GPU")
        self.torch = torch
        settings = ServerArgs(
            model_path="dummy", page_size=1, hicache_ratio=2.,
            hicache_io_backend="kernel", hicache_mem_layout="page_first",
            hicache_write_policy="write_back",
        )
        set_global_server_args_for_scheduler(settings)
        self.pool = MHATokenToKVPool(
            size=128, page_size=1, head_num=2, head_dim=64, dtype=torch.bfloat16,
            layer_num=2, device="cuda", enable_memory_saver=False,
        )
        self.rows = ReqToTokenPool(
            size=8, max_context_len=self.context_limit, device="cuda",
            enable_memory_saver=False,
        )
        self.allocator = TokenToKVPoolAllocator(
            size=128, dtype=torch.bfloat16, device="cuda", kvcache=self.pool, need_sort=False,
        )
        params = CacheInitParams(
            disable=False, req_to_token_pool=self.rows,
            token_to_kv_pool_allocator=self.allocator, page_size=1,
            tree_components=(ComponentType.FULL,), enable_session_radix_cache=True,
        )
        self.cache = UnifiedRadixCache(params)
        self.cache.init_hicache(settings, params)
        self.cache.load_back_threshold = 1
        self.host = self.cache.host_pool_group.anchor_entry.host_pool
        self.drained = torch.cuda.Event()
        self.sync()
        self.actor = AgentixNativeSwap(self.cache, drained=self.drained.query)
        self.capacity = AgentixNativeCapacity(
            self.actor.transfers, context_limit=self.context_limit, decode_growth_tokens=4,
        )
        self.backing = AgentixHostBacking(self.capacity, self.actor)
        self.active, self.expected, self.original = {}, {}, {}
        self.host_blocker = None

    def sync(self):
        self.drained.record(self.torch.cuda.current_stream())
        self.drained.synchronize()

    def request(self, identity, *, prompt_tokens=26, running=True):
        from sglang.srt.managers.schedule_batch import Req
        from sglang.srt.sampling.sampling_params import SamplingParams

        req = Req(
            rid=identity, origin_input_text="",
            origin_input_ids=array("q", range(1, prompt_tokens + 1)),
            extra_key=identity, cache_salt=identity,
            sampling_params=SamplingParams(
                temperature=1., top_k=1, max_new_tokens=self.context_limit,
            ),
            beliefkv_metadata={
                "root_workflow_id": "workflow:" + identity,
                "invocation_id": "agent:" + identity,
                "context_id": "context:" + identity, "context_epoch": 0,
            },
        )
        req.last_node = self.cache.root_node_handle()
        if running:
            req.output_ids.extend((101, 102))
            count = req.seqlen - 1
            if self.rows.alloc([req]) is None:
                raise ValueError("native pressure fixture could not allocate a request row")
            indices = self.allocator.alloc(count)
            if indices is None:
                raise ValueError("native pressure fixture could not allocate its initial KV")
            self.rows.write((req.kv.req_pool_idx, slice(0, count)), indices)
            req.kv.kv_committed_len = req.kv.kv_allocated_len = count
            self._write(req, indices, offset=0)
            self.active[req.rid] = req
            self._remember(req)
        return req

    def _write(self, req, indices, *, offset):
        torch = self.torch
        shape = self.pool.k_buffer[0].shape[1:]
        tokens = torch.tensor(
            (list(req.origin_input_ids) + list(req.output_ids[:-1]))[offset:],
            device=indices.device, dtype=torch.float32,
        )[:, None, None]
        feature = torch.arange(
            shape[0] * shape[1], device=indices.device, dtype=torch.float32,
        ).reshape(1, *shape) / 512.
        for layer, (key, value) in enumerate(zip(
            self.pool.k_buffer, self.pool.v_buffer, strict=True,
        )):
            key[indices] = (tokens + feature + layer / 8.).to(key.dtype)
            value[indices] = (tokens + feature + layer / 8. + 4.).to(value.dtype)
        self.sync()

    def _indices(self, req):
        return self.rows.req_to_token[
            req.kv.req_pool_idx, :req.effective_kv_committed_len()
        ]

    def _remember(self, req):
        indices = self._indices(req)
        self.expected[req.rid] = [
            (key[indices].clone(), value[indices].clone())
            for key, value in zip(self.pool.k_buffer, self.pool.v_buffer, strict=True)
        ]
        self.original[req.rid] = (
            tuple(req.origin_input_ids), tuple(req.output_ids), req.sampling_params,
        )

    def preserved(self, req):
        indices = self._indices(req)
        original = self.original[req.rid]
        return (
            tuple(req.origin_input_ids) == original[0]
            and tuple(req.output_ids) == original[1]
            and req.sampling_params is original[2]
            and all(
                self.torch.equal(key[indices], old_key)
                and self.torch.equal(value[indices], old_value)
                for (key, value), (old_key, old_value) in zip(
                    zip(self.pool.k_buffer, self.pool.v_buffer, strict=True),
                    self.expected[req.rid], strict=True,
                )
            )
        )

    def pause(self, requests):
        self.sync()
        self.actor.prepare(requests, running_batch=SimpleNamespace(reqs=list(self.active.values())))
        before = self.allocator.available_size()
        if self.actor.commit() is not None or self.allocator.available_size() != before:
            raise ValueError("native pressure fixture released KV before processing its D2H ACK")
        poll_until(lambda: not self.cache.ongoing_write_through, self.cache.writing_check)
        result = self.actor.commit()
        if result["status"] != "paused" or result["allocator_available_increase"] <= 0:
            raise ValueError("native pressure checkpoint did not release backed exclusive KV")
        for req in requests:
            self.active.pop(req.rid)
        return result

    def grow(self, req, tokens):
        if tokens <= 0 or req.seqlen + tokens > self.context_limit:
            raise ValueError("native pressure fixture growth exceeds its declared context")
        if not self.preserved(req):
            raise ValueError("native pressure fixture lost KV before decode growth")
        expected = self.expected[req.rid]
        offset = req.kv.kv_allocated_len
        indices = self.allocator.alloc(tokens)
        if indices is None:
            raise ValueError("selected native decode could not progress inside its reserved budget")
        output_count = len(req.output_ids)
        req.output_ids.extend(200 + output_count + step for step in range(tokens))
        self.rows.write((req.kv.req_pool_idx, slice(offset, offset + tokens)), indices)
        req.kv.kv_committed_len = req.kv.kv_allocated_len = offset + tokens
        self._write(req, indices, offset=offset)
        old_indices = self._indices(req)[:offset]
        if any(
            not self.torch.equal(key[old_indices], old_key)
            or not self.torch.equal(value[old_indices], old_value)
            for (key, value), (old_key, old_value) in zip(
                zip(self.pool.k_buffer, self.pool.v_buffer, strict=True),
                expected, strict=True,
            )
        ):
            raise ValueError("native decode growth overwrote an existing KV prefix")
        self._remember(req)

    def finish(self, req):
        from sglang.srt.mem_cache.common import release_kv_cache

        self.sync()
        if not self.preserved(req):
            raise ValueError("native decode growth changed existing request KV or work")
        release_kv_cache(req, self.cache, is_insert=False)
        self.active.pop(req.rid)

    def restore(self, req):
        pin = self.actor.paused[req.rid]
        leaf = self.cache.tree_core.node_by_id(pin.leaf)
        if not self.cache.load_back(leaf.id):
            raise ValueError("native pressure fixture could not restore a paused checkpoint")
        self.cache.cache_controller.start_loading()
        req.init_next_round_input(self.cache)
        if len(req.prefix_indices) != req.seqlen - 1 or self.rows.alloc([req]) is None:
            raise ValueError("native pressure resumption lost its complete computed prefix")
        count = len(req.prefix_indices)
        self.rows.write((req.kv.req_pool_idx, slice(0, count)), req.prefix_indices)
        req.lock_receipt = self.cache.inc_lock_ref(req.last_node).to_dec_params()
        req.kv.kv_committed_len = req.kv.kv_allocated_len = count
        if not self.actor.resumed(req):
            raise ValueError("native pressure resumption did not consume its Host ownership")
        poll_until(lambda: not self.cache.ongoing_load_back, self.cache.loading_check)
        self.active[req.rid] = req
        if not self.preserved(req):
            raise ValueError("native pressure resumption changed KV bytes, tokens or sampler")

    def close(self):
        try:
            if self.host_blocker is not None:
                self.host.free(self.host_blocker)
                self.host_blocker = None
            self.actor.close()
        finally:
            self.cache.release_host_resources()


def backing_growth(fixture):
    requests = []
    for index in range(6):
        req = fixture.request("backing-" + str(index))
        requests.append(req)
        if index < 2:
            fixture.pause((req,))
    waiting = fixture.request("unstarted", prompt_tokens=8, running=False)
    budget = fixture.backing
    budget.prepare((*requests, waiting))
    initial = budget.status()
    if (
        fixture.allocator.size != 128 or fixture.host.size != 257
        or initial["normal_live_budget_tokens"] != 193
        or budget.draining or fixture.capacity.required_tokens(requests) != 192
        or budget.eligible(waiting) or budget.can_fit(waiting, ())
    ):
        raise ValueError("finite native backing did not defer an unstarted context at its actual limit")
    selected = requests[2]
    fixture.grow(selected, 2)
    budget.prepare((*requests, waiting))
    if not budget.draining or fixture.capacity.required_tokens(requests) != 194:
        raise ValueError("observed native decode growth did not trigger finite backing drain")
    budget.selected((selected.rid,))
    if not budget.can_fit(selected, ()) or any(
        budget.eligible(req) for req in requests if req is not selected
    ):
        raise ValueError("finite backing did not retain exactly its selected live call")
    fixture.pause(tuple(req for req in fixture.active.values() if req is not selected))
    growth = fixture.context_limit - selected.seqlen
    fixture.grow(selected, growth)
    budget.prepare((*requests, waiting))
    if budget.single != selected.rid or not budget.can_fit(selected, ()):
        raise ValueError("selected native decode lost its finite backing progress reserve")
    fixture.finish(selected)
    remaining = tuple(req for req in requests if req is not selected)
    budget.prepare((*remaining, waiting))
    if budget.draining or not budget.eligible(waiting) or not budget.can_fit(waiting, ()):
        raise ValueError("actual completion did not reopen finite backing admission")
    fixture.restore(requests[0])
    return {
        "initial": initial, "after_completion": budget.status(),
        "selected_growth_tokens_after_drain": growth,
        "restored_after_drain": requests[0].rid,
        "remaining_paused": len(fixture.actor.paused),
    }


def host_refusal(fixture):
    victim = fixture.request("host-refusal", prompt_tokens=39)
    before, row = fixture.allocator.available_size(), victim.kv.req_pool_idx
    fixture.host_blocker = fixture.host.alloc(fixture.host.available_size())
    if fixture.host_blocker is None or fixture.host.available_size() != 0:
        raise ValueError("could not exhaust actual remaining native Host slots")
    fixture.actor.prepare((victim,), running_batch=SimpleNamespace(reqs=list(fixture.active.values())))
    refused = fixture.actor.commit()
    if (
        refused is None or refused["status"] != "backup_declined"
        or refused.get("host_capacity_refused") is not True
        or victim.kv.req_pool_idx != row or not victim.kv.holds_kv
        or fixture.allocator.available_size() != before or not fixture.preserved(victim)
    ):
        raise ValueError("actual Host refusal changed the running native request or device capacity")
    fixture.actor.cancel_preparation()
    for pin in fixture.actor.paused.values():
        path = fixture.actor.transfers.resolve(pin.snapshot, owner=fixture.actor._owner(pin.proof))
        if not path or any(node.component_data[0].host_lock_ref <= 0 for node in path):
            raise ValueError("real Host pressure lost paused complete-prefix ownership")
    if fixture.cache.evict_host(fixture.host.size) != 0:
        raise ValueError("real Host reclaim released a protected paused checkpoint")
    released = fixture.host.free(fixture.host_blocker)
    fixture.host_blocker = None
    fixture.pause((victim,))
    fixture.restore(victim)
    if any(not fixture.preserved(req) for req in fixture.active.values()):
        raise ValueError("Host retry damaged another live native request")
    return {
        "refused": refused, "actual_host_blocker_slots_released": released,
        "retry_and_consumer_handoff_verified": True,
    }


def qualify(engine, canonical_patch):
    if gpu_processes():
        raise ValueError("GPU is occupied before the bounded Agentix pressure check")
    native = git_source_state(engine, canonical_patch=canonical_patch)["canonical_patch"]
    if not native["matches_worktree"]:
        raise ValueError("native tree differs from the canonical pressure-check patch")
    fixture = NativePressureFixture(engine)
    try:
        growth = backing_growth(fixture)
        refusal = host_refusal(fixture)
        for req in tuple(fixture.active.values()):
            fixture.finish(req)
        restored = []
        for pin in tuple(fixture.actor.paused.values()):
            req = pin.proof.request
            fixture.restore(req)
            fixture.finish(req)
            restored.append(req.rid)
        if fixture.actor.paused or fixture.actor.pending:
            raise ValueError("native pressure checks left paused Host ownership unsettled")
        report = {
            "status": "agentix_native_pressure_components_qualified",
            "scope": "Actual small native GPU/Host pools, explicit fixture drains and synthetic KV; no LLM execution.",
            "native_engine": native, "gpu": fixture.torch.cuda.get_device_name(),
            "device_tokens": fixture.allocator.size, "host_tokens": fixture.host.size,
            "layers": len(fixture.pool.k_buffer),
            "backing_growth": growth, "host_refusal": refusal,
            "remaining_paused_requests_restored": restored,
            **dict.fromkeys(PROPERTIES, True),
            "swap": fixture.actor.status(), "backing": fixture.backing.status(),
            "whole_llm_pressure_progress_qualified": False,
            "model_numerical_equivalence_verified": False,
            "complete_baseline_qualified": False, "performance_accepted": False,
        }
        return report
    finally:
        fixture.close()


def run(args):
    args.output_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    try:
        result = qualify(args.engine_checkout.resolve(), args.canonical_patch.resolve())
    except BaseException as error:
        result = {
            "status": "failed", "error_type": type(error).__name__, "error": str(error),
            "performance_accepted": False,
        }
        raise
    finally:
        result["elapsed_seconds"] = time.monotonic() - started
        result["source_sha256"] = {name: sha256_file(ROOT / name) for name in SOURCES}
        with (args.output_dir / "summary.json").open("x") as output:
            json.dump(result, output, indent=2, allow_nan=False)
            output.write("\n")
        print(json.dumps(result, indent=2, allow_nan=False))


def check_registration(registration):
    if registration["source_sha256"] != {
        name: sha256_file(ROOT / name) for name in SOURCES
    }:
        raise ValueError("Agentix pressure sources differ from their frozen registration")
    predecessor = Path(registration["after_run_root"])
    if (
        sha256_file(predecessor / "registration.json")
        != registration["predecessor_registration_sha256"]
        or json.loads((predecessor / "supervisor.json").read_bytes())
        != registration["predecessor_supervisor"]
    ):
        raise ValueError("Agentix pressure predecessor registration or process identity changed")


def start(args):
    root = args.run_root.resolve()
    if root.exists():
        raise FileExistsError("preserve previous Agentix pressure qualification artifacts")
    reference = json.loads(args.reference.read_bytes())
    controls.fresh.check_hold(reference, released=True)
    predecessor = args.after_run_root.resolve()
    identity = json.loads((predecessor / "supervisor.json").read_bytes())
    root.mkdir(parents=True)
    frozen = freeze(args.engine_checkout, args.canonical_patch)
    checkout = Path(frozen["checkout"])
    registration = {
        **frozen, "run_root": str(root), "port": args.port,
        "hold_file": reference["hold_file"],
        "reference_environment": reference["reference_environment"],
        "after_run_root": str(predecessor),
        "predecessor_registration_sha256": sha256_file(predecessor / "registration.json"),
        "predecessor_supervisor": identity,
        "source_sha256": {name: sha256_file(checkout / name) for name in SOURCES},
        "scope": "small_native_gpu_host_pressure_components_not_a_model_or_performance_run",
        "performance_accepted": False,
    }
    controls.reservation.write_json(root / "registration.json", registration)
    with (root / "supervisor.log").open("x") as log:
        process = subprocess.Popen([
            sys.executable, str(checkout / "scripts/qualify_agentix_pressure.py"),
            "watch", "--registration", str(root / "registration.json"),
        ], cwd=checkout, env={**os.environ, "PYTHONHASHSEED": "21"},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True)
    supervisor = controls.reservation.process(process.pid)
    if supervisor is None:
        raise ValueError("Agentix pressure supervisor has no owned process identity")
    controls.reservation.write_json(root / "supervisor.json", supervisor)
    print(json.dumps({
        "status": "supervisor_started", "run": str(root), "supervisor": supervisor,
    }, indent=2))


def watch(path):
    registration = json.loads(path.read_bytes())
    root, owned, stopped = Path(registration["run_root"]), [], False
    state = {"status": "waiting_predecessor_or_resources", "performance_accepted": False}

    def stop(*_args):
        nonlocal stopped
        stopped = True

    def pulse():
        if stopped:
            raise ValueError("Agentix pressure supervisor received a stop request")
        controls.fresh.check_hold(registration, released=True)
        state.update(
            checked_at=controls.reservation.now().isoformat(),
            owned=controls.owned_processes(owned),
        )
        controls.reservation.write_json(root / "state.json", state)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with (root / "supervisor.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            check_registration(registration)
            while not predecessor_finished(registration["after_run_root"]) or not (
                controls.qualified.resources(registration["port"])["ready"]
            ):
                pulse()
                time.sleep(5.)
            pulse()
            check_registration(registration)
            state["status"] = "qualifying_native_agentix_pressure"
            output = root / "qualification"
            with (root / "qualification.log").open("x") as log:
                child = subprocess.Popen([
                    sys.executable, str(ROOT / "scripts/qualify_agentix_pressure.py"), "run",
                    "--engine-checkout", registration["engine_checkout"],
                    "--canonical-patch", str(ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch"),
                    "--output-dir", str(output),
                ], cwd=ROOT, env=prefix_qualification_environment(registration, os.environ),
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True)
                identity = controls.reservation.process(child.pid)
                if identity is None:
                    raise ValueError("Agentix GPU pressure child has no owned process identity")
                owned.append(identity)
                deadline = time.monotonic() + 180.
                while child.poll() is None:
                    pulse()
                    if time.monotonic() >= deadline:
                        raise TimeoutError("native Agentix pressure qualification expired")
                    time.sleep(.5)
                if child.wait():
                    raise ValueError("native Agentix pressure qualification failed; retain its evidence")
            report = json.loads((output / "summary.json").read_bytes())
            validate_report(
                report, engine_tree=registration["engine_tree"],
                patch_sha256=registration["native_patch_sha256"],
            )
            state.update(
                status="agentix_native_pressure_components_qualified", qualification=report,
                whole_llm_pressure_progress_qualified=False, performance_accepted=False,
            )
        except BaseException as error:
            state.update(status="failed", error_type=type(error).__name__, error=str(error))
        finally:
            try:
                controls.cleanup(root, owned, state["status"])
            except BaseException as error:
                state.update(status="failed", cleanup_error=repr(error))
            state["finished_at"] = controls.reservation.now().isoformat()
            controls.reservation.write_json(root / "summary.json", state)
            controls.reservation.write_json(root / "state.json", state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    direct = commands.add_parser("run")
    for name in ("engine-checkout", "canonical-patch", "output-dir"):
        direct.add_argument("--" + name, required=True, type=Path)
    launch = commands.add_parser("start")
    for name in ("run-root", "reference", "engine-checkout", "canonical-patch", "after-run-root"):
        launch.add_argument("--" + name, required=True, type=Path)
    launch.add_argument("--port", required=True, type=int)
    monitor = commands.add_parser("watch")
    monitor.add_argument("--registration", required=True, type=Path)
    args = parser.parse_args()
    {"run": run, "start": start, "watch": lambda value: watch(value.registration)}[args.command](args)


if __name__ == "__main__":
    main()
