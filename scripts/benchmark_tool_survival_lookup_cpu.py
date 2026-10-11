#!/usr/bin/env python3
"""Compare native survival queries on recorded CDFs without changing predictions."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from statistics import median
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import beliefkv.predictor.action_frontier as timing_module
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_prediction import NativeToolWaitHint


SOURCE = "beliefkv/predictor/action_frontier.py"


def baseline_curve(revision):
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{SOURCE}"], cwd=ROOT, text=True,
    )
    owner = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "ActionTimingCurve"
    )
    owner.name = "BaselineActionTimingCurve"
    namespace = dict(vars(timing_module))
    module = ast.Module(body=[
        ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0,
        ),
        owner,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), f"{revision}:{SOURCE}", "exec"), namespace)
    return namespace[owner.name], hashlib.sha256(source.encode()).hexdigest()


def recorded_curves(path, *, max_forecasts, max_curves):
    selected, signatures = [], set()
    prefix, bytes_read, forecasts = hashlib.sha256(), 0, 0
    with path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                break
            prefix.update(line)
            bytes_read += len(line)
            if b'"tool_wait_forecast"' not in line:
                continue
            row = json.loads(line)
            if row.get("event") != "tool_wait_forecast":
                continue
            forecasts += 1
            signature = (
                tuple(tuple(point) for point in row["release_cdf"]),
                tuple(tuple(point) for point in row.get("residual_duration_mass", ())),
                row.get("residual_support_detail"),
            )
            if signature[0] and signature not in signatures:
                signatures.add(signature)
                selected.append(row)
            if forecasts >= max_forecasts or len(selected) >= max_curves:
                break
    if not selected:
        raise ValueError("the selected prefix contains no tool CDF")
    return selected, {
        "path": str(path.resolve()), "read_prefix_bytes": bytes_read,
        "read_prefix_sha256": prefix.hexdigest(), "forecasts_seen": forecasts,
        "distinct_forecasts_selected": len(selected),
    }


def cases(rows, curve_class):
    curves, hints, queries = [], [], []
    for index, row in enumerate(rows):
        points = tuple(tuple(point) for point in row["release_cdf"])
        curve = curve_class(
            tuple(point[0] for point in points),
            tuple(point[1] for point in points), "pooled", 0.,
        )
        hint = NativeToolWaitHint(
            PrefillCandidateKey(
                f"fixture-{index}", row["workflow_id"], row["invocation_id"],
                row["context_id"], row["context_epoch"], 0, "fixture", 1,
            ),
            row["p10_ms"], row["p50_ms"], row["p90_ms"], 0., 1e12, "a" * 64,
            release_cdf=points,
            residual_duration_mass=tuple(
                tuple(point) for point in row.get("residual_duration_mass", ())
            ),
            residual_support_detail=row.get("residual_support_detail"),
        )
        hint.__dict__["_release_curve"] = curve
        ages = {
            0., 100., 500., 1000., row["p50_ms"], curve.tau_ms[-1] + 1.,
            *curve.tau_ms,
            *(max(0., value - 1e-6) for value in curve.tau_ms),
            *(value + 1e-6 for value in curve.tau_ms),
        }
        for age in sorted(ages):
            curves.append((curve, age))
            hints.append((hint, age))
            queries.append((index, age))
    return curves, hints, queries


def query_curve(items):
    return [curve.release_within(age) for curve, age in items]


def query_survival(items):
    return [
        (
            hint.remaining_quantile(.1, now_ms=age),
            hint.remaining_quantile(.5, now_ms=age),
            hint.remaining_quantile(.9, now_ms=age),
            hint.release_probability_within(500., now_ms=age),
            hint.release_probability_within(1000., now_ms=age),
        )
        for hint, age in items
    ]


def measure(variants, query, *, samples, iterations):
    expected = query(variants["baseline"])
    if query(variants["optimized"]) != expected:
        raise AssertionError("CDF or survival query output changed")
    costs = {name: {"cpu": [], "wall": []} for name in variants}
    for sample in range(samples + 5):
        order = list(variants)
        if sample % 2:
            order.reverse()
        for name in order:
            cpu_begin, wall_begin = time.thread_time_ns(), time.perf_counter_ns()
            for _ in range(iterations):
                output = query(variants[name])
            cpu_us = (time.thread_time_ns() - cpu_begin) / 1000. / iterations / len(output)
            wall_us = (time.perf_counter_ns() - wall_begin) / 1000. / iterations / len(output)
            if output != expected:
                raise AssertionError("query outputs changed during timing")
            if sample >= 5:
                costs[name]["cpu"].append(cpu_us)
                costs[name]["wall"].append(wall_us)
    summaries = {
        name: {
            "median_thread_cpu_us": median(values["cpu"]),
            "median_wall_us": median(values["wall"]),
            "sample_mean_thread_cpu_us": values["cpu"],
            "sample_mean_wall_us": values["wall"],
        } for name, values in costs.items()
    }
    return {
        "queries_per_iteration": len(expected), "outputs_equal": True,
        "costs_per_query": summaries,
        "median_thread_cpu_reduction_fraction": 1 - (
            summaries["optimized"]["median_thread_cpu_us"]
            / summaries["baseline"]["median_thread_cpu_us"]
        ),
        "query_output_sha256": hashlib.sha256(
            json.dumps(expected, allow_nan=False).encode()
        ).hexdigest(),
    }


def verify_duplicate_knots(original):
    count = 0
    for knots, probabilities in (
        ((1.,), (.5,)),
        ((1., 1., 2., 4., 4., 8.), (.1, .2, .2, .5, .5, .9)),
        ((1., 2., 4.), (.3, .3 - 5e-13, .9)),
    ):
        left = original(knots, probabilities, "pooled", 0.)
        right = timing_module.ActionTimingCurve(knots, probabilities, "pooled", 0.)
        for value in (-1., 0., *knots, *(point + 1e-6 for point in knots), 100.):
            assert left.release_within(value) == right.release_within(value)
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--forecast-log", type=Path, required=True)
    parser.add_argument("--max-forecasts", type=int, default=256)
    parser.add_argument("--max-curves", type=int, default=32)
    parser.add_argument("--samples", type=int, default=31)
    parser.add_argument("--iterations", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.max_forecasts, args.max_curves, args.samples, args.iterations) <= 0:
        parser.error("counts must be positive")
    original, source_hash = baseline_curve(args.baseline_revision)
    rows, evidence = recorded_curves(
        args.forecast_log, max_forecasts=args.max_forecasts, max_curves=args.max_curves,
    )
    baseline = cases(rows, original)
    optimized = cases(rows, timing_module.ActionTimingCurve)
    assert baseline[2] == optimized[2]
    assert Path(timing_module.__file__).resolve() == ROOT / SOURCE
    report = {
        "scope": (
            "CPU lookup in cached validated curves from an observed forecast prefix. "
            "Exact probability, remaining quantile and 500/1000ms window output equality. "
            "These are query fixtures, not workload frequencies, tool accuracy, "
            "physical prefetch feasibility, native allocator, DMA, GPU throughput or JCT."
        ),
        "baseline_revision": args.baseline_revision,
        "baseline_source_sha256": source_hash,
        "optimized_source_sha256": hashlib.sha256((ROOT / SOURCE).read_bytes()).hexdigest(),
        "benchmark_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "recorded_prefix": evidence,
        "curve_lengths": [len(row["release_cdf"]) for row in rows],
        "duplicate_knot_boundary_queries_equal": verify_duplicate_knots(original),
        "samples": args.samples, "iterations_per_sample": args.iterations,
        "warmup_samples": 5,
        "cases": {
            "cdf_lookup": measure(
                {"baseline": baseline[0], "optimized": optimized[0]}, query_curve,
                samples=args.samples, iterations=args.iterations,
            ),
            "native_survival_queries": measure(
                {"baseline": baseline[1], "optimized": optimized[1]}, query_survival,
                samples=args.samples, iterations=args.iterations,
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        name: {"outputs_equal": case["outputs_equal"],
               "thread_cpu_reduction_percent": 100 * case["median_thread_cpu_reduction_fraction"]}
        for name, case in report["cases"].items()
    }, indent=2))


if __name__ == "__main__":
    main()
