#!/usr/bin/env python3
"""CPU correctness, latency and sensitivity checks; not a serving experiment."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
from statistics import median
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.runtime.layer_prefetch_policy import (
    LayerCost, LayerCostProfile, LayerPrefetchBudget, LeadTimeScenario,
    choose_prefetch_depth,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime


def quantiles(values):
    ordered = sorted(values)
    return {
        "p50": median(ordered),
        "p90": ordered[int((len(ordered) - 1) * .9)],
        "p99": ordered[int((len(ordered) - 1) * .99)],
    }


def profile(kind, full_bytes, *, bandwidth_bytes_per_ms=24_000_000., compute_ms=.2):
    hybrid = kind == "hybrid"
    layer_types = ("state", "state", "state", "full") * 10 if hybrid else ("full",) * 40
    full_layers = layer_types.count("full")
    costs = tuple(LayerCost(
        full_bytes // full_layers if name == "full" else 0,
        64389120 // 30 if name == "state" else 0,
        (
            full_bytes / full_layers if name == "full" else 64389120 / 30
        ) / bandwidth_bytes_per_ms,
        compute_ms,
    ) for name in layer_types)
    return LayerCostProfile.build(
        costs, allocation_bytes=full_bytes + (64389120 if hybrid else 0),
        before_forward_layer=38 if hybrid else None,
        before_forward_copy_ms=.05 if hybrid else 0.,
    )


def simulate(costs, depth, lead, backlog):
    clock, ready = backlog, []
    for index, layer in enumerate(costs.layers):
        if index == depth:
            clock = max(clock, lead)
        clock += layer.transfer_ms
        ready.append(clock)
    clock = max(lead, ready[costs.before_forward_layer]) if (
        costs.before_forward_layer is not None
    ) else lead
    clock += costs.before_forward_copy_ms
    for layer, arrival in zip(costs.layers, ready):
        clock = max(clock, arrival) + layer.compute_ms
    return clock


def realized_score(costs, depth, lead, resources):
    service = costs.transfer_prefix_ms[depth]
    if (
        costs.byte_prefix[depth] > resources.transfer_bytes
        or service > resources.transfer_ms
        or depth and costs.allocation_bytes > resources.allocation_bytes
    ):
        return None
    if depth == 0:
        return 0.
    gain = simulate(costs, 0, lead, resources.backlog_ms) - simulate(
        costs, depth, lead, resources.backlog_ms,
    )
    return (
        resources.continuation_probability * gain
        - (1 - resources.continuation_probability + resources.transfer_shadow_price) * service
        - resources.control_ms
        - costs.allocation_bytes / 1024 ** 3 * lead / 1000.
        * resources.residency_price_ms_per_gib_second
    )


def decision_benchmark(iterations):
    costs = profile("hybrid", 256 * 1024 ** 2)
    scenarios = (
        LeadTimeScenario(2., .2), LeadTimeScenario(5., .5), LeadTimeScenario(15., .3),
    )
    resources = LayerPrefetchBudget(
        costs.allocation_bytes, costs.byte_prefix[-1], 100.,
        transfer_shadow_price=.1, control_ms=.01,
    )
    for _ in range(100):
        choose_prefetch_depth(costs, scenarios, budget=resources)
    times = []
    for _ in range(iterations):
        begin = time.perf_counter_ns()
        choose_prefetch_depth(costs, scenarios, budget=resources)
        times.append((time.perf_counter_ns() - begin) / 1000.)
    builds = []
    for _ in range(200):
        begin = time.perf_counter_ns()
        profile("hybrid", 256 * 1024 ** 2)
        builds.append((time.perf_counter_ns() - begin) / 1000.)
    return {
        "iterations": iterations, "layers": 40, "lead_scenarios": 3,
        "cached_profile_decision_us": quantiles(times),
        "cold_profile_build_us": quantiles(builds),
        "scope": "CPU function wall time; no scheduler contention, engine or GPU",
    }


def maintenance_benchmark(iterations):
    results = {}
    for interval in (0., 100.):
        runtime = NativeAdmissionRuntime()
        runtime._prediction_maintenance_interval_ms = interval
        runtime.tool_wait_hints = {
            f"candidate-{index}": SimpleNamespace(
                key=None, live=lambda key, now_ms: True,
            ) for index in range(512)
        }
        try:
            begin = time.perf_counter_ns()
            for index in range(iterations):
                runtime._maintain_prediction_state(1000. + index * 10.)
            elapsed = (time.perf_counter_ns() - begin) / 1e6
            results[str(interval)] = {
                "total_cpu_wall_ms": elapsed,
                "sweeps": runtime.counts["prediction_maintenance_sweeps"],
                "deferred": runtime.counts["prediction_maintenance_deferred"],
            }
        finally:
            runtime.close()
    return {
        "iterations": iterations, "candidate_count": 512, "modeled_tick_ms": 10.,
        "interval_ms": results,
        "scope": "controlled hint inventory; cleanup cost only, not full control cost or JCT",
    }


def sensitivity(case_count):
    rng, rows = random.Random(21), []
    depths = Counter()
    for index in range(case_count):
        kind = "hybrid" if index % 2 else "full"
        size = rng.choice((4, 32, 128, 512, 1024)) * 1024 ** 2
        compute = rng.choice((.05, .2, 1.))
        expected_lead = rng.choice((.1, .5, 2., 5., 20., 100., 1000.))
        expected = profile(kind, size, compute_ms=compute)
        actual = profile(
            kind, size, compute_ms=compute,
            bandwidth_bytes_per_ms=24_000_000. * rng.choice((.5, .8, 1., 1.25, 2.)),
        )
        actual_lead = expected_lead * rng.choice((.25, .5, 1., 2., 4.))
        resources = LayerPrefetchBudget(
            expected.allocation_bytes, expected.byte_prefix[-1],
            expected.transfer_prefix_ms[-1] * 2.1,
            backlog_ms=rng.choice((0., 0., .5, 5.)),
            transfer_shadow_price=.1, control_ms=.01,
            residency_price_ms_per_gib_second=.1,
        )
        scenarios = (
            LeadTimeScenario(expected_lead * .5, .2),
            LeadTimeScenario(expected_lead, .5),
            LeadTimeScenario(expected_lead * 2., .3),
        )
        decision = choose_prefetch_depth(expected, scenarios, budget=resources)
        depths[f"{kind}:{decision.depth}"] += 1
        oracle = max(
            (realized_score(actual, depth, actual_lead, resources), depth)
            for depth in range(41)
            if realized_score(actual, depth, actual_lead, resources) is not None
        )
        selected = realized_score(actual, decision.depth, actual_lead, resources)
        rows.append({
            "kind": kind, "full_bytes": size, "expected_lead_ms": expected_lead,
            "actual_lead_ms": actual_lead, "selected_depth": decision.depth,
            "oracle_depth": oracle[1], "selected_score_ms": selected,
            "oracle_score_ms": oracle[0],
            "fixed_20_score_ms": realized_score(actual, 20, actual_lead, resources),
            "full_prefetch_score_ms": realized_score(actual, 40, actual_lead, resources),
        })
    oracle_sum = sum(row["oracle_score_ms"] for row in rows)
    selected_sum = sum(row["selected_score_ms"] or 0. for row in rows)
    return {
        "case_count": case_count, "seed": 21, "selected_depths": dict(sorted(depths.items())),
        "summed_local_oracle_score_ms": oracle_sum,
        "summed_selected_score_ms": selected_sum,
        "modeled_oracle_score_recovered_fraction": selected_sum / oracle_sum if oracle_sum else None,
        "negative_selected_score_cases": sum((row["selected_score_ms"] or 0.) < 0 for row in rows),
        "infeasible_actual_service_budget_cases": sum(
            row["selected_score_ms"] is None for row in rows
        ),
        "scope": (
            "controlled geometry/bandwidth/lead-error sensitivity, not trace-calibrated. "
            "Oracle knows realized lead and bandwidth but has the same local budgets "
            "and modeled costs; it is not a whole-system throughput oracle."
        ),
        "cases": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=5000)
    parser.add_argument("--cases", type=int, default=500)
    parser.add_argument(
        "--model-config", type=Path,
        default=Path("/srv/ai/models/Qwen/Qwen3.5-35B-A3B/config.json"),
    )
    args = parser.parse_args()
    if args.iterations < 1 or args.cases < 1:
        parser.error("positive iteration and case counts required")
    config = json.loads(args.model_config.read_text())["text_config"]
    if (
        config["num_hidden_layers"] != 40
        or config["layer_types"] != (["linear_attention"] * 3 + ["full_attention"]) * 10
    ):
        raise ValueError("controlled hybrid geometry differs from the selected model")
    inputs = [
        Path(__file__).resolve(), ROOT / "beliefkv/runtime/layer_prefetch_policy.py",
        ROOT / "beliefkv/runtime/sglang_v0520_runtime.py", args.model_config.resolve(),
    ]
    report = {
        "scope": "CPU prototype qualification; native layer pause/resume is not installed",
        "gpu_experiment_started": False,
        "source_bindings": [{
            "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        } for path in inputs],
        "decision_benchmark": decision_benchmark(args.iterations),
        "prediction_maintenance": maintenance_benchmark(args.iterations),
        "sensitivity": sensitivity(args.cases),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "report": str(args.output), "decision": report["decision_benchmark"],
        "maintenance": report["prediction_maintenance"],
        "sensitivity": {key: value for key, value in report["sensitivity"].items() if key != "cases"},
    }, indent=2))


if __name__ == "__main__":
    main()
