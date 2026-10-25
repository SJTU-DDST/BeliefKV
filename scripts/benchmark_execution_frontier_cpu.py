#!/usr/bin/env python3
"""Complete execution-frontier control cost with scalar native enqueue stand-ins."""

import argparse
from dataclasses import asdict, replace
import gc
import json
from pathlib import Path
from statistics import median
import sys
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.sglang_v0520_runtime import (
    NativeAdmissionRuntime, _ExecutionHandoffTicket,
)
from scripts.benchmark_wait_prefetch_dispatch_cpu import dispatch_fixture


def measure(
    depth, scenario, samples, *, enabled=True, preload_quota=4, priority_credit=4,
):
    runtime, key, cache, reads, actions, _, reset, _, _ = dispatch_fixture(
        NativeAdmissionRuntime, source="join_ticket", depth=depth,
        capture_issuance=True,
    )
    runtime.enable_frontier_restore = enabled
    runtime.frontier_restore_ordinary_quota = preload_quota
    runtime.enable_execution_handoff = runtime.enable_tool_prefetch = False
    runtime.prefill_scheduling_policy = "join_priority"
    request = NS(
        rid=key.request_id, session_id=key.session_id, session_generation=key.session_generation,
        cache_request_handle=NS(attempt_id=key.attempt_id),
        beliefkv_metadata={
            "root_workflow_id": key.root_workflow_id, "invocation_id": key.invocation_id,
            "context_id": key.context_id, "context_epoch": key.context_epoch,
        },
        origin_input_ids=tuple(range(depth * 128 + 1)), output_ids=(),
    )
    runtime.register_visible_request(request)
    leaf = depth
    cache.inspect_beliefkv_reentry = lambda req: {
        "component_leaves": ((0, ((leaf, leaf + 1.),)), (2, ((leaf, leaf + 1.),))),
        "reusable_input_tokens": depth * 128, "checkpoint_tokens": depth * 128,
        "device_checkpoint_tokens": 0,
        "missing_full_tokens": depth * 128, "missing_mamba_slots": 1,
    }
    if scenario == "new_context":
        cache.session_refs.snapshot_latest_session_leaf_anchors = lambda *_args, **_kwargs: None
    elif scenario == "resident":
        original = cache.inspect_beliefkv_reentry
        cache.inspect_beliefkv_reentry = lambda req: {
            **original(req), "missing_full_tokens": 0, "missing_mamba_slots": 0,
        }
    elif scenario == "no_capacity":
        cache.token_to_kv_pool_allocator.available_size = lambda: 0
    elif scenario == "no_byte_budget":
        runtime._residency_budget = replace(
            runtime._residency_budget, request_slots=1, byte_limit=0,
        )
    wall, cpu, signatures = [], [], []
    try:
        for index in range(samples + 20):
            runtime.on_batch_selected(NS(reqs=[request]))
            reset()
            runtime._join_ticket = None
            runtime._execution_handoff_next_ms = 0.
            runtime._prefetch_priority_normal_admissions = priority_credit
            runtime._frontier_probe_after_ms.clear()
            runtime._reentry_observations.clear()
            if enabled and scenario == "selected_pending":
                runtime._frontier_restore_consumer = _ExecutionHandoffTicket(
                    key, request, time.monotonic() + 60.,
                )
            started, thread = time.perf_counter_ns(), time.thread_time_ns()
            runtime.dispatch_frontier_restore([request], running_batch=NS(reqs=[]))
            elapsed_cpu = (time.thread_time_ns() - thread) / 1000.
            elapsed_wall = (time.perf_counter_ns() - started) / 1000.
            signature = (
                len(actions), runtime.physical_ledger.pending_count,
                runtime.physical_ledger.pending_action_bytes("PREFETCH_GPU"),
            )
            if enabled and scenario == "positive" and priority_credit >= preload_quota:
                if signature[0] != min(depth, runtime.physical_ledger.max_pending) or signature[1] != signature[0]:
                    raise AssertionError(("positive enqueue was lost", signature))
                consumer = runtime._frontier_restore_consumer
                if consumer is None or consumer.key != key:
                    raise AssertionError("positive enqueue lost its selected consumer")
            elif any(signature):
                raise AssertionError("negative probe issued a physical action")
            if enabled and scenario == "selected_pending" and (
                any(reads.values())
                or runtime.counts["frontier_restore_selection_pending"] != 1
            ):
                raise AssertionError("selected consumer caused another native inspection")
            if not enabled and (any(reads.values()) or runtime._frontier_probe_after_ms):
                raise AssertionError("disabled frontier performed native inspection or probe maintenance")
            if runtime._execution_handoff is not None or runtime.physical_disabled:
                raise AssertionError("frontier left a waiting ticket or invalid physical state")
            if index >= 20:
                cpu.append(elapsed_cpu)
                wall.append(elapsed_wall)
                signatures.append(signature)
        if len(set(signatures)) != 1:
            raise AssertionError("control iterations changed their native payloads")
        return {
            "depth": depth, "scenario": scenario, "frontier_restore": enabled,
            "preload_ordinary_quota": preload_quota, "scheduling_priority_credit": priority_credit,
            "control_payload_signature": signatures[0],
            "native_reads_last_iteration": dict(reads),
            "selected_pending_checks_last_iteration": (
                runtime.counts["frontier_restore_selection_pending"]
            ),
            "wall_p50_us": median(wall), "cpu_p50_us": median(cpu),
            "wall_samples_us": wall, "cpu_samples_us": cpu,
            "residency_budget": asdict(runtime._residency_budget),
        }
    finally:
        runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=101)
    parser.add_argument("--include-disabled", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists() or not gc.isenabled():
        parser.error("positive samples, normal GC and a new output are required")
    sources = {
        name: sha256_file(ROOT / name) for name in (
            "beliefkv/runtime/sglang_v0520_runtime.py",
            "beliefkv/runtime/sglang_v0520_physical.py",
            "scripts/benchmark_execution_frontier_cpu.py",
            "scripts/benchmark_wait_prefetch_dispatch_cpu.py",
        )
    }
    result = {
        "scope": "CPU planning, capture, validation, enqueue, ledger and issuance tracking; scalar native stand-ins, no DMA/allocator/JCT claim",
        "normal_gc": True, "retains_all_samples": True, "source_sha256": sources,
        "comparison_scope": (
            "Enabled and disabled use independent fixture runs in fixed order, not paired "
            "alternating samples; disabled must perform no native reads or physical actions."
            if args.include_disabled else "Enabled frontier only."
        ),
        "iteration_scope": (
            "Each iteration consumes the previous selection, resets fixture transfer state "
            "and restores the four-ordinary-admission quota outside timing. selected_pending "
            "supplies one live previously selected consumer without a pending DMA; its "
            "control check must perform no new native inspection or transfer."
        ),
        "cases": [
            measure(depth, scenario, args.samples, enabled=enabled)
            for depth in (4, 16, 64)
            for scenario in (
                "new_context", "resident", "no_capacity", "no_byte_budget",
                "positive", "selected_pending",
            )
            for enabled in ((True, False) if args.include_disabled else (True,))
        ],
    }
    if any(sha256_file(ROOT / name) != digest for name, digest in sources.items()):
        raise RuntimeError("benchmark source changed")
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "cases": len(result["cases"])}))


if __name__ == "__main__":
    main()
