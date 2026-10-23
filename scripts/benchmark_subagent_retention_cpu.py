#!/usr/bin/env python3
"""Measure boundary policy costs; no CUDA, cache simulation or speedup claim."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from statistics import mean, median, quantiles
import sys
import time
from types import SimpleNamespace as NS


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.reusable_subagents import reuse_features
from beliefkv.runtime.subagent_retention import DormantState, DormantSubagentRetention, KVExtent


def measure(operation, samples, *, iterations=1):
    for _ in range(10):
        operation()
    wall, cpu = [], []
    for _ in range(samples):
        begin, thread = time.perf_counter_ns(), time.thread_time_ns()
        for _ in range(iterations):
            operation()
        cpu.append((time.thread_time_ns() - thread) / iterations / 1000.)
        wall.append((time.perf_counter_ns() - begin) / iterations / 1000.)
    return {
        "wall_median_us": median(wall), "wall_mean_us": mean(wall),
        "wall_p95_us": quantiles(wall, n=20)[18],
        "thread_median_us": median(cpu), "samples_wall_us": wall, "samples_thread_us": cpu,
    }


def policy_fixture(count, depth):
    manager = DormantSubagentRetention("selective", host_budget_bytes=32 * 1024**3)
    # Match actual metadata lengths; these extents are policy inputs, not a
    # claim that all bytes fit or that the real native tree has this shape.
    for index in range(count):
        key = NS(
            context_id=f"context-{index}", root_workflow_id=f"root-{index % 36}",
            invocation_id=f"invocation-{index}",
        )
        extents = tuple(
            KVExtent(0, index * depth + n, 1., 4096, 4096 * 20480,
                     True, n % 2 == 0, True, False)
            for n in range(depth)
        ) + (KVExtent(2, index * depth, 1., 1, 64389120, True, True, True, False),)
        state = DormantState(
            key, f"handle-{index}", {"reuse_count": 0}, float(index),
            extents=extents, scored=True,
        )
        manager.states[key.context_id] = state
    return manager


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=101)
    args = parser.parse_args()
    if args.samples < 21:
        raise ValueError("at least 21 samples required")
    cases = []
    for count, depth in ((36, 8), (128, 16), (512, 32)):
        manager = policy_fixture(count, depth)
        for warm in (False, True):
            if warm:
                for index in range(16):
                    manager.estimator.observe({}, bool(index % 2), predicted=.5)
            costs = measure(manager._plan, args.samples)
            assert manager.counts["planned_unique_host_bytes"] <= manager.host_budget_bytes
            assert manager.counts["planned_device_bytes"] <= manager.device_budget_bytes
            cases.append({
                "contexts": count, "extents_per_context": depth + 1,
                "predictor_warm": warm, "costs": costs,
                "actions": dict(Counter(
                    state.tier for state in manager.states.values()
                )),
            })
    empty = DormantSubagentRetention()
    description = ("Implement cache state transitions in src/cache.py with focused tests. " * 128)[:8192]
    report = ("Remaining failing tests need follow-up in the same module. " * 160)[-8192:]
    result = {
        "semantics": (
            "CPU-only metadata policy and bounded feature extraction, normal GC; "
            "no CUDA, DMA, native path inspection or end-to-end benefit estimate."
        ),
        "cases": cases,
        "empty_dispatch": measure(lambda: empty.dispatch(None), args.samples, iterations=1000),
        "boundary_features": measure(lambda: reuse_features(description, report), args.samples),
        "structured_boundary_features": measure(
            lambda: reuse_features(
                description, report, completion={"status": "complete", "unresolved": []},
            ), args.samples,
        ),
        "source_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                ROOT / "beliefkv/runtime/subagent_retention.py",
                ROOT / "beliefkv/runtime/reusable_subagents.py",
                Path(__file__).resolve(),
            )
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({
        "cases": [{key: value for key, value in case.items() if key != "costs"}
                  | {"median_us": case["costs"]["wall_median_us"]} for case in cases],
        "empty_dispatch_median_us": result["empty_dispatch"]["wall_median_us"],
        "boundary_features_median_us": result["boundary_features"]["wall_median_us"],
        "structured_boundary_features_median_us": result["structured_boundary_features"]["wall_median_us"],
    }, indent=2))


if __name__ == "__main__":
    main()
