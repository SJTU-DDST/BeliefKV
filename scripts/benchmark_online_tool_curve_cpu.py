#!/usr/bin/env python3
"""Compare complete timing estimates across small and large survival histories."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import gc
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEventKind
from beliefkv.predictor.online_workload_timing import OnlineWorkloadToolTiming
from scripts.benchmark_long_chat_token_cache import source_binding
from scripts.benchmark_online_tool_lookup_cpu import baseline_class, tool_start
from scripts.benchmark_prefetch_control_cpu import distribution


def model_fixture(cls, count):
    model = cls(window=count)
    for index in range(count):
        model.observe(tool_start(("sandbox", "pytest"), "past", call=str(index)))
        model.observe(NS(
            kind=RuntimeEventKind.TOOL_END, workflow_id="cpu-probe",
            invocation_id="past", ts_ms=100. * (index + 1),
            attributes={"tool_run_id": str(index), "duration_ms": 100. * (index + 1)},
        ))
    model.observe(tool_start(("sandbox", "pytest"), "agent"))
    return model


def measure(classes, *, count, age, refit, samples, calls):
    models = {name: model_fixture(cls, count) for name, cls in classes.items()}
    expected = {}
    for name, model in models.items():
        value = model.estimate("cpu-probe", "agent", now_ms=age)
        expected[name] = asdict(value) if value is not None else None
    if expected["baseline"] != expected["candidate"]:
        raise AssertionError("complete timing prediction changed")
    costs = {name: {"cpu": [], "wall": []} for name in models}
    for sample in range(samples + 10):
        order = tuple(models) if sample % 2 else tuple(reversed(models))
        for name in order:
            model = models[name]
            cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
            for _ in range(calls):
                if refit:
                    model._curves.clear()
                value = model.estimate("cpu-probe", "agent", now_ms=age)
            elapsed_cpu = (time.thread_time_ns() - cpu) / 1000. / calls
            elapsed_wall = (time.perf_counter_ns() - wall) / 1000. / calls
            if (asdict(value) if value is not None else None) != expected[name]:
                raise AssertionError("prediction changed during timed calls")
            if sample >= 10:
                costs[name]["cpu"].append(elapsed_cpu)
                costs[name]["wall"].append(elapsed_wall)
    return {
        "history_samples": count, "elapsed_ms": age,
        "forced_refit_before_each_query": refit,
        "complete_prediction_equal": True,
        "costs": {
            name: {
                clock: {**distribution(values), "samples_us": values}
                for clock, values in clocks.items()
            }
            for name, clocks in costs.items()
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--samples", type=int, default=101)
    parser.add_argument("--calls", type=int, default=25)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or min(args.samples, args.calls) < 1 or not gc.isenabled():
        parser.error("a fresh output, positive samples/calls and normal GC are required")
    baseline, bindings = baseline_class(args.baseline)
    sources = [
        source_binding(ROOT / name) for name in (
            "beliefkv/predictor/tool_survival.py",
            "beliefkv/predictor/online_workload_timing.py",
            "scripts/benchmark_online_tool_lookup_cpu.py",
            "scripts/benchmark_online_tool_curve_cpu.py",
        )
    ]
    cases = [
        measure(
            {"baseline": baseline, "candidate": OnlineWorkloadToolTiming},
            count=count, age=age, refit=refit, samples=args.samples, calls=args.calls,
        )
        for count in (4, 8, 12, 16, 32, 64, 128)
        for age in (0., count * 50.) for refit in (False, True)
    ]
    if any(source_binding(Path(item["path"])) != item for item in sources):
        raise ValueError("benchmark source changed")
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "Complete CPU timing estimate with controlled distinct duration histories, "
            "alternating order, warmup and normal GC. Refit clears only the fitted "
            "curve cache to measure its rebuilding cost; it is not a measured "
            "history-update frequency. Cases are not workload weighted. "
            "No GPU, prediction-accuracy or JCT improvement is inferred."
        ),
        "baseline_revision": args.baseline, "baseline_source_sha256": bindings,
        "candidate_sources": sources, "normal_gc": gc.isenabled(),
        "samples_per_case": args.samples, "calls_per_sample": args.calls,
        "all_predictions_equal": True, "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "output": str(args.output), "cases": len(cases),
        "all_predictions_equal": True,
        "cached_prediction_cpu_p50_us": [{
            "history": case["history_samples"], "elapsed_ms": case["elapsed_ms"],
            **{name: clocks["cpu"]["p50_us"] for name, clocks in case["costs"].items()},
        } for case in cases if not case["forced_refit_before_each_query"]],
    }, indent=2))


if __name__ == "__main__":
    main()
