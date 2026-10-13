#!/usr/bin/env python3
"""Compare complete and empty-restore paths on identical CPU ancestry fixtures."""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.sglang_v0520_physical import (
    ContextSessionAnchors, inspect_session_h2d_opportunity,
)
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from tests.test_sglang_v0520_observer import (
    ComponentData, UnifiedTreeCore, UnifiedTreeNode, _static_cache,
)


def fixture(depth, case):
    cache = _static_cache()
    nodes = {}
    for node_id in range(depth + 1):
        full = range(2) if node_id else None
        state = (0,) if node_id else None
        data = {
            pool: ComponentData(
                value=value, host_value=value, lock_ref=0, host_lock_ref=0,
                session_ref=1, session_ids={"s"},
            ) for pool, value in ((0, full), (2, state))
        }
        nodes[node_id] = UnifiedTreeNode(
            id=node_id, creation_time=node_id + 1., key=range(2 if node_id else 0),
            parent=nodes.get(node_id - 1), component_data=data,
            write_through_pending_id=None, load_back_pending_id=None,
        )
    cache.tree_core = UnifiedTreeCore(node_by_id=nodes.__getitem__)
    if case == "unbacked":
        for node in nodes.values():
            for data in node.component_data.values():
                data.value = data.host_value = None
    elif case == "full_host_only":
        nodes[1].component_data[0].value = None
    elif case == "mamba_host_only":
        nodes[depth].component_data[2].value = None
    anchors = ContextSessionAnchors(
        PrefillCandidateKey("r", "w", "i", "c", 0, 0, "s", 1),
        ((0, ((depth, depth + 1.),)), (2, ((depth, depth + 1.),))),
        1., reusable_input_tokens=2 * depth,
    )
    if case == "unknown_anchor":
        anchors = replace(anchors, component_leaves=(
            (0, ((depth, depth + 2.),)), (2, ((depth, depth + 2.),)),
        ))
    return cache, anchors


def distribution(values):
    return {"mean_ms": mean(values), "p50_ms": median(values), "max_ms": max(values)}


def benchmark(iterations):
    rows = []
    for depth in (4, 24, 64):
        for case in ("resident", "unbacked", "full_host_only", "mamba_host_only", "unknown_anchor"):
            cache, anchors = fixture(depth, case)
            normal = inspect_session_h2d_opportunity(cache, anchors, max_steps=128)
            optimized = inspect_session_h2d_opportunity(
                cache, anchors, max_steps=128, skip_empty_restore=True,
            )
            if normal.steps != optimized.steps:
                raise AssertionError((depth, case, normal.steps, optimized.steps))
            if case not in ("resident", "unbacked") and normal != optimized:
                raise AssertionError((depth, case, "positive/unknown evidence changed"))
            costs = {name: {"wall": [], "cpu": []} for name in ("baseline", "optimized")}
            for iteration in range(iterations):
                order = ("baseline", "optimized") if iteration % 2 else ("optimized", "baseline")
                for name in order:
                    wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                    result = inspect_session_h2d_opportunity(
                        cache, anchors, max_steps=128,
                        skip_empty_restore=name == "optimized",
                    )
                    costs[name]["cpu"].append((time.thread_time_ns() - cpu) / 1e6)
                    costs[name]["wall"].append((time.perf_counter_ns() - wall) / 1e6)
                    if result.steps != normal.steps:
                        raise AssertionError((depth, case, "plan changed during timing"))
            rows.append({
                "depth": depth, "case": case, "planned_extents": len(normal.steps),
                "iterations_per_variant": iterations,
                "baseline": {clock: distribution(values) for clock, values in costs["baseline"].items()},
                "optimized": {clock: distribution(values) for clock, values in costs["optimized"].items()},
                "thread_cpu_reduction_fraction": 1. - mean(costs["optimized"]["cpu"]) / mean(costs["baseline"]["cpu"]),
            })
    return {
        "scope": "Identical synthetic CPU tree fixtures; not a workload-weighted cost or throughput gain.",
        "results": rows,
        "source_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                ROOT / "beliefkv/runtime/sglang_v0520_physical.py",
                ROOT / "beliefkv/runtime/sglang_v0520_runtime.py",
                Path(__file__).resolve(),
            )
        },
        "semantics": [
            "Alternating measurement order; live host/device fields read on every call.",
            "A positive hint and unknown provenance retain complete planning and validation.",
            "A negative hint leaves unbacked-node diagnostics unknown; detailed audit still uses the complete path.",
            "No extrapolation to GPU execution, short-prefill forward time or end-to-end JCT.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1 or args.output.exists():
        parser.error("positive iterations and a fresh output path required")
    report = benchmark(args.iterations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([
        {key: row[key] for key in ("depth", "case", "planned_extents", "thread_cpu_reduction_fraction")}
        for row in report["results"]
    ], indent=2))


if __name__ == "__main__":
    main()
