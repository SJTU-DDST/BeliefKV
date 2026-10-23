#!/usr/bin/env python3
"""Compare exact metadata policy decisions and CPU cost against pinned sources."""

from __future__ import annotations

import argparse
from dataclasses import fields, replace
import gc
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.benchmark_subagent_retention_cpu import policy_fixture


SOURCE = "beliefkv/runtime/subagent_retention.py"


def reference_module(revision):
    commit = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "--verify", revision + "^{commit}"], text=True,
    ).strip()
    source = subprocess.check_output(["git", "-C", str(ROOT), "show", commit + ":" + SOURCE])
    module = ModuleType("dormant_geometry_reference")
    sys.modules[module.__name__] = module
    exec(compile(source, f"git:{commit}:{SOURCE}", "exec"), module.__dict__)
    return module, commit, hashlib.sha256(source).hexdigest()


def fixture(count, depth, trained, reference=None):
    current = policy_fixture(count, depth)
    if reference is None:
        manager = current
    else:
        manager = reference.DormantSubagentRetention(
            "selective", host_budget_bytes=current.host_budget_bytes,
        )
        for key, state in current.states.items():
            attributes = {item.name: getattr(state, item.name)
                          for item in fields(reference.DormantState) if item.init}
            attributes["extents"] = tuple(
                reference.KVExtent(**vars(item)) for item in state.extents
            )
            manager.states[key] = reference.DormantState(**attributes)
    if trained:
        for index in range(16):
            manager.estimator.observe({}, bool(index % 2), predicted=.5)
    return manager


def outcome(manager):
    return (
        [(key, state.tier, state.probability, state.score, state.private_bytes)
         for key, state in manager.states.items()],
        tuple(manager.work), dict(manager.counts), manager.dirty,
    )


def refresh_four(manager, iteration):
    for index, state in enumerate(manager.states.values()):
        if index == 4:
            break
        state.extents = tuple(
            replace(item, host=bool(iteration % 2), created=item.created + 1.)
            for item in state.extents
        )


def timed_plan(manager):
    wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
    manager._plan()
    return (time.perf_counter_ns() - wall) / 1000., (time.thread_time_ns() - cpu) / 1000.


def compare(reference, count, depth, trained, mode, samples):
    managers = [fixture(count, depth, trained, module) for module in (reference, None)]
    if mode != "fresh":
        for _ in range(10):
            for manager in managers:
                manager._plan()
    wall, cpu = [[], []], [[], []]
    for index in range(samples):
        if mode == "fresh":
            managers = [fixture(count, depth, trained, module) for module in (reference, None)]
        elif mode == "refresh_four":
            for manager in managers:
                refresh_four(manager, index)
        for which in (0, 1) if index % 2 else (1, 0):
            elapsed, thread = timed_plan(managers[which])
            wall[which].append(elapsed)
            cpu[which].append(thread)
        if outcome(managers[0]) != outcome(managers[1]):
            raise AssertionError("ranking, tiers, capacity, work or counters differ")
    medians = [statistics.median(values) for values in wall]
    return {
        "contexts": count, "extents_per_context": depth + 1, "predictor_trained": trained,
        "mode": mode, "identical_policy_outcomes": True,
        "reference_median_us": medians[0], "current_median_us": medians[1],
        "current_change_percent": (medians[1] / medians[0] - 1.) * 100.,
        "reference_wall_us": wall[0], "current_wall_us": wall[1],
        "reference_thread_us": cpu[0], "current_thread_us": cpu[1],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=101)
    args = parser.parse_args()
    if args.samples < 21 or not gc.isenabled():
        raise ValueError("use at least 21 samples and normal GC")
    reference, commit, digest = reference_module(args.reference_revision)
    cases = [
        compare(reference, count, depth, trained, mode, args.samples)
        for count, depth in ((36, 8), (128, 16), (512, 32))
        for trained in (False, True)
        for mode in ("fresh", "stable", "refresh_four")
    ]
    result = {
        "semantics": "CPU metadata only; alternating order, normal GC, all raw samples; no native inspection, DMA or JCT claim",
        "reference_commit": commit, "reference_source_sha256": digest,
        "source_sha256": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in (SOURCE, "scripts/benchmark_subagent_retention_cpu.py",
                         str(Path(__file__).resolve().relative_to(ROOT)))
        },
        "gpu_performance_accepted": False, "samples": args.samples, "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps([
        {key: value for key, value in case.items() if not isinstance(value, list)}
        for case in cases
    ], indent=2))


if __name__ == "__main__":
    main()
