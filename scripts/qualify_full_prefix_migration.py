#!/usr/bin/env python3
"""Qualify Full prefix migration with small native GPU/Host pools and requests."""

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
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import (
    EventConfidence, ExecutionMode, RelationType, RuntimeEvent, RuntimeEventKind as Kind,
)
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.qualify_paper_prefix_transfers import gpu_processes, poll_until
from scripts import run_causal_comparison as controls
from scripts.run_agentx_baseline import freeze, predecessor_finished, prefix_qualification_environment


SOURCES = (
    "scripts/qualify_full_prefix_migration.py",
    "beliefkv/runtime/full_prefix_migration.py",
    "beliefkv/runtime/paper_prefix_transfers.py",
    "beliefkv/runtime/sglang_v0520_runtime.py",
    "beliefkv/runtime/sglang_v0520_physical.py",
    "beliefkv/runtime/agent_working_set.py",
    "beliefkv/policy/thunder_agent_migration.py",
    "beliefkv/policy/paper_baselines.py",
    "beliefkv/experiments/internal_comparison.py",
    "beliefkv/core/events.py",
    "scripts/run_agentx_baseline.py",
    "scripts/run_causal_comparison.py",
)
SCENARIOS = (
    "incremental", "shared_ready", "early_d2h", "confirmed_join",
    "controlled_eos", "length_stop", "busy_producer", "host_pressure", "native_reserve",
    "inferred_join",
)
EXPECTED_ACKS = {
    "incremental": (12, 0), "shared_ready": (16, 0), "early_d2h": (8, 0),
    "confirmed_join": (8, 8), "controlled_eos": (8, 8), "length_stop": (8, 0),
    "busy_producer": (8, 8), "host_pressure": (12, 0), "native_reserve": (8, 8),
    "inferred_join": (0, 0),
}
PROPERTIES = (
    "real_native_requests_and_prefill_admission",
    "full_runtime_routes_native_receipts",
    "background_spawn_keeps_parent_executable",
    "incremental_backup_copies_only_growth",
    "bounded_backup_splits_actual_native_extents",
    "device_release_requires_actual_d2h_ack",
    "unstarted_shared_owner_prevents_prefix_demotion",
    "all_restored_layer_key_and_value_bytes_preserved",
    "freed_device_pages_poisoned_before_restore",
    "same_agent_native_admission_consumes_restore_reservation",
    "length_stop_does_not_supply_semantic_eos",
    "inferred_join_does_not_authorize_native_actions",
    "early_return_reuses_resident_d2h_source",
    "busy_native_producer_defers_restore_without_allocation",
    "real_host_capacity_refusal_preserves_existing_backups",
    "actual_admitted_unallocated_input_reserve_precedes_speculation",
    "all_native_rows_locks_and_receipts_released",
)


@contextmanager
def full_environment():
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_retained",
        "PREFILL_SCHEDULING_POLICY": "join_priority",
        "TOOL_RESTORE_MODE": "execution", "SESSIONLESS_PREFIX_MIGRATION": "1",
    }, "full")
    previous = {name: os.environ.get(name) for name in settings}
    try:
        os.environ.update(settings)
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class NativeFullFixture:
    """Synthetic KV bytes and controlled events; no model forward is claimed."""

    size = 128
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

        self.torch = torch
        settings = ServerArgs(
            model_path="dummy", page_size=1, hicache_ratio=2.,
            hicache_io_backend="kernel", hicache_mem_layout="page_first",
            hicache_write_policy="write_back",
        )
        set_global_server_args_for_scheduler(settings)
        self.pool = MHATokenToKVPool(
            size=self.size, page_size=1, head_num=2, head_dim=64,
            dtype=torch.bfloat16, layer_num=2, device="cuda", enable_memory_saver=False,
        )
        self.rows = ReqToTokenPool(
            size=8, max_context_len=self.context_limit, device="cuda", enable_memory_saver=False,
        )
        self.allocator = TokenToKVPoolAllocator(
            size=self.size, dtype=torch.bfloat16, device="cuda",
            kvcache=self.pool, need_sort=False,
        )
        params = CacheInitParams(
            disable=False, req_to_token_pool=self.rows, token_to_kv_pool_allocator=self.allocator,
            page_size=1, tree_components=(ComponentType.FULL,), enable_session_radix_cache=True,
        )
        self.cache = UnifiedRadixCache(params)
        self.cache.init_hicache(settings, params)
        self.cache.load_back_threshold = 1
        self.socket_dir = tempfile.TemporaryDirectory(prefix="full-prefix-")
        with full_environment():
            self.runtime = NativeAdmissionRuntime(
                event_socket_path=str(Path(self.socket_dir.name) / "control.sock"),
            )
        self.runtime.attach_native_cache(self.cache)
        self.actor = self.runtime._full_prefix_migration
        # Scale only the component fixture; formal serving retains its buffer.
        self.runtime._agent_admission.policy.buffer_tokens = 1
        self.runtime._native_plain_output_template = True
        self.sequence, self.live, self.registered = 0, {}, {}
        self.commits, self.issued_batches, self.poisoned_tokens = [], [], 0
        self.restore_consumers = 0
        self.host_blocker = self.device_blocker = None
        self.cache.on_hicache_transfer_commit = self.acknowledged
        self.send(Kind.WORKFLOW_START)

    def acknowledged(self, commit):
        self.commits.append(commit)
        self.runtime.on_native_transfer_commit(commit)

    def sync(self):
        self.torch.cuda.current_stream().synchronize()

    def send(self, kind, *, invocation=None, context=None, **kwargs):
        self.sequence += 1
        event = RuntimeEvent(
            f"full-prefix-fixture:{self.sequence}", time.monotonic() * 1000.,
            kind, "workflow", invocation_id=invocation, context_id=context or invocation,
            **kwargs,
        )
        self.runtime.on_events((event,))

    def create(self, identity, *, parent=None):
        self.send(
            Kind.INVOCATION_CREATE, invocation=identity,
            parent_invocation_id=parent, context_epoch=0,
            agent_definition_id="fixture", agent_instance_id=identity,
            relation_type=RelationType.SPAWN if parent else RelationType.ROOT,
            execution_mode=ExecutionMode.BACKGROUND if parent else ExecutionMode.FOREGROUND,
        )

    def spawn(self, child="child"):
        if child not in self.runtime.graph.invocations:
            self.create(child, parent="parent")
        self.send(
            Kind.SPAWN, invocation="parent", target_invocation_id=child,
            execution_mode=ExecutionMode.BACKGROUND,
        )

    def join(self, *, inferred=False):
        if "child" not in self.runtime.graph.invocations:
            self.create("child", parent="parent")
        confidence = EventConfidence.INFERRED if inferred else EventConfidence.OBSERVED_EXACT
        self.send(
            Kind.JOIN_CREATE, join_id="join", member_invocation_ids=("child",),
            confidence=confidence,
        )
        self.send(
            Kind.JOIN_WAIT, invocation="parent", join_id="join", confidence=confidence,
        )

    def request(self, identity, tokens):
        from sglang.srt.managers.schedule_batch import Req
        from sglang.srt.sampling.sampling_params import SamplingParams

        if identity not in self.runtime.graph.invocations:
            self.create(identity)
        rid = identity + ":" + str(self.sequence)
        self.send(
            Kind.LLM_SUBMIT, invocation=identity, context_epoch=0,
            attributes={"request_id": rid},
        )
        parent = self.runtime.graph.invocations[identity].parent_invocation_id
        req = Req(
            rid=rid, origin_input_text="", origin_input_ids=array("q", tokens),
            sampling_params=SamplingParams(temperature=1., top_k=1, max_new_tokens=1),
            extra_key="workflow", cache_salt="workflow",
            beliefkv_metadata={
                "root_workflow_id": "workflow", "invocation_id": identity,
                "context_id": identity, "context_epoch": 0, "parent_invocation_id": parent,
            },
        )
        req.tokenizer = SimpleNamespace(decode=lambda *_args, **_kwargs: "completed answer")
        if not self.runtime.register_visible_request(req):
            raise ValueError("Full component request was not registered")
        self.registered[req.rid] = req
        return req

    def batch(self, reqs):
        from sglang.srt.managers.schedule_batch import ScheduleBatch
        from sglang.srt.model_executor.forward_batch_info import ForwardMode

        return ScheduleBatch(reqs=list(reqs), batch_is_full=False, forward_mode=ForwardMode.EXTEND)

    def write(self, indices, tokens):
        torch = self.torch
        values = torch.tensor(tokens, dtype=torch.float32, device=indices.device)[:, None, None]
        shape = self.pool.k_buffer[0].shape[1:]
        feature = torch.arange(
            shape[0] * shape[1], device=indices.device, dtype=torch.float32,
        ).reshape(1, *shape) / 512.
        for layer, (keys, vals) in enumerate(zip(self.pool.k_buffer, self.pool.v_buffer, strict=True)):
            keys[indices] = (values + feature + layer / 8.).to(keys.dtype)
            vals[indices] = (values + feature + layer / 8. + 4.).to(vals.dtype)

    def match(self, tokens):
        from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
        from sglang.srt.mem_cache.radix_cache import RadixKey

        return self.cache.match_prefix(MatchPrefixParams(key=RadixKey(
            array("q", tokens), "workflow", cache_salt="workflow",
        ), cow_mamba=False))

    def admit(self, req, *, chunk=64):
        from sglang.srt.managers.schedule_policy import PrefillAdder

        restore_owner = self.runtime._agent_admission.policy.restore_owner
        plan = self.runtime.plan_native_prefill([req], running_batch=None, adder=None)
        chosen = select_native_prefill_candidates(
            [req], plan=plan, current_semantic_revision=self.runtime.semantic_revision,
        ).candidates
        if chosen != (req,) or not self.runtime.allow_native_prefill_candidate(
            req, running_batch=None, adder=None,
        ):
            raise ValueError("native Full fixture request was not eligible")
        req.init_next_round_input(self.cache, cow_mamba=False)
        adder = PrefillAdder(
            1, self.cache, self.allocator, None, 0., self.context_limit,
            chunk, max_running_requests=8,
        )
        result = adder.add_one_req(req, has_chunked_req=False, truncation_align_size=None)
        admitted = req in adder.can_run_list
        self.runtime.on_prefill_candidate_result(req, admitted=admitted, result=result.name)
        if not admitted:
            raise ValueError("native Full fixture prefill adder declined its request")
        if (
            restore_owner == req.beliefkv_metadata["invocation_id"]
            and self.runtime._agent_admission.policy.restore_owner is None
        ):
            self.restore_consumers += 1
        if self.cache.cache_controller.load_queue:
            self.cache.cache_controller.start_loading()
        self.settle_loads()
        if self.rows.alloc([req]) is None:
            raise ValueError("native Full fixture has no request row")
        count = req.extend_range.length
        private = self.allocator.alloc(count)
        if private is None:
            raise ValueError("native Full fixture has no private token slots")
        self.write(private, req.get_fill_ids()[len(req.prefix_indices):])
        indices = self.torch.cat((req.prefix_indices, private))
        self.rows.write((req.kv.req_pool_idx, slice(0, len(indices))), indices)
        req.kv.kv_allocated_len = req.kv.kv_committed_len = len(indices)
        req.kv.cache_protected_len = len(req.prefix_indices)
        self.live[req.rid] = req
        self.runtime.on_batch_selected(self.batch([req]))
        self.sync()
        return indices.clone()

    def finish(self, req, *, eos=False, preserve=True):
        from sglang.srt.managers.schedule_batch import FINISH_LENGTH, FINISH_MATCHED_TOKEN

        if req.kv.kv_committed_len != len(req.origin_input_ids):
            raise ValueError("partial fixture requests must cancel, not fake completion")
        req.output_ids.append(99)
        req.finished_reason = FINISH_MATCHED_TOKEN(99) if eos else FINISH_LENGTH(1)
        self.sync()
        self.cache.cache_finished_req(
            req, is_insert=preserve, kv_len_to_handle=req.kv.kv_committed_len,
        )
        self.rows.free(req)
        self.live.pop(req.rid)
        self.registered.pop(req.rid)
        if self.runtime.on_batch_completed(self.batch([req])) != (req.rid,):
            raise ValueError("native Full fixture lost its completion callback")
        self.send(
            Kind.LLM_RESULT, invocation=req.beliefkv_metadata["invocation_id"],
            attributes={"request_id": req.rid},
        )

    def abort(self, req):
        from sglang.srt.managers.schedule_batch import FINISH_ABORT

        if req.rid in self.live:
            self.sync()
            req.finished_reason = FINISH_ABORT("component fixture cancellation")
            self.cache.cache_finished_req(
                req, is_insert=False, kv_len_to_handle=req.kv.kv_committed_len,
            )
            self.rows.free(req)
            self.live.pop(req.rid)
        self.registered.pop(req.rid, None)
        self.runtime.on_abort_request(SimpleNamespace(rid=req.rid, abort_all=False))

    def served(self, identity, tokens):
        req = self.request(identity, tokens)
        indices = self.admit(req)
        expected = self.saved(indices)
        self.finish(req)
        return expected

    def saved(self, indices):
        return [(keys[indices].clone(), vals[indices].clone()) for keys, vals in zip(
            self.pool.k_buffer, self.pool.v_buffer, strict=True,
        )]

    def require_bytes(self, tokens, expected):
        indices = self.match(tokens).device_indices
        if len(indices) != len(tokens) or any(
            not self.torch.equal(keys[indices], old_keys)
            or not self.torch.equal(vals[indices], old_vals)
            for (keys, vals), (old_keys, old_vals) in zip(
                zip(self.pool.k_buffer, self.pool.v_buffer, strict=True), expected, strict=True,
            )
        ):
            raise ValueError("native Full restored K/V differs from its original per-layer bytes")

    def settle_writes(self):
        poll_until(lambda: not self.cache.ongoing_write_through, self.cache.writing_check)

    def settle_loads(self):
        poll_until(lambda: not self.cache.ongoing_load_back, self.cache.loading_check)

    def dispatch(self):
        before = self.actor.counts["d2h_enqueued_tokens"]
        self.runtime.dispatch_after_forward(running_batch=self.batch(self.live.values()))
        copied = self.actor.counts["d2h_enqueued_tokens"] - before
        if copied:
            self.issued_batches.append(copied)

    def offload_parent(self):
        expected = self.served("parent", range(8))
        self.join()
        before = self.allocator.available_size()
        self.dispatch()
        if not self.cache.ongoing_write_through or self.allocator.available_size() != before:
            raise ValueError("Full released Device capacity before actual native D2H ACK")
        self.settle_writes()
        self.dispatch()
        if self.allocator.available_size() != before + 8:
            raise ValueError("Full did not release exactly the settled parent prefix")
        self.poison_free_pages()
        return expected

    def poison_free_pages(self):
        count = self.allocator.available_size()
        indices = self.allocator.alloc(count)
        if indices is None:
            raise ValueError("Full fixture cannot poison all released Device slots")
        for keys, vals in zip(self.pool.k_buffer, self.pool.v_buffer, strict=True):
            keys[indices], vals[indices] = -777., -777.
        self.sync()
        self.allocator.free(indices)
        self.poisoned_tokens += count

    def ack_tokens(self):
        return {
            direction: sum(
                dict(commit.num_tokens_by_pool).get("kv", 0)
                for commit in self.commits if commit.direction == direction
            ) for direction in ("d2h", "h2d")
        }

    @contextmanager
    def busy_producer(self):
        counter = self.cache.cache_controller.layer_done_counter
        event = counter.events[(counter.producer_index + 2) % counter.num_counters].finish_event
        stream = self.torch.cuda.Stream()
        with self.torch.cuda.stream(stream):
            self.torch.cuda._sleep(100_000_000)
            event.record(stream)
        if event.query():
            raise ValueError("native Full fixture did not create real producer backpressure")
        try:
            yield stream.synchronize
        finally:
            stream.synchronize()

    def finish_scenario(self):
        from sglang.srt.mem_cache.base_prefix_cache import EvictParams

        for req in tuple(self.registered.values()):
            self.abort(req)
        self.settle_writes()
        self.settle_loads()
        self.send(Kind.WORKFLOW_END)
        self.sync()
        if self.host_blocker is not None:
            self.cache.host_pool_group.anchor_entry.host_pool.free(self.host_blocker)
            self.host_blocker = None
        if self.device_blocker is not None:
            self.allocator.free(self.device_blocker)
            self.device_blocker = None
        result = {
            "native_ack_tokens": self.ack_tokens(), "d2h_batches": list(self.issued_batches),
            "poisoned_device_tokens": self.poisoned_tokens,
            "counts": dict(self.actor.counts), "transfer_counts": dict(self.actor.transfers.counts),
            "native_restore_consumers": self.restore_consumers,
        }
        self.cache.evict(EvictParams(num_tokens=self.size))
        self.settle_writes()
        self.settle_loads()
        self.cache.evict(EvictParams(num_tokens=self.size))
        pending = list(self.cache.tree_core.root_node.children.values())
        while pending:
            node = pending.pop()
            if any(data.lock_ref or data.host_lock_ref for data in node.component_data):
                raise ValueError("native Full component leaked a GPU or Host path lock")
            pending.extend(node.children.values())
        if (
            self.runtime.physical_disabled or self.actor.transfers.receipts.pending_count
            or self.live or self.registered or self.actor.requests or self.actor.contexts
            or self.allocator.available_size() != self.size
            or self.rows.available_size() != self.rows.size
            or self.cache.session_refs._session_generations
        ):
            raise ValueError("native Full component ended with lost ACKs, capacity or fake sessions")
        return {**result, "status": "complete", "final_device_free_tokens": self.allocator.available_size()}

    def close(self):
        try:
            for req in tuple(self.registered.values()):
                self.abort(req)
            self.settle_writes()
            self.settle_loads()
            self.runtime.close()
        finally:
            self.cache.release_host_resources()
            self.socket_dir.cleanup()


def scenario(fixture, name):
    f = fixture
    if name == "incremental":
        f.served("parent", range(8))
        f.spawn()
        f.actor.spawn_batch_bytes = 4 * f.cache.host_pool_group.anchor_entry.host_pool.size_per_token
        for _ in range(2):
            before = f.allocator.available_size()
            f.dispatch()
            if f.allocator.available_size() != before:
                raise ValueError("background SPAWN released an executable parent's Device state")
            f.settle_writes()
        f.served("parent", range(12))
        f.spawn("other")
        f.dispatch()
        f.settle_writes()
        if f.issued_batches != [4, 4, 4]:
            raise ValueError("native bounded backup repeated an old prefix or exceeded its grant")
    elif name == "shared_ready":
        expected = f.served("parent", range(16))
        f.spawn()
        peer = f.request("child", (*range(8), *range(100, 108)))
        f.join()
        before = f.allocator.available_size()
        f.dispatch()
        if f.allocator.available_size() != before:
            raise ValueError("shared prefix was released before native backup ACK")
        f.settle_writes()
        f.dispatch()
        if f.allocator.available_size() != before + 8:
            raise ValueError("unstarted peer's observed shared prefix was proactively demoted")
        f.require_bytes(range(8), [(key[:8], value[:8]) for key, value in expected])
        f.abort(peer)
    elif name == "host_pressure":
        f.served("parent", range(8))
        f.spawn()
        f.dispatch()
        f.settle_writes()
        old = f.actor.contexts["parent"]
        old_path = f.actor.transfers.resolve(old.snapshot, owner=old.owner)
        old_host = [node.component_data[0].host_value.clone() for node in old_path]
        host = f.cache.host_pool_group.anchor_entry.host_pool
        f.host_blocker = host.alloc(host.available_size())
        if f.host_blocker is None:
            raise ValueError("native Full fixture could not exhaust real Host capacity")
        f.served("parent", range(12))
        f.spawn("other")
        before = f.allocator.available_size()
        f.dispatch()
        if f.actor.counts["d2h_enqueued_tokens"] != 8 or f.allocator.available_size() != before:
            raise ValueError("Host refusal repeated old D2H or released unbacked Device state")
        host.free(f.host_blocker[:4])
        f.host_blocker = f.host_blocker[4:]
        f.actor.next_tick = 0.
        f.dispatch()
        f.settle_writes()
        if any(
            not f.torch.equal(node.component_data[0].host_value, original)
            for node, original in zip(old_path, old_host, strict=True)
        ):
            raise ValueError("bounded incremental backup evicted its existing Host prefix")
    elif name == "inferred_join":
        f.served("parent", range(8))
        f.join(inferred=True)
        before = f.allocator.available_size()
        f.dispatch()
        f.send(Kind.JOIN_SATISFIED, join_id="join", confidence=EventConfidence.INFERRED)
        f.dispatch()
        if (
            f.allocator.available_size() != before
            or f.cache.ongoing_write_through or f.cache.ongoing_load_back or f.actor.restores
            or f.runtime.graph.invocations["parent"].state.value != "ready"
        ):
            raise ValueError("inferred JOIN authorized native Full actions or parent blocking")
    else:
        expected = f.offload_parent() if name != "early_d2h" else None
        if name == "early_d2h":
            f.served("parent", range(8))
            f.join()
            f.dispatch()
            f.send(Kind.RETURN, invocation="child", attributes={"context_retired": False})
            consumer = f.request("parent", range(9))
            f.admit(consumer)
            f.settle_writes()
            if f.ack_tokens()["h2d"]:
                raise ValueError("early D2H return restored an already resident source")
            f.finish(consumer)
        else:
            child = f.request("child", range(30, 34))
            f.admit(child)
            f.finish(child, eos=name == "controlled_eos")
            if name == "length_stop":
                f.dispatch()
                if f.actor.restores or f.cache.ongoing_load_back:
                    raise ValueError("fixed-length completion became a semantic EOS trigger")
            else:
                if name != "controlled_eos":
                    f.send(Kind.RETURN, invocation="child", attributes={"context_retired": False})
                elif f.runtime.graph.joins["join"].satisfied or not f.actor.restores:
                    raise ValueError("controlled EOS did not start recovery before child RETURN")
                if name == "busy_producer":
                    with f.busy_producer() as release:
                        before = f.allocator.available_size()
                        f.dispatch()
                        if f.cache.ongoing_load_back or f.allocator.available_size() != before:
                            raise ValueError("busy producer consumed native restore slots")
                        release()
                    f.actor.next_restore = 0.
                elif name == "native_reserve":
                    peer = f.request("peer", range(100, 132))
                    f.admit(peer)
                    large = f.request("large", range(200, 264))
                    f.admit(large, chunk=8)
                    f.device_blocker = f.allocator.alloc(32)
                    if f.device_blocker is None:
                        raise ValueError("native reserve fixture could not hold its extra Device slots")
                    f.dispatch()
                    if f.cache.ongoing_load_back or not f.actor.counts["restore_native_capacity_deferred"]:
                        raise ValueError("speculation consumed actual admitted unallocated input capacity")
                    f.allocator.free(f.device_blocker)
                    f.device_blocker = None
                    f.abort(large)
                    f.actor.next_restore = 0.
                f.dispatch()
                f.settle_loads()
                if f.actor.counts["h2d_enqueued_tokens"] != 8:
                    raise ValueError("Full recovery did not transfer the actual missing parent prefix")
                f.require_bytes(range(8), expected)
                if name == "controlled_eos":
                    f.send(Kind.RETURN, invocation="child", attributes={"context_retired": False})
                consumer = f.request("parent", range(9))
                f.admit(consumer)
                if f.runtime._agent_admission.policy.restore_owner is not None:
                    raise ValueError("actual same-agent admission did not consume its restore reservation")
                f.finish(consumer)
    return f.finish_scenario()


def validate_report(report, *, engine_tree, patch_sha256, root=ROOT):
    rows = report.get("scenarios", {})
    if (
        report.get("status") != "full_native_prefix_components_qualified"
        or report.get("native_engine", {}).get("actual_tree") != engine_tree
        or report.get("native_patch_sha256") != patch_sha256
        or report.get("device_tokens") != 128 or report.get("host_tokens") != 257
        or report.get("layers") != 2
        or report.get("fixture_only_agent_buffer_tokens") != 1
        or set(rows) != set(SCENARIOS)
        or any(
            row.get("status") != "complete" or row.get("final_device_free_tokens") != 128
            or row.get("native_ack_tokens") != dict(zip(("d2h", "h2d"), EXPECTED_ACKS[name]))
            or EXPECTED_ACKS[name][1] and row.get("poisoned_device_tokens", 0) <= 0
            or row.get("native_restore_consumers") != int(EXPECTED_ACKS[name][1] > 0)
            for name, row in rows.items()
        )
        or rows.get("incremental", {}).get("d2h_batches") != [4, 4, 4]
        or any(report.get(name) is not True for name in PROPERTIES)
        or any(report.get(name) is not False for name in (
            "whole_model_qualified", "model_numerical_equivalence_verified", "performance_accepted",
        ))
        or report.get("source_sha256") != {name: sha256_file(root / name) for name in SOURCES}
    ):
        raise ValueError("Full native prefix evidence is incomplete, stale or overclaimed")


def qualify(engine, patch):
    if gpu_processes():
        raise ValueError("GPU is occupied before Full native prefix qualification")
    native = git_source_state(engine, canonical_patch=patch)["canonical_patch"]
    if not native["matches_worktree"]:
        raise ValueError("Full component engine differs from its canonical patch")
    sys.path.insert(0, str(engine / "python"))
    import torch

    if torch.cuda.device_count() != 1:
        raise ValueError("Full prefix qualification requires one explicit visible GPU")
    rows, dimensions = {}, None
    for name in SCENARIOS:
        fixture = NativeFullFixture(engine)
        try:
            actual = {
                "device_tokens": fixture.allocator.size,
                "host_tokens": fixture.cache.host_pool_group.anchor_entry.host_pool.size,
                "layers": len(fixture.pool.k_buffer),
            }
            if actual != {"device_tokens": 128, "host_tokens": 257, "layers": 2}:
                raise ValueError("Full native fixture actual pool dimensions differ")
            dimensions = actual
            rows[name] = scenario(fixture, name)
        finally:
            fixture.close()

    if os.getpid() not in gpu_processes():
        raise ValueError("Full prefix qualification did not execute on its owned GPU")
    return {
        "status": "full_native_prefix_components_qualified",
        "scope": "Real native GPU/Host pools and request admission; controlled KV values, stop events and fixture buffer. No model forward or performance claim.",
        "native_engine": native, "native_patch_sha256": sha256_file(patch),
        **dimensions, "fixture_only_agent_buffer_tokens": 1,
        "owned_gpu_pid": os.getpid(), "gpu": torch.cuda.get_device_name(),
        "scenarios": rows, **dict.fromkeys(PROPERTIES, True),
        "whole_model_qualified": False, "model_numerical_equivalence_verified": False,
        "performance_accepted": False,
    }


def run(args):
    args.output_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    try:
        report = qualify(args.engine_checkout.resolve(), args.canonical_patch.resolve())
    except BaseException as error:
        report = {
            "status": "failed", "error_type": type(error).__name__,
            "error": str(error), "performance_accepted": False,
        }
        raise
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        report["source_sha256"] = {name: sha256_file(ROOT / name) for name in SOURCES}
        with (args.output_dir / "summary.json").open("x") as output:
            json.dump(report, output, indent=2, allow_nan=False)
            output.write("\n")


def check_registration(registration):
    if registration["source_sha256"] != {
        name: sha256_file(ROOT / name) for name in SOURCES
    }:
        raise ValueError("Full component sources differ from their frozen registration")
    predecessor = Path(registration["after_run_root"])
    if (
        sha256_file(predecessor / "registration.json")
        != registration["predecessor_registration_sha256"]
        or json.loads((predecessor / "supervisor.json").read_bytes())
        != registration["predecessor_supervisor"]
    ):
        raise ValueError("Full component predecessor registration or process identity changed")


def start(args):
    root = args.run_root.resolve()
    if root.exists():
        raise FileExistsError("preserve earlier Full component qualification artifacts")
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
        "scope": "full_native_prefix_components_not_model_or_performance",
        "performance_accepted": False,
    }
    controls.reservation.write_json(root / "registration.json", registration)
    with (root / "supervisor.log").open("x") as log:
        process = subprocess.Popen([
            sys.executable, str(checkout / "scripts/qualify_full_prefix_migration.py"),
            "watch", "--registration", str(root / "registration.json"),
        ], cwd=checkout, env={**os.environ, "PYTHONHASHSEED": "21"},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True)
    supervisor = controls.reservation.process(process.pid)
    if supervisor is None:
        raise ValueError("Full component supervisor has no owned process identity")
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
            raise ValueError("Full component supervisor received a stop request")
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
            state["status"] = "qualifying_full_native_prefix_components"
            output = root / "qualification"
            with (root / "qualification.log").open("x") as log:
                child = subprocess.Popen([
                    sys.executable, str(ROOT / "scripts/qualify_full_prefix_migration.py"), "run",
                    "--engine-checkout", registration["engine_checkout"],
                    "--canonical-patch", str(ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch"),
                    "--output-dir", str(output),
                ], cwd=ROOT, env=prefix_qualification_environment(registration, os.environ),
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True)
                identity = controls.reservation.process(child.pid)
                if identity is None:
                    raise ValueError("Full native GPU qualifier has no owned process identity")
                owned.append(identity)
                deadline = time.monotonic() + 600.
                while child.poll() is None:
                    pulse()
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Full native prefix component qualification expired")
                    time.sleep(.5)
                if child.wait():
                    raise ValueError("Full native prefix component qualification failed; retain evidence")
            report = json.loads((output / "summary.json").read_bytes())
            validate_report(
                report, engine_tree=registration["engine_tree"],
                patch_sha256=registration["native_patch_sha256"],
            )
            state.update(
                status="full_native_prefix_components_qualified", qualification=report,
                whole_model_qualified=False, performance_accepted=False,
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
