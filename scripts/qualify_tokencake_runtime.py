#!/usr/bin/env python3
"""Queue real native TokenCake lifecycle and consumer-handoff checks."""

from __future__ import annotations

import argparse
from array import array
from contextlib import contextmanager
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

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.tokencake_baseline import TokenCakePaperAdapter
from scripts import run_causal_comparison as controls
from scripts.qualify_agentx_data_plane import predecessor_finished
from scripts.qualify_paper_prefix_transfers import gpu_processes, poll_until
from scripts.run_agentx_baseline import freeze, prefix_qualification_environment


QUALIFIED_SOURCES = (
    "scripts/qualify_tokencake_runtime.py",
    "beliefkv/runtime/tokencake_baseline.py",
    "beliefkv/runtime/tokencake_lifecycle.py",
    "beliefkv/runtime/tokencake_paper.py",
    "beliefkv/runtime/tokencake_capacity.py",
    "beliefkv/runtime/paper_prefix_transfers.py",
    "beliefkv/runtime/paper_baseline.py",
    "beliefkv/runtime/sglang_v0520_runtime.py",
    "beliefkv/runtime/sglang_v0520_physical.py",
    "beliefkv/policy/tokencake.py",
    "beliefkv/policy/tokencake_capacity.py",
    "beliefkv/policy/tokencake_lifecycle.py",
)
SCENARIOS = ("full_upload", "early_d2h", "early_h2d", "expiry", "retirement", "pressure")
REQUIRED_EVIDENCE = (
    "real_native_requests_and_prefill_adder",
    "first_fit_uses_only_observed_decode_rate",
    "recorded_generation_limit_does_not_change_policy_capacity",
    "no_device_release_before_d2h_ack",
    "deadline_controls_predictive_upload",
    "progressive_grants_bind_actual_device_allocations",
    "all_restored_layer_key_and_value_bytes_match",
    "split_host_and_capacity_ownership_preserved",
    "real_consumer_lock_receipt_consumes_reservation_once",
    "early_return_during_d2h_keeps_resident_source",
    "early_return_during_h2d_preserves_native_source_ownership",
    "native_demand_recovers_unuploaded_prefix",
    "expiry_and_explicit_retirement_release_optional_ownership",
    "pressure_revokes_optional_upload_before_request_preemption",
    "persistent_return_retains_ordinary_evictable_cache",
    "paper_receipts_reconciled_and_runtime_not_disabled",
    "all_native_capacity_and_host_pins_released",
    "full_prompt_native_radix_sessions_follow_the_same_paper_lifecycle",
    "native_session_generation_and_cached_source_ownership_preserved",
)
FIXTURE_PARAMETERS = {
    "generation_budget": 8, "context_limit": 64, "running_limit": 4,
    "prefill_token_budget": 32, "chunked_prefill_tokens": 8,
    "policy_block_tokens": 1, "eviction_window_tokens": 16,
}


def _complete_scenarios(observed, *, native_sessions):
    return type(observed) is dict and set(observed) == set(SCENARIOS) and all(
        row.get("status") == "complete"
        and row.get("final_uncommitted_tokens") == 64
        and row.get("grants") == (
            [] if name == "early_d2h"
            else [4, 2, 1, 1] if name == "full_upload" else [4]
        )
        and row.get("native_ack_tokens") == {
            "d2h": 8,
            "h2d": 0 if name == "early_d2h" else 8 if name in ("full_upload", "early_h2d") else 4,
        }
        and row.get("native_demand_h2d_tokens") == (4 if name == "early_h2d" else 0)
        and (
            name not in ("full_upload", "early_h2d")
            or row.get("capacity_counts", {}).get("upload_reservations_consumed") == 1
            and row.get("temporal_counts", {}).get("paper_upload_consumers") == 1
        )
        and (
            not native_sessions or (
                row.get("request_transport") == "full_prompt_native_radix_session"
                and row.get("native_session_request_count") == (
                    4 if name in ("full_upload", "early_d2h", "early_h2d") else 3
                )
                and type(row.get("native_session_generations")) is dict
                and len(row["native_session_generations"]) == 3
                and all(
                    type(value) is int and value > 0
                    for value in row["native_session_generations"].values()
                )
                and len(set(row["native_session_generations"].values())) == 3
                and type(row.get("native_session_source_anchors")) is int
                and row["native_session_source_anchors"] > 0
                and row.get("native_session_ownership_preserved") is True
            )
        )
        for name, row in observed.items()
    )


def validate_report(report, *, engine_tree, patch_sha256, root=ROOT):
    complete = _complete_scenarios(report.get("scenarios"), native_sessions=False) and (
        _complete_scenarios(report.get("native_session_scenarios"), native_sessions=True)
    )
    if (
        report.get("status") != "tokencake_native_lifecycle_qualified"
        or report.get("engine_tree") != engine_tree
        or report.get("native_patch_sha256") != patch_sha256
        or report.get("whole_model_or_performance_qualified") is not False
        or report.get("performance_accepted") is not False
        or not complete
        or any(report.get(name) is not True for name in REQUIRED_EVIDENCE)
        or report.get("source_sha256") != {
            name: sha256_file(root / name) for name in QUALIFIED_SOURCES
        }
    ):
        raise ValueError("TokenCake native lifecycle evidence or source differs")


@contextmanager
def isolated_environment():
    declared = {**paper_environment({}, "thunderagent"), "BELIEFKV_PAPER_BASELINE": ""}
    previous = {name: os.environ.get(name) for name in declared}
    try:
        os.environ.update(declared)
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def scenario(name, *, native_sessions=False):
    import torch
    from sglang.srt.mem_cache.allocator import TokenToKVPoolAllocator
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.cache_init_params import CacheInitParams
    from sglang.srt.mem_cache.memory_pool import MHATokenToKVPool, ReqToTokenPool
    from sglang.srt.mem_cache.radix_cache import RadixKey
    from sglang.srt.mem_cache.unified_cache.component_type import ComponentType
    from sglang.srt.mem_cache.unified_radix_cache import UnifiedRadixCache
    from sglang.srt.managers.schedule_batch import FINISH_LENGTH, Req, ScheduleBatch
    from sglang.srt.managers.schedule_policy import PrefillAdder
    from sglang.srt.model_executor.forward_batch_info import ForwardMode
    from sglang.srt.sampling.sampling_params import SamplingParams
    from sglang.srt.server_args import ServerArgs, set_global_server_args_for_scheduler

    settings = ServerArgs(
        model_path="dummy", page_size=1, hicache_ratio=2.,
        hicache_io_backend="kernel", hicache_mem_layout="page_first",
        hicache_write_policy="write_back",
    )
    set_global_server_args_for_scheduler(settings)
    pool = MHATokenToKVPool(
        size=64, page_size=1, head_num=2, head_dim=64, dtype=torch.bfloat16,
        layer_num=2, device="cuda", enable_memory_saver=False,
    )
    rows = ReqToTokenPool(size=4, max_context_len=64, device="cuda", enable_memory_saver=False)
    allocator = TokenToKVPoolAllocator(
        size=64, dtype=torch.bfloat16, device="cuda", kvcache=pool, need_sort=False,
    )
    params = CacheInitParams(
        disable=False, req_to_token_pool=rows, token_to_kv_pool_allocator=allocator,
        page_size=1, tree_components=(ComponentType.FULL,), enable_session_radix_cache=True,
    )
    cache = UnifiedRadixCache(params)
    cache.init_hicache(settings, params)
    cache.load_back_threshold = 1
    clock, commits, live = SimpleNamespace(now=10., sequence=0), [], {}
    session_requests, session_generations = {}, {}
    namespace = None if native_sessions else "workflow"
    with isolated_environment():
        runtime = NativeAdmissionRuntime()
        adapter = TokenCakePaperAdapter(
            runtime, **FIXTURE_PARAMETERS, clock=lambda: clock.now,
        )
        runtime._paper_baseline = adapter
        runtime.attach_native_cache(cache)

    def acknowledged(commit):
        commits.append(commit)
        runtime.on_native_transfer_commit(commit)

    cache.on_hicache_transfer_commit = acknowledged

    def send(kind, invocation="agent", **attributes):
        clock.sequence += 1
        runtime.on_events([RuntimeEvent(
            f"{name}:{clock.sequence}", clock.now * 1000. + clock.sequence * .001,
            kind, "workflow", invocation_id=invocation, context_id=invocation,
            context_epoch=0, agent_definition_id="worker", attributes=attributes,
        )])

    def request(identity, tokens, context):
        send(Kind.LLM_SUBMIT, context, request_id=identity)
        req = Req(
            rid=identity, origin_input_text="", origin_input_ids=array("q", tokens),
            sampling_params=SamplingParams(temperature=1., top_k=1, max_new_tokens=1),
            extra_key=namespace, cache_salt=namespace,
            session_id="tokencake-session-" + context if native_sessions else None,
            beliefkv_metadata={
                "root_workflow_id": "workflow", "invocation_id": context,
                "context_id": context, "context_epoch": 0,
                "full_prompt_replay_guaranteed": True,
            },
        )
        if native_sessions:
            req.session_generation = cache.ensure_session_generation(req.session_id)
            session_requests[req.rid] = req.session_id
            session_generations[req.session_id] = req.session_generation
        if not runtime.register_visible_request(req):
            raise ValueError("native lifecycle request was not registered")
        return req

    def batch(reqs, mode):
        return ScheduleBatch(reqs=list(reqs), batch_is_full=False, forward_mode=mode)

    def fill(indices, tokens):
        values = torch.tensor(tokens, dtype=torch.float32, device="cuda")[:, None, None]
        feature = torch.arange(128, device="cuda", dtype=torch.float32).reshape(1, 2, 64) / 512.
        for layer, (keys, vals) in enumerate(zip(pool.k_buffer, pool.v_buffer, strict=True)):
            keys[indices] = (values + feature + layer / 8.).to(torch.bfloat16)
            vals[indices] = (values + feature + layer / 8. + 4.).to(torch.bfloat16)

    def match(tokens):
        return cache.match_prefix(MatchPrefixParams(key=RadixKey(
            array("q", tokens), namespace, cache_salt=namespace,
        ), cow_mamba=False))

    def native_admit(req):
        plan = runtime.plan_native_prefill([req], running_batch=None, adder=None)
        chosen = select_native_prefill_candidates(
            [req], plan=plan, current_semantic_revision=runtime.semantic_revision,
        ).candidates
        if chosen != (req,) or not runtime.allow_native_prefill_candidate(
            req, running_batch=None, adder=None,
        ):
            return False
        req.init_next_round_input(cache, cow_mamba=False)
        adder = PrefillAdder(
            1, cache, allocator, None, 0., 64, 64, max_running_requests=4,
        )
        result = adder.add_one_req(req, has_chunked_req=False, truncation_align_size=None)
        admitted = req in adder.can_run_list
        runtime.on_prefill_candidate_result(req, admitted=admitted, result=result.name)
        if not admitted:
            return False
        if cache.cache_controller.load_queue:
            cache.cache_controller.start_loading()
        poll_until(lambda: not cache.ongoing_load_back, cache.loading_check)
        if rows.alloc([req]) is None:
            raise ValueError("native lifecycle request row was not allocated")
        count = req.extend_range.length
        private = allocator.alloc(count)
        if private is None:
            raise ValueError("native lifecycle private slots were not allocated")
        fill(private, req.get_fill_ids()[len(req.prefix_indices):])
        indices = torch.cat((req.prefix_indices, private))
        rows.write((req.kv.req_pool_idx, slice(0, len(indices))), indices)
        req.kv.kv_allocated_len = req.kv.kv_committed_len = len(indices)
        req.kv.cache_protected_len = len(req.prefix_indices)
        live[req.rid] = req
        runtime.on_batch_selected(batch([req], ForwardMode.EXTEND))
        return True

    def finish(req, *, preserve):
        torch.cuda.current_stream().synchronize()
        req.finished_reason = FINISH_LENGTH(len(req.output_ids))
        cache.cache_finished_req(
            req, is_insert=preserve, kv_len_to_handle=req.kv.kv_committed_len,
        )
        rows.free(req)
        live.pop(req.rid, None)
        if runtime.on_batch_completed(batch([req], ForwardMode.EXTEND)) != (req.rid,):
            raise ValueError("native lifecycle completion callback lost its request")

    try:
        send(Kind.WORKFLOW_START, None)
        for context in ("agent", "peer", "queued"):
            send(Kind.INVOCATION_CREATE, context)
        first = request("first", range(8), "agent")
        if not native_admit(first):
            raise ValueError("initial native request was not admitted")
        first.output_ids.append(8)
        source_indices = rows.req_to_token[first.kv.req_pool_idx, :8].clone()
        expected = [(keys[source_indices].clone(), vals[source_indices].clone()) for keys, vals in zip(
            pool.k_buffer, pool.v_buffer, strict=True,
        )]
        finish(first, preserve=True)
        send(Kind.LLM_RESULT, request_id="first")
        record = adapter.temporal.registry.records["first"]
        peer = request("peer:r", range(100, 138), "peer")
        if not native_admit(peer):
            raise ValueError("native pressure peer was not admitted")
        queued = request("queued:r", range(200, 208), "queued")
        # The fixture changes only CPU-observed output counts, never future lengths.
        peer.output_ids.append(138)
        runtime.on_batch_completed(batch([peer], ForwardMode.DECODE))
        clock.now += .01
        extra = allocator.alloc(1)
        if extra is None:
            raise ValueError("native decode fixture could not grow its actual KV")
        fill(extra, [138])
        rows.write((peer.kv.req_pool_idx, slice(38, 39)), extra)
        peer.kv.kv_allocated_len = peer.kv.kv_committed_len = 39
        runtime.on_batch_selected(batch([peer], ForwardMode.DECODE))
        with adapter._authority():
            before = adapter.capacity.probe(queued).target_tokens
            queued.sampling_params.max_new_tokens = 1_000_000
            if adapter.capacity.probe(queued).target_tokens != before:
                raise ValueError("recorded output length reached TokenCake capacity policy")
            queued.sampling_params.max_new_tokens = 1
        send(Kind.TOOL_START, tool_run_id="external", tool_family="fixture", estimated_duration_s=10.)
        adapter.tick()
        if cache.ongoing_write_through or not adapter.temporal.counts["paper_first_fit_rejected"]:
            raise ValueError("first-fit accepted work before any observed decode rate")
        peer.output_ids.append(139)
        runtime.on_batch_completed(batch([peer], ForwardMode.DECODE))
        if not 99. < adapter.temporal.decode_rate < 101.:
            raise ValueError("first-fit throughput did not use observed decode progress")
        free = allocator.available_size()
        adapter.tick()
        if (
            not record.pending_retention or not cache.ongoing_write_through
            or allocator.available_size() != free
            or adapter.temporal.last_first_fit != queued.rid
        ):
            raise ValueError("paper offload did not retain device ownership until its D2H ACK")
        snapshot = record.snapshot.snapshot
        grants, native_demand = [], 0
        if name == "early_d2h":
            send(Kind.TOOL_END, tool_run_id="external")
            if adapter.temporal.checkpoints:
                raise ValueError("tool return before backup ACK retained a predictive checkpoint")
            consumer = request("next", range(9), "agent")
            if not native_admit(consumer):
                raise ValueError("early tool return could not reuse its resident D2H source")
            poll_until(lambda: not cache.ongoing_write_through, cache.writing_check)
            if any(commit.direction == "h2d" for commit in commits):
                raise ValueError("early D2H return unnecessarily restored a resident prefix")
        else:
            poll_until(lambda: not cache.ongoing_write_through, cache.writing_check)
            path = adapter.transfers.resolve(snapshot, owner=snapshot.owner)
            if (
                any(node.component_data[0].value is not None for node in path)
                or allocator.available_size() != free + 8 or not record.retained
            ):
                raise ValueError("settled paper offload did not free exactly its private prefix")
            for keys, vals in zip(pool.k_buffer, pool.v_buffer, strict=True):
                keys[source_indices], vals[source_indices] = -777., -777.
            torch.cuda.current_stream().synchronize()
            checkpoint = adapter.temporal.checkpoints["first"]
            clock.now = checkpoint.finish_at - .2
            adapter.tick()
            if cache.ongoing_load_back:
                raise ValueError("paper upload started before its predicted lead window")
            clock.now = checkpoint.finish_at - .05
            while not grants or name == "full_upload" and sum(grants) < 8:
                free_before = allocator.available_size()
                copied_before = adapter.temporal.counts["paper_upload_tokens"]
                adapter.tick()
                copied = adapter.temporal.counts["paper_upload_tokens"] - copied_before
                if not copied or free_before - allocator.available_size() != copied:
                    raise ValueError("paper upload grant did not allocate its exact native token count")
                grants.append(copied)
                if name == "early_h2d":
                    send(Kind.TOOL_END, tool_run_id="external")
                    consumer = request("next", range(9), "agent")
                    runtime.plan_native_prefill([consumer], running_batch=None, adder=None)
                    if runtime.allow_native_prefill_candidate(consumer, running_batch=None, adder=None):
                        raise ValueError("consumer bypassed its in-flight paper H2D dependency")
                    if not checkpoint.host_leases:
                        raise ValueError("tool return lost its native upload Host source")
                poll_until(lambda: not cache.ongoing_load_back, cache.loading_check)
            if grants != ([4, 2, 1, 1] if name == "full_upload" else [4]):
                raise ValueError("paper half-deficit uploads did not follow physical grants")
            if "first" not in adapter.capacity.reservations:
                raise ValueError("paper upload has no native capacity reservation")
            if name in ("expiry", "retirement", "pressure"):
                if name == "expiry":
                    clock.now = checkpoint.expires_at + 1.
                    adapter.tick()
                elif name == "retirement":
                    send(Kind.RETURN, context_retired=True)
                elif not runtime.on_native_decode_capacity_pressure(batch([peer], ForwardMode.DECODE)):
                    raise ValueError("native decode pressure did not revoke its optional upload")
                if adapter.capacity.reservations or adapter.transfers.status(
                    snapshot, owner=snapshot.owner,
                ) in ("invalid", "missing"):
                    raise ValueError("optional upload release leaked ownership or lost cached state")
                consumer = None
            else:
                if name == "full_upload":
                    send(Kind.TOOL_END, tool_run_id="external")
                    consumer = request("next", range(9), "agent")
                before_h2d = sum(
                    dict(commit.num_tokens_by_pool).get("kv", 0)
                    for commit in commits if commit.direction == "h2d"
                )
                if not native_admit(consumer):
                    raise ValueError("actual native consumer did not take its uploaded prefix")
                native_demand = sum(
                    dict(commit.num_tokens_by_pool).get("kv", 0)
                    for commit in commits if commit.direction == "h2d"
                ) - before_h2d
                if adapter.capacity.reservations or adapter.temporal.checkpoints:
                    raise ValueError("actual native admission did not consume its same-owner reservation")
                if adapter.temporal.counts["paper_upload_consumers"] != 1:
                    raise ValueError("upload capacity was not handed to exactly one real consumer")
        if consumer is not None:
            restored = match(range(8)).device_indices
            if len(restored) != 8 or any(
                not torch.equal(keys[restored], old_keys)
                or not torch.equal(vals[restored], old_vals)
                for (keys, vals), (old_keys, old_vals) in zip(
                    zip(pool.k_buffer, pool.v_buffer, strict=True), expected, strict=True,
                )
            ):
                raise ValueError("native consumer prefix differs from its original per-layer KV bytes")
            if name == "early_h2d" and native_demand != 4:
                raise ValueError("native demand did not recover exactly the unuploaded prefix")
            if name == "full_upload" and native_demand:
                raise ValueError("fully uploaded prefix was recovered twice")
            consumer.output_ids.append(9)
            finish(consumer, preserve=True)
            send(Kind.RETURN, context_retired=False)
            if adapter.transfers.resolve(snapshot, owner=snapshot.owner) is None:
                raise ValueError("persistent invocation return deleted ordinary cached history")
        if name in ("expiry", "pressure"):
            send(Kind.TOOL_END, tool_run_id="external")
        finish(peer, preserve=False)
        session_anchors = 0
        if native_sessions:
            for session_id, generation in session_generations.items():
                if (
                    cache.session_refs._session_generations.get(session_id) != generation
                    or session_id in cache.session_refs._closed_session_ids
                ):
                    raise ValueError("paper lifecycle changed a native session generation")
            source_session = "tokencake-session-agent"
            anchors = cache.session_refs.snapshot_session_leaf_anchors(
                source_session, session_generations[source_session],
            )
            if anchors is None:
                raise ValueError("paper lifecycle lost the native cached source session")
            session_anchors = sum(len(leaves) for _, leaves in anchors)
            if not session_anchors:
                raise ValueError("paper lifecycle retained no native source ownership")
        send(Kind.WORKFLOW_END, None)
        with adapter._authority():
            adapter.capacity.refresh()
        if (
            runtime.physical_disabled or adapter.transfers.receipts.pending_count
            or cache.ongoing_load_back or cache.ongoing_write_through
            or adapter.capacity.reservations or adapter.capacity.requests
            or adapter.capacity.extents or adapter.transfers._host_leases
            or adapter.capacity.window.uncommitted != 64
        ):
            raise ValueError("native lifecycle ended with missing ACKs, pins or capacity claims")
        pending = list(cache.tree_core.root_node.children.values())
        while pending:
            node = pending.pop()
            if any(component.lock_ref or component.host_lock_ref for component in node.component_data):
                raise ValueError("native lifecycle ended with a leaked physical path lock")
            pending.extend(node.children.values())
        return {
            "status": "complete",
            "grants": grants, "native_demand_h2d_tokens": native_demand,
            "temporal_counts": dict(adapter.temporal.counts),
            "capacity_counts": dict(adapter.capacity.counts),
            "transfer_counts": dict(adapter.transfers.counts),
            "native_ack_tokens": {
                direction: sum(
                    dict(commit.num_tokens_by_pool).get("kv", 0)
                    for commit in commits if commit.direction == direction
                ) for direction in ("d2h", "h2d")
            },
            "final_uncommitted_tokens": adapter.capacity.window.uncommitted,
            "request_transport": (
                "full_prompt_native_radix_session" if native_sessions else "plain_tokens"
            ),
            "native_session_request_count": len(session_requests),
            "native_session_generations": session_generations,
            "native_session_source_anchors": session_anchors,
            "native_session_ownership_preserved": native_sessions and session_anchors > 0,
        }
    finally:
        try:
            poll_until(lambda: not cache.ongoing_write_through, cache.writing_check)
            poll_until(lambda: not cache.ongoing_load_back, cache.loading_check)
            runtime.close()
        finally:
            cache.release_host_resources()


def qualify(engine, native_patch):
    if gpu_processes():
        raise ValueError("GPU is occupied before TokenCake lifecycle qualification")
    native = git_source_state(engine, canonical_patch=native_patch)["canonical_patch"]
    if not native["matches_worktree"]:
        raise ValueError("TokenCake engine differs from its pinned native patch")
    sys.path.insert(0, str(engine / "python"))
    import torch

    if torch.cuda.device_count() != 1:
        raise ValueError("TokenCake lifecycle check requires one explicit visible GPU")
    evidence = {name: scenario(name) for name in SCENARIOS}
    session_evidence = {name: scenario(name, native_sessions=True) for name in SCENARIOS}
    if os.getpid() not in gpu_processes():
        raise ValueError("TokenCake lifecycle did not execute on its owned GPU process")
    return {
        "status": "tokencake_native_lifecycle_qualified",
        "scope": "Real two-layer native GPU/Host pools, requests and prefill admission; synthetic values and policy clock. No whole-model or performance claim.",
        "engine_tree": native["actual_tree"],
        "native_patch_sha256": sha256_file(native_patch),
        "native_engine": native,
        "gpu": torch.cuda.get_device_name(), "owned_gpu_pid": os.getpid(),
        "fixture_only_parameters": FIXTURE_PARAMETERS, "scenarios": evidence,
        "native_session_scenarios": session_evidence,
        **dict.fromkeys(REQUIRED_EVIDENCE, True),
        "whole_model_or_performance_qualified": False, "performance_accepted": False,
    }


def run(args):
    args.output_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    try:
        report = qualify(args.engine.resolve(), args.native_patch.resolve())
    except BaseException as error:
        report = {
            "status": "failed", "error_type": type(error).__name__, "error": str(error),
            "whole_model_or_performance_qualified": False, "performance_accepted": False,
        }
        raise
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        report["source_sha256"] = {
            name: sha256_file(ROOT / name) for name in QUALIFIED_SOURCES
        }
        controls.reservation.write_json(args.output_dir / "summary.json", report)
        print(json.dumps(report, indent=2, allow_nan=False))


def start(args):
    run_root = args.run_root.resolve()
    if run_root.exists():
        raise FileExistsError("preserve prior TokenCake qualification artifacts")
    reference = json.loads(args.reference.read_bytes())
    controls.fresh.check_hold(reference, released=True)
    run_root.mkdir(parents=True)
    frozen = freeze(args.engine, args.native_patch)
    registration = {
        **frozen, "run_root": str(run_root), "port": args.port,
        "hold_file": reference["hold_file"],
        "reference_environment": reference["reference_environment"],
        "after_run_root": str(args.after_run_root.resolve()) if args.after_run_root else None,
        "performance_accepted": False,
    }
    controls.reservation.write_json(run_root / "registration.json", registration)
    with (run_root / "supervisor.log").open("x") as log:
        process = subprocess.Popen([
            sys.executable, str(Path(frozen["checkout"]) / "scripts/qualify_tokencake_runtime.py"),
            "watch", "--registration", str(run_root / "registration.json"),
        ], cwd=frozen["checkout"], env={**os.environ, "PYTHONHASHSEED": "21"},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    identity = controls.reservation.process(process.pid)
    if identity is None:
        raise ValueError("TokenCake qualifier supervisor has no owned identity")
    controls.reservation.write_json(run_root / "supervisor.json", identity)
    print(json.dumps({"status": "supervisor_started", "run": str(run_root), "supervisor": identity}, indent=2))


def watch(path):
    registration = json.loads(path.read_bytes())
    root, identities, stopped = Path(registration["run_root"]), [], False
    state = {"status": "waiting_predecessor_or_resources", "performance_accepted": False}

    def stop(*_):
        nonlocal stopped
        stopped = True

    def pulse():
        if stopped:
            raise ValueError("TokenCake qualifier received a stop request")
        controls.fresh.check_hold(registration, released=True)
        state.update(checked_at=controls.reservation.now().isoformat(), owned=controls.owned_processes(identities))
        controls.reservation.write_json(root / "state.json", state)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with (root / "supervisor.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            while not predecessor_finished(registration["after_run_root"]) or not (
                controls.qualified.resources(registration["port"])["ready"]
            ):
                pulse()
                time.sleep(5.)
            pulse()
            state["status"] = "qualifying_tokencake_native_lifecycle"
            output = root / "qualification"
            env = prefix_qualification_environment(registration, os.environ)
            with (root / "qualification.log").open("x") as log:
                child = subprocess.Popen([
                    sys.executable, str(ROOT / "scripts/qualify_tokencake_runtime.py"), "run",
                    "--engine", registration["engine_checkout"],
                    "--native-patch", str(ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch"),
                    "--output-dir", str(output),
                ], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                identity = controls.reservation.process(child.pid)
                if identity is None:
                    raise ValueError("TokenCake GPU qualifier has no owned identity")
                identities.append(identity)
                deadline = time.monotonic() + 180.
                while child.poll() is None:
                    pulse()
                    if time.monotonic() >= deadline:
                        raise TimeoutError("TokenCake lifecycle qualification exceeded its bound")
                    time.sleep(.5)
                if child.wait():
                    raise ValueError("TokenCake native lifecycle qualification failed; preserve its report")
            report = json.loads((output / "summary.json").read_bytes())
            validate_report(
                report, engine_tree=registration["engine_tree"],
                patch_sha256=registration["native_patch_sha256"],
            )
            state.update(status="tokencake_native_lifecycle_qualified", qualification=report)
        except BaseException as error:
            state.update(status="failed", error_type=type(error).__name__, error=str(error))
        finally:
            try:
                controls.cleanup(root, identities, state["status"])
            except BaseException as error:
                state.update(status="failed", cleanup_error=repr(error))
            state["finished_at"] = controls.reservation.now().isoformat()
            controls.reservation.write_json(root / "summary.json", state)
            controls.reservation.write_json(root / "state.json", state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    launch = commands.add_parser("start")
    for name in ("run-root", "reference", "engine", "native-patch"):
        launch.add_argument("--" + name, required=True, type=Path)
    launch.add_argument("--port", type=int, required=True)
    launch.add_argument("--after-run-root", type=Path)
    monitor = commands.add_parser("watch")
    monitor.add_argument("--registration", required=True, type=Path)
    direct = commands.add_parser("run")
    for name in ("engine", "native-patch", "output-dir"):
        direct.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    if args.command == "start":
        start(args)
    elif args.command == "watch":
        watch(args.registration)
    else:
        run(args)


if __name__ == "__main__":
    main()
