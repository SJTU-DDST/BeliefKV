#!/usr/bin/env python3
"""Measure complete JOIN/tool prefetch planning and registration on a CPU."""

from __future__ import annotations

import argparse
from collections import Counter, deque
from contextlib import nullcontext
import cProfile
from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import pstats
import subprocess
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime.native_transfer_service import NativeServiceSample
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime, fixture
from scripts.benchmark_prefetch_control_cpu import distribution


def dispatch_fixture(
    cls, *, source, depth, diagnostics=False, native_budget=False, tail_reclaim=False,
    workflows=1, running=48, sparse_session_leaves=False, numpy_created_times=False,
):
    runtime, _, reads, _, rows = fixture(
        cls, workflows=workflows, depth=depth + 1, backed=True, host_full=1_000_000,
        leases=0, host_only=True,
    )
    names = cls._wait_prefetch_opportunity.__globals__
    cache = runtime._native_cache
    if sparse_session_leaves or numpy_created_times:
        from numpy import float64

        for node_id in range(workflows * (depth + 1)):
            node = cache.tree_core.node_by_id(node_id)
            offset = node_id % (depth + 1)
            if sparse_session_leaves:
                full, mamba = node.component_data[0], node.component_data[2]
                full.session_ref = int(offset != 0)
                mamba.session_ref = int(offset == depth)
                if offset != depth:
                    full.session_ids = mamba.session_ids = None
            if numpy_created_times:
                node.creation_time = float64(node.creation_time)
    snapshot = cache.session_refs.snapshot_session_leaf_anchors

    def live_snapshot(session, generation, *, max_leaves):
        if (
            cache.session_refs._session_generations.get(session) != generation
            or session in cache.session_refs._closed_session_ids
        ):
            return None
        return snapshot(session, generation, max_leaves=max_leaves)

    cache.session_refs.snapshot_session_leaf_anchors = live_snapshot
    cache.session_refs.snapshot_latest_session_leaf_anchors = live_snapshot
    cache.token_to_kv_pool_allocator.available_size = lambda: 2_000_000
    cache.req_to_token_pool.mamba_allocator.available_size = lambda: 1024
    if native_budget:
        cache.req_to_token_pool.available_size = lambda: 16
        cache.full_evictable_size = lambda: 0
        cache.mamba_evictable_size = lambda: 0
        runtime._observe_native_admission_capacity(
            running_batch=NS(reqs=[NS(rid=f"busy-{index}") for index in range(running)]),
            adder=NS(
                max_running_requests=running, max_prefill_bs=4,
                page_size=1, rem_input_tokens=256,
            ),
        )
    key = runtime.context_sessions["ctx-wf-0-root"]
    parent = runtime.graph.invocations[key.invocation_id]
    join = runtime.graph.joins[parent.join_id]
    fixed_now = time.monotonic()
    full_unit = cache.host_pool_group.entry_map["kv"].host_pool.size_per_token
    mamba_unit = cache.host_pool_group.entry_map["mamba"].host_pool.size_per_token
    size = depth * 128 * full_unit + mamba_unit
    runtime._native_service_samples.extend(
        NativeServiceSample(size, 10., shape="hybrid") for _ in range(3)
    )
    if depth > runtime.physical_ledger.max_pending:
        # A bounded prefix may stop before its Mamba checkpoint.
        full_size = runtime.physical_ledger.max_pending * 128 * full_unit
        runtime._native_service_samples.extend(
            NativeServiceSample(full_size, 10., shape="full") for _ in range(3)
        )
    runtime.predictor_sha256 = "a" * 64
    runtime._hotpath_timing.enabled = False
    runtime.enable_h2d_opportunity_sampling = False
    runtime.prefetch_lead_ms = 1000.
    if not diagnostics:
        runtime._opportunity_writer = None
    actions = []
    controller = cache.cache_controller
    controller.mem_pool_host = cache.host_pool_group
    controller._num_tokens_by_pool = lambda operation: {
        "kv": len(operation.host_indices),
        **({"mamba": 1} if operation.pool_transfers else {}),
    }
    controller._transfer_num_bytes = lambda operation: (
        len(operation.host_indices) * full_unit
        + (mamba_unit if operation.pool_transfers else 0)
    )

    def native_burst(**kwargs):
        outcomes = []
        for node_id, created, state, command in kwargs["nodes"]:
            node = cache.tree_core.node_by_id(node_id)
            full = node.component_data[0]
            host_indices = full.host_value if full.value is None else ()
            operation = NS(
                beliefkv_command_id=command, node_ids=[node_id],
                host_indices=host_indices, device_indices=host_indices,
                pool_transfers=[NS(
                    name="mamba", indices_from_pool=None,
                    host_indices=(1,), device_indices=(2,),
                )] if state and node.component_data[2].value is None else None,
            )
            if not kwargs["beliefkv_before_enqueue"](operation):
                break
            actions.append((node_id, created, bool(operation.pool_transfers)))
            outcomes.append(NS(issued=True, node_id=node_id, load_started=True))
        return tuple(outcomes)

    cache.prefetch_gpu_session_nodes = native_burst
    if tail_reclaim:
        def reclaim(**kwargs):
            reads["native_tail_reclaim"] += 1
            return {}

        cache.reclaim_beliefkv_handoff_capacity = reclaim
    if source == "tool_wait":
        runtime.enable_tool_prefetch = True
        parent.state = InvocationState.WAIT_TOOL
        parent.active_tool_calls = {"long": ("shell", parent.updated_ts_ms)}
        parent.active_tool_family = "shell"
        parent.active_tool_start_ms = parent.updated_ts_ms
        hint = names["NativeToolWaitHint"](
            key, 100., 300., 900., fixed_now * 1000., fixed_now * 1000. + 3600_000.,
            runtime.predictor_sha256, parent.updated_ts_ms,
        )
        runtime.tool_wait_hints[key.context_id] = hint
        dispatch = runtime.dispatch_tool_prefetch
    else:
        runtime.enable_final_stage_prefetch = True
        parent.state = InvocationState.READY
        join.completed_member_ids = set(join.member_invocation_ids)
        join.satisfied = True
        dispatch = runtime.dispatch_join_prefetch

    def reset():
        runtime.physical_ledger = names["PhysicalTransactionLedger"]()
        runtime._prefetch_steps.clear()
        runtime._prefetch_issue_budgets.clear()
        runtime._prefetch_issue_times.clear()
        runtime._join_prefetch_issued.clear()
        runtime._tool_opportunity_cache.clear()
        runtime._tool_prefetch_next_ms = 0.
        runtime._tool_ticket = None
        runtime._join_ticket = (
            names["_JoinPrefetchTicket"](
                key, join.join_id, join.mode.value, tuple(sorted(join.member_invocation_ids)),
                "confirmed", fixed_now + 3600.,
            ) if source == "join_ticket" else None
        )
        runtime._confirmed_join_tickets.clear()
        runtime._wait_prefetch_records.clear()
        runtime.counts.clear()
        actions.clear()
        rows.clear()
        reads.clear()

    reset()
    return runtime, key, cache, reads, actions, rows, reset, dispatch, fixed_now


def state_signature(runtime, actions):
    pending = []
    for step, source, _ in runtime._prefetch_steps.values():
        pending.append({
            "node": step.node_id, "created": step.creation_time,
            "include_mamba": step.include_mamba, "source": source,
        })
    return {
        "actions": list(actions), "pending_steps": pending,
        "pending_count": runtime.physical_ledger.pending_count,
        "pending_bytes": runtime.physical_ledger.pending_action_bytes("PREFETCH_GPU"),
        "residency_budget": asdict(runtime._residency_budget),
        "physical_disabled": runtime.physical_disabled,
    }


def selection_fixture(cls, *, depth, remaining_ms, report_service=False):
    runtime, key, _, reads, actions, _, reset, _, now = dispatch_fixture(
        cls, source="join_ticket", depth=depth, native_budget=True,
    )
    names = cls._roll_final_stage.__globals__
    parent = runtime.graph.invocations[key.invocation_id]
    join = runtime.graph.joins[parent.join_id]
    child_id = max(join.member_invocation_ids, key=lambda value: int(value.rsplit("-", 1)[1]))
    child_key = next(item for item in runtime.visible.values() if item.invocation_id == child_id)
    parent.state = InvocationState.WAIT_JOIN
    join.satisfied = False
    join.completed_member_ids = join.member_invocation_ids - {child_id}
    runtime._native_running_batch.reqs[0] = NS(rid=child_key.request_id)
    runtime.join_prefetch_min_advance_ms = 1000.
    stage = names["_ChildFinalStage"](
        key, join.join_id, child_id, child_key.context_epoch,
        100 + int(remaining_ms / 10.), now + 3600.,
        request_id=child_key.request_id, generated_tokens=100, tokens_per_second=100.,
    )
    runtime._final_stages[join.join_id] = stage
    runtime._final_request_stages[child_key.request_id] = stage
    if report_service:
        runtime.join_prefetch_trigger = "report_service_or_eos"
        runtime._semantic_progress[child_key.request_id] = deque((
            (now * 1000. - 200., 80), (now * 1000., 100),
        ))
    if not runtime._live_final_stage(stage):
        raise AssertionError("selection fixture has an invalid JOIN")

    def reset_selection():
        reset()
        runtime._join_ticket = None
        runtime._report_candidate_next_ms = 0.

    reset_selection()
    return runtime, reads, actions, reset_selection, now


def measure_selection(baseline, *, depth, remaining_ms, samples, report_service=False):
    variants = {
        name: selection_fixture(
            cls, depth=depth, remaining_ms=remaining_ms, report_service=report_service,
        )
        for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime))
    }
    costs = {name: [] for name in variants}
    native_reads = {name: Counter() for name in variants}
    try:
        for sample in range(samples + 10):
            signatures = {}
            order = list(variants)
            if sample % 2:
                order.reverse()
            for name in order:
                runtime, reads, actions, reset, now = variants[name]
                reset()
                with patch("time.monotonic", lambda: now):
                    started = time.thread_time_ns()
                    result = runtime._roll_final_stage()
                    elapsed = (time.thread_time_ns() - started) / 1000.
                if (
                    (result is not None) != (remaining_ms <= 1000.)
                    or actions or runtime.physical_ledger.pending_count or runtime.physical_disabled
                ):
                    raise AssertionError((name, remaining_ms, dict(runtime.counts)))
                signatures[name] = (
                    ([asdict(step) for step in result.steps],
                     result.required_full_tokens, result.required_mamba_slots)
                    if result is not None else None
                )
                if sample >= 10:
                    costs[name].append(elapsed)
                    native_reads[name].update(reads)
            if signatures["baseline"] != signatures["candidate"]:
                raise AssertionError("the selection target changed")
        return {
            "case": "report_service_join_selection" if report_service else "provisional_join_selection",
            "depth": depth,
            "remaining_ms": remaining_ms, "lead_ms": 1000.,
            "costs": {name: distribution(values) for name, values in costs.items()},
            "native_reads_per_scan": {
                name: {key: value / samples for key, value in reads.items()}
                for name, reads in native_reads.items()
            },
            "selected_target_and_no_physical_actions_equal": True,
        }
    finally:
        for runtime, *_ in variants.values():
            runtime.close()


def measure_prefill_check(baseline, *, depth, matching, ready, legacy, samples):
    variants = {
        name: dispatch_fixture(
            cls, source="join_ticket", depth=max(4, depth), native_budget=True,
        )
        for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime))
    }
    callbacks, outputs, before, native_checks = {}, {}, {}, {}
    try:
        for name, (runtime, key, cache, _, actions, _, _, dispatch, now) in variants.items():
            if depth:
                with patch("time.monotonic", lambda: now):
                    dispatch()
                if runtime.physical_ledger.pending_count != depth:
                    raise AssertionError("prefill check needs actual pending reservations")
            if legacy:
                key = replace(key, session_id=None, session_generation=None)
                expectations = [
                    runtime.physical_ledger.pending_expectation(command)
                    for command in runtime._prefetch_steps
                ]
                runtime.physical_ledger = type(runtime.physical_ledger)()
                for expected in expectations:
                    step, source, started = runtime._prefetch_steps[expected.command_id]
                    runtime._prefetch_steps[expected.command_id] = (
                        replace(step, key=key), source, started,
                    )
                    runtime.physical_ledger.register(replace(
                        expected, session_id=None, session_generation=None,
                    ))
            if not matching:
                key = replace(key, context_id="unrelated", session_id="unrelated")
            runtime.visible[key.request_id] = key
            request = NS(
                rid=key.request_id,
                beliefkv_metadata={
                    "root_workflow_id": key.root_workflow_id,
                    "invocation_id": key.invocation_id,
                    "context_id": key.context_id, "context_epoch": key.context_epoch,
                },
                cache_request_handle=NS(attempt_id=key.attempt_id),
                session_id=key.session_id, session_generation=key.session_generation,
            )
            checked = {"calls": 0, "nodes": ()}

            def can_admit(commands, *, runtime=runtime, checked=checked):
                checked["calls"] += 1
                checked["nodes"] = tuple(
                    runtime._prefetch_steps[command][0].node_id for command in commands
                )
                return ready

            cache.beliefkv_prefetch_can_admit = can_admit
            native_checks[name] = checked
            callbacks[name] = lambda runtime=runtime, request=request: (
                runtime.defer_prefill_for_prefetch(request)
            )
            before[name] = state_signature(runtime, actions)
            outputs[name] = callbacks[name]()
        if (
            outputs["baseline"] != outputs["candidate"]
            or outputs["candidate"] != bool(depth and matching and not ready)
            or before["baseline"] != before["candidate"]
        ):
            raise AssertionError("prefill dependencies or physical state changed")
        costs = {name: [] for name in variants}
        calls = 100
        for sample in range(samples + 10):
            order = list(variants)
            if sample % 2:
                order.reverse()
            for name in order:
                started = time.thread_time_ns()
                for _ in range(calls):
                    callbacks[name]()
                elapsed = (time.thread_time_ns() - started) / calls / 1000.
                if sample >= 10:
                    costs[name].append(elapsed)
        if native_checks["baseline"] != native_checks["candidate"]:
            raise AssertionError("native readiness checks changed targets or frequency")
        for name, (runtime, _, _, _, actions, *_) in variants.items():
            if state_signature(runtime, actions) != before[name]:
                raise AssertionError("prefill check changed pending physical reservations")
        return {
            "case": "prefill_restore_dependency_check",
            "pending_commands": depth, "matching_context": matching,
            "native_state_ready": ready, "legacy_session": legacy,
            "deferred": outputs["candidate"], "calls_per_sample": calls,
            "costs": {name: distribution(values) for name, values in costs.items()},
            "native_checks": native_checks["candidate"],
            "native_checks_and_pending_physical_state_equal": True,
        }
    finally:
        for runtime, *_ in variants.values():
            runtime.close()


def measure(
    baseline, *, source, depth, diagnostics, native_budget, samples, tail_reclaim=False,
    workflows=1, running=48, sparse_session_leaves=False, numpy_created_times=False,
    align_command_namespaces=False,
):
    with (
        patch("time.monotonic", lambda: 1000.)
        if align_command_namespaces else nullcontext()
    ):
        variants = {
            name: dispatch_fixture(
                cls, source=source, depth=depth,
                diagnostics=diagnostics, native_budget=native_budget,
                tail_reclaim=tail_reclaim,
                workflows=workflows, running=running,
                sparse_session_leaves=sparse_session_leaves,
                numpy_created_times=numpy_created_times,
            )
            for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime))
        }
    if align_command_namespaces:
        for runtime, *_ in variants.values():
            runtime._physical_command_namespace = "0123456789abcdef0123456789abcdef"
    costs = {name: [] for name in variants}
    observed_reads = {name: Counter() for name in variants}
    canonical = None
    try:
        for sample in range(samples + 10):
            states = {}
            order = list(variants)
            if sample % 2:
                order.reverse()
            for name in order:
                runtime, _, _, reads, actions, rows, reset, dispatch, now = variants[name]
                reset()
                with patch("time.monotonic", lambda: now), (
                    patch("time.time", lambda: 1700000000.)
                    if align_command_namespaces else nullcontext()
                ):
                    started = time.thread_time_ns()
                    dispatch()
                    elapsed = (time.thread_time_ns() - started) / 1000.
                states[name] = state_signature(runtime, actions)
                if align_command_namespaces:
                    states[name]["records"] = rows[:]
                    states[name]["expectations"] = [
                        asdict(pending[0])
                        for pending in runtime.physical_ledger._pending.values()
                    ]
                expected_mode = (
                    "native_next_prefill" if native_budget else "legacy_capacity_unavailable"
                )
                expected_nodes = min(depth, runtime.physical_ledger.max_pending)
                if (
                    len(actions) != expected_nodes
                    or runtime.physical_ledger.pending_count != expected_nodes
                    or runtime._residency_budget.source != expected_mode or runtime.physical_disabled
                ):
                    raise AssertionError((name, source, depth, states[name], dict(runtime.counts)))
                if sample >= 10:
                    costs[name].append(elapsed)
                    observed_reads[name].update(reads)
            if states["baseline"] != states["candidate"]:
                raise AssertionError("a stable cache changed physical targets or pending credit")
            if align_command_namespaces:
                canonical = hashlib.sha256(
                    json.dumps(states["candidate"], sort_keys=True).encode(),
                ).hexdigest()
        runtime, _, _, _, _, _, reset, dispatch, now = variants["candidate"]
        reset()
        with patch("time.monotonic", lambda: now):
            profile = cProfile.Profile()
            profile.runcall(dispatch)
        functions = sorted(pstats.Stats(profile).stats.items(), key=lambda item: -item[1][2])
        return {
            "source": source, "depth": depth, "issued_nodes": expected_nodes,
            "diagnostics": diagnostics,
            "tail_reclaim_supported": tail_reclaim,
            "workflows": workflows, "configured_running_requests": running,
            "sparse_session_leaves": sparse_session_leaves,
            "numpy_created_times": numpy_created_times,
            "aligned_command_namespaces_and_record_clocks": align_command_namespaces,
            "records_and_complete_expectations_equal": align_command_namespaces,
            "canonical_state_sha256": canonical,
            "budget_mode": "native_next_prefill" if native_budget else "legacy_capacity_unavailable",
            "costs": {name: distribution(values) for name, values in costs.items()},
            "raw_thread_cpu_us": costs,
            "native_reads_per_dispatch": {
                name: {key: value / samples for key, value in counters.items()}
                for name, counters in observed_reads.items()
            },
            "stable_physical_targets_and_pending_credit_equal": True,
            "top_profile_self": [
                {
                    "file": key[0], "line": key[1], "function": key[2],
                    "calls": values[1], "self_ms": values[2] * 1000.,
                    "cumulative_ms": values[3] * 1000.,
                } for key, values in functions[:16]
            ],
        }
    finally:
        for runtime, *_ in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="7f1509e")
    parser.add_argument("--samples", type=int, default=31)
    parser.add_argument("--depths", type=int, nargs="+", default=(4, 16, 64))
    parser.add_argument("--include-selection", action="store_true")
    parser.add_argument("--include-prefill-checks", action="store_true")
    parser.add_argument("--prefill-checks-only", action="store_true")
    parser.add_argument("--prefill-key-layouts", type=int, default=1)
    parser.add_argument("--report-selection-only", action="store_true")
    parser.add_argument("--tail-reclaim", action="store_true")
    parser.add_argument("--workflows", type=int, default=1)
    parser.add_argument("--running", type=int, default=48)
    parser.add_argument("--sparse-session-leaves", action="store_true")
    parser.add_argument("--numpy-created-times", action="store_true")
    parser.add_argument("--align-command-namespaces", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        args.samples < 1 or args.prefill_key_layouts < 1
        or args.workflows < 1 or args.running < 1
        or any(depth < 1 for depth in args.depths) or args.output.exists()
    ):
        parser.error("use positive samples/key layouts and a fresh output path")
    paths = (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/sglang_v0520_admission.py",
        "beliefkv/runtime/sglang_v0520_physical.py",
        "beliefkv/runtime/sglang_v0520_observer.py",
        "beliefkv/runtime/event_channel.py",
        "scripts/benchmark_wait_prefetch_dispatch_cpu.py",
        "scripts/benchmark_native_prepare_path.py",
    )
    digests = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}
    baseline, baseline_sources = baseline_runtime(args.baseline)
    cases = [] if args.prefill_checks_only or args.report_selection_only else [
        measure(
            baseline, source=source, depth=depth, diagnostics=diagnostics,
            native_budget=native_budget, samples=args.samples,
            tail_reclaim=args.tail_reclaim,
            workflows=args.workflows, running=args.running,
            sparse_session_leaves=args.sparse_session_leaves,
            numpy_created_times=args.numpy_created_times,
            align_command_namespaces=args.align_command_namespaces,
        )
        for source in ("join_ticket", "tool_wait") for depth in args.depths
        for diagnostics in (False, True) for native_budget in (False, True)
    ]
    selection_cases = [
        measure_selection(
            baseline, depth=depth, remaining_ms=remaining, samples=args.samples,
            report_service=args.report_selection_only,
        ) for depth in ((4, 16, 64, 256) if args.report_selection_only else (4, 16, 64))
        for remaining in (500., 1500.)
    ] if args.include_selection or args.report_selection_only else []
    prefill_cases = [
        {
            "key_layout": layout,
            **measure_prefill_check(
                baseline, depth=depth, matching=matching, ready=ready,
                legacy=legacy, samples=args.samples,
            ),
        }
        for layout in range(args.prefill_key_layouts)
        for depth, matching, ready, legacy in (
            (0, False, True, False),
            (4, False, False, False), (64, False, False, False),
            (4, True, True, False), (4, True, False, False),
            (64, True, True, False), (64, True, False, False),
            (4, True, True, True), (4, True, False, True),
        )
    ] if args.include_prefill_checks or args.prefill_checks_only else []
    if any(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest
           for path, digest in digests.items()):
        raise RuntimeError("CPU benchmark sources changed during measurement")
    report = {
        "status": "complete_cpu_comparison_not_gpu_or_jct_gain",
        "scope": "Real CPU cache observation, selection, revalidation and transaction registration; no DMA or GPU",
        "at": datetime.now().astimezone().isoformat(),
        "baseline_revision": args.baseline, "baseline_source_sha256": baseline_sources,
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "candidate_source_sha256": digests, "samples": args.samples, "warmup_samples": 10,
        "cases": cases, "selection_cases": selection_cases,
        "prefill_check_cases": prefill_cases,
        "prefill_key_layouts": args.prefill_key_layouts,
        "dispatch_workflows": args.workflows,
        "dispatch_running_requests": args.running,
        "dispatch_sparse_session_leaves": args.sparse_session_leaves,
        "dispatch_numpy_created_times": args.numpy_created_times,
        "dispatch_aligned_command_namespaces": args.align_command_namespaces,
        "limitations": [
            "The CPU native enqueue uses scalar index stand-ins, not the real native allocator or controller.",
            "Plans have real positive Host-only prefixes and causal identities in both sources.",
            "Physical actions remain pending; this fixture never fabricates DMA completion or ACK/use credit.",
            "Resets, state verification and cProfile are outside the CPU timings.",
            "Each prefill key layout creates independent command namespaces and dictionaries; samples within it reuse that layout.",
            "When enabled, the diagnostic writer appends records to memory without I/O or background GIL contention.",
            "Local CPU timings and synthetic scene frequencies do not establish workflow throughput or JCT gains.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps([
        {
            **{key: case[key] for key in ("source", "depth", "diagnostics", "budget_mode")},
            "baseline_p50_us": case["costs"]["baseline"]["p50_us"],
            "candidate_p50_us": case["costs"]["candidate"]["p50_us"],
        }
        for case in cases
    ] + selection_cases + prefill_cases, indent=2), flush=True)


if __name__ == "__main__":
    main()
