#!/usr/bin/env python3
"""Compare online tool timing queries using observed duration histories, CPU only."""

from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEventKind
from beliefkv.predictor.online_workload_timing import OnlineWorkloadToolTiming
from scripts.benchmark_long_chat_token_cache import source_binding
from scripts.benchmark_prefetch_control_cpu import distribution


def baseline_class(revision):
    modules, bindings = {}, {}
    for name in ("tool_survival", "online_workload_timing"):
        path = f"beliefkv/predictor/{name}.py"
        source = subprocess.check_output(
            ["git", "show", f"{revision}:{path}"], cwd=ROOT, text=True,
        )
        module = ModuleType(f"_baseline_cpu_{name}")
        sys.modules[module.__name__] = module
        exec(compile(source, f"{revision}:{path}", "exec"), vars(module))
        if name == "online_workload_timing":
            module.KaplanMeierCurve = modules["tool_survival"].KaplanMeierCurve
        modules[name] = module
        bindings[path] = hashlib.sha256(source.encode()).hexdigest()
    return modules["online_workload_timing"].OnlineWorkloadToolTiming, bindings


def tool_start(signature, invocation, *, call="active", start=0.):
    return SimpleNamespace(
        kind=RuntimeEventKind.TOOL_START, workflow_id="cpu-probe",
        invocation_id=invocation, ts_ms=start, attributes={
            "tool_run_id": call, "tool_name": "execute",
            "backend_class": signature[0], "observed_command_shape": signature[1],
        },
    )


def prediction_value(value):
    return asdict(value) if value is not None else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--baseline", default="6c40e43")
    parser.add_argument("--samples", type=int, default=51)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists():
        parser.error("use positive samples and a fresh output path")
    baseline, baseline_bindings = baseline_class(args.baseline)
    prototypes = {"baseline": baseline(), "candidate": OnlineWorkloadToolTiming()}
    relevant = {
        RuntimeEventKind.TOOL_START, RuntimeEventKind.TOOL_END, RuntimeEventKind.RETURN,
        RuntimeEventKind.INVOCATION_CANCEL, RuntimeEventKind.WORKFLOW_END,
    }
    events, sources = [], []
    for path in sorted(args.arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl")):
        sources.append(source_binding(path))
        with path.open() as stream:
            for line in stream:
                row = json.loads(line)
                kind = RuntimeEventKind(row["kind"])
                if kind in relevant:
                    events.append(SimpleNamespace(
                        kind=kind, workflow_id=row["workflow_id"],
                        invocation_id=row.get("invocation_id"), ts_ms=row["ts_ms"],
                        attributes=row.get("attributes") or {},
                    ))
    if not events:
        parser.error("no tool lifecycle events found")
    observe_cpu_ns = {name: 0 for name in prototypes}
    for event in sorted(events, key=lambda item: item.ts_ms):
        for name, model in prototypes.items():
            started = time.thread_time_ns()
            model.observe(event)
            observe_cpu_ns[name] += time.thread_time_ns() - started
    if (
        prototypes["baseline"].open != prototypes["candidate"].open
        or prototypes["baseline"].samples != prototypes["candidate"].samples
    ):
        raise AssertionError("observed history or active tool identities changed")
    supported = [
        (key[1:], list(history))
        for key, history in prototypes["baseline"].samples.items()
        if key[0] == "*" and sum(completed for _, completed in history) >= 8
    ]
    supported.sort(key=lambda item: (-len(item[1]), item[0]))
    cases = []
    for signature, history in supported[:3]:
        complete = sorted(duration for duration, completed in history if completed)
        p90 = complete[min(len(complete) - 1, int(.9 * len(complete)))]
        for active_count in (1, 24, 96, 256):
            for elapsed in (0., .5 * statistics.median(complete), .9 * p90):
                models = {name: copy.deepcopy(model) for name, model in prototypes.items()}
                for model in models.values():
                    for index in range(active_count):
                        model.observe(tool_start(signature, f"agent-{index}"))
                    model.estimate("cpu-probe", "agent-0", now_ms=elapsed)
                costs = {name: [] for name in models}
                observed = {}
                for sample in range(args.samples):
                    names = list(models)
                    if sample % 2:
                        names.reverse()
                    for name in names:
                        started = time.thread_time_ns()
                        value = models[name].estimate("cpu-probe", "agent-0", now_ms=elapsed)
                        costs[name].append((time.thread_time_ns() - started) / 1000.)
                        observed[name] = prediction_value(value)
                    if observed["baseline"] != observed["candidate"]:
                        raise AssertionError("quantiles, CDF, scope or sample support changed")
                for model in models.values():
                    model.observe(tool_start(signature, "new-observation", call="new"))
                    model.observe(SimpleNamespace(
                        kind=RuntimeEventKind.TOOL_END, workflow_id="cpu-probe",
                        invocation_id="new-observation", ts_ms=complete[-1],
                        attributes={"tool_run_id": "new", "duration_ms": complete[-1]},
                    ))
                updated = {
                    name: prediction_value(model.estimate("cpu-probe", "agent-0", now_ms=elapsed))
                    for name, model in models.items()
                }
                if updated["baseline"] != updated["candidate"]:
                    raise AssertionError("history update failed to invalidate a fitted curve")
                cases.append({
                    "signature": signature, "observed_history_samples": len(history),
                    "active_tools": active_count, "elapsed_ms": elapsed,
                    "supported": observed["candidate"] is not None,
                    "costs": {name: distribution(values) for name, values in costs.items()},
                    "all_predictions_equal": True, "post_update_prediction_equal": True,
                })
    if not cases:
        parser.error("no observed signatures have enough completed samples")
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "CPU only. Replay observed completed/censored tool histories, then measure repeated "
            "queries with controlled active-tool counts and elapsed times. "
            "Populations/frequencies are not an observed workload distribution. "
            "No accuracy, GPU, prefetch coverage, throughput or JCT claim."
        ),
        "sources": sources, "replayed_lifecycle_events": len(events),
        "replay_observe_thread_cpu_ms": {
            name: cost / 1e6 for name, cost in observe_cpu_ns.items()
        },
        "baseline_revision": args.baseline, "baseline_source_sha256": baseline_bindings,
        "candidate_sources": [
            source_binding(ROOT / path) for path in (
                "beliefkv/predictor/tool_survival.py",
                "beliefkv/predictor/online_workload_timing.py",
            )
        ],
        "benchmark_source": source_binding(Path(__file__)), "samples_per_case": args.samples,
        "all_predictions_equal": True, "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "replayed_lifecycle_events": len(events), "case_count": len(cases),
        "all_predictions_equal": True,
        "replay_observe_thread_cpu_ms": report["replay_observe_thread_cpu_ms"],
        "p50_us_by_active_tools": {
            str(count): {
                name: statistics.median(
                    row["costs"][name]["p50_us"] for row in cases if row["active_tools"] == count
                ) for name in models
            } for count in (1, 24, 96, 256)
        },
    }, indent=2))


if __name__ == "__main__":
    main()
