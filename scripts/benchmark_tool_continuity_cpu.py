#!/usr/bin/env python3
"""Measure complete advisory planning/selection with short-tool continuity."""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_shared_path import distribution, synthetic_runtime


def fixture(size, *, enabled, ready):
    with patch.dict("os.environ", {
        "BELIEFKV_PREFILL_SCHEDULING_POLICY": "join_priority",
        "BELIEFKV_ENABLE_TOOL_CONTINUITY": str(int(enabled)),
        "BELIEFKV_ENABLE_JOIN_LAST_CHILD_PRIORITY": "0",
    }):
        runtime, queue = synthetic_runtime(
            NativeAdmissionRuntime, CausalFrontierScheduler, size, 1,
        )
    now = time.monotonic()
    for req in queue:
        runtime._visible_since[req.rid] = now - .2
    if enabled and ready:
        req = queue[-1]
        key = runtime.visible[req.rid]
        prior = replace(key, request_id="previous-" + key.request_id)
        current = replace(key, context_epoch=key.context_epoch + 1)
        req.beliefkv_metadata["context_epoch"] = current.context_epoch
        runtime.graph.contexts[key.context_id].epoch = current.context_epoch
        runtime.visible[req.rid] = current
        runtime.context_sessions[key.context_id] = current
        runtime._visible_since[req.rid] = now
        runtime._tool_continuity.admitted(prior, arrival=now - 1., promoted=False)
        runtime._tool_continuity.completed(prior, now=now - .1)
        for kind, timestamp in ((Kind.TOOL_START, 100.), (Kind.TOOL_END, 120.)):
            runtime._tool_continuity.observe(RuntimeEvent(
                kind.value, timestamp, kind, key.root_workflow_id,
                invocation_id=key.invocation_id, context_id=key.context_id,
                context_epoch=key.context_epoch, attributes={"tool_run_id": "short"},
            ))
    return runtime, queue


def exercise(runtime, queue):
    inspection = runtime.inspect_native_prefill_queue(queue)
    plan = runtime.plan_native_prefill(
        queue, running_batch=None, adder=None, _queue_inspection=inspection,
    )
    selected = select_native_prefill_candidates(
        queue, plan=plan, current_semantic_revision=runtime.semantic_revision,
    )
    if selected.rejected or {req.rid for req in selected.candidates} != {req.rid for req in queue}:
        raise AssertionError("continuity must preserve the entire admitted candidate set")
    return tuple(req.rid for req in selected.candidates)


def benchmark(*, samples=201):
    if samples < 3:
        raise ValueError("at least three samples required")
    rows = []
    for size in (8, 36, 128):
        for ready in (False, True):
            fixtures = {enabled: fixture(size, enabled=enabled, ready=ready)
                        for enabled in (False, True)}
            times = {enabled: [] for enabled in fixtures}
            try:
                orders = {enabled: exercise(*pair) for enabled, pair in fixtures.items()}
                if ready:
                    assert orders[True][0] == orders[False][-1]
                else:
                    assert orders[True] == orders[False]
                for index in range(samples + 10):
                    for enabled in ((False, True) if index % 2 == 0 else (True, False)):
                        started = time.perf_counter_ns()
                        assert exercise(*fixtures[enabled]) == orders[enabled]
                        elapsed = (time.perf_counter_ns() - started) / 1e6
                        if index >= 10:
                            times[enabled].append(elapsed)
                rows.append({
                    "queue_size": size, "short_successor_ready": ready,
                    "baseline": distribution(times[False]),
                    "continuity": distribution(times[True]),
                    "raw_ms": {str(enabled): values for enabled, values in times.items()},
                })
            finally:
                for runtime, _ in fixtures.values():
                    runtime.close()
    return {
        "scope": "CPU advisory inspection, planning and live selection; no allocator or DMA",
        "sampling": "alternating; normal GC; ten warmups; all measured samples retained",
        "cases": rows,
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                "beliefkv/runtime/tool_continuity.py",
                "beliefkv/runtime/sglang_v0520_runtime.py",
                "scripts/benchmark_tool_continuity_cpu.py",
            )
        },
        "gpu_performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(benchmark(), indent=2) + "\n")


if __name__ == "__main__":
    main()
