"""Reproduce the overview's conditional survival curves on held-out TraceLab calls."""

import argparse
import gzip
import hashlib
import json
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from types import SimpleNamespace

from beliefkv.core.events import RuntimeEventKind
from beliefkv.experiments.tool_timing_overlay import fingerprint
from beliefkv.predictor.online_workload_timing import OnlineWorkloadToolTiming
from beliefkv.predictor.tool_timing_prior import ToolTimingPrior


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "experiments/reports/v331_tracelab_prior_reproduction_cpu_20261027.json"
OUTPUT = Path(__file__).parent / "assets/migragate_tool_survival.json"
AGE = 1000
HORIZONS = list(range(0, 40001, 500))
ALPHAS = (.1, .3, .5)


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000


def replay(trace):
    report = json.loads(REPORT.read_text())
    prior_file = REPORT.with_suffix(".prior.json")
    prior = ToolTimingPrior.load(prior_file, report["offline_prior"]["sha256"])
    artifact = json.loads(prior_file.read_text())
    held = set(artifact["evaluation_groups"])
    assert not held.intersection(artifact["training_groups"])
    assert hashlib.sha256(trace.read_bytes()).hexdigest() == report["public_source"]["sha256"]
    calls = {}
    with gzip.open(trace, "rt") as rows:
        for line in rows:
            row = json.loads(line)
            if row["provider"] != "claude":
                continue
            group = fingerprint([row["provider"], row["session_id"]])
            if group not in held:
                continue
            for tool in row.get("tools") or ():
                category, duration = tool["tool_name"], tool.get("tool_internal_latency_ms")
                if category not in artifact["held_out_evaluation"] or duration is None:
                    continue
                key = fingerprint([row["provider"], row["session_id"], tool["tool_call_id"]])
                start = timestamp(tool["emitted_at"])
                end = timestamp(tool["result_at"])
                calls.setdefault(key, (category, duration, group, start, end))

    timing = OnlineWorkloadToolTiming(prior=prior)
    # Delay each history update until the result is visible and the recorded duration has elapsed.
    events = []
    for key, (_, duration, _, start, end) in calls.items():
        events.extend(((start, 0, key), (max(end, start+duration), 2, key)))
        if duration > AGE:
            events.append((start+AGE, 1, key))
    curves, observed, sources = defaultdict(list), defaultdict(list), Counter()
    point_errors, ewma = defaultdict(list), {}
    for category in artifact["held_out_evaluation"]:
        curve = prior.curves[(artifact["domain"], category)]
        last, average = 1., 0.
        for duration, survival in curve._steps:
            average += duration * (last-survival)
            last = survival
        for alpha in ALPHAS:
            ewma[category, alpha] = average
    for time, op, key in sorted(events):
        category, duration, group, _, _ = calls[key]
        attrs = dict(tool_name=category, observed_timing_class=category, timing_domain=artifact["domain"],
                     tool_run_id=key, duration_ms=duration) if op == 2 else dict(
                         tool_name=category, observed_timing_class=category, timing_domain=artifact["domain"],
                         tool_run_id=key)
        if op != 1:
            timing.observe(SimpleNamespace(kind=(RuntimeEventKind.TOOL_END if op == 2 else RuntimeEventKind.TOOL_START),
                                           workflow_id=group, invocation_id=key, ts_ms=time, attributes=attrs))
            if op == 2:
                for alpha in ALPHAS:
                    ewma[category, alpha] += alpha*(duration-ewma[category, alpha])
            continue
        forecast = timing.estimate(group, key, now_ms=time)
        assert forecast is not None
        sources[forecast.scope] += 1
        times, cdf = zip(*forecast.release_cdf)
        curves[category].append([1.-(cdf[i-1] if (i := bisect_right(times, h)) else 0.)
                                 for h in HORIZONS])
        observed[category].append(duration-AGE)
        point_errors["MigraGate conditional median"].append(abs(forecast.p50_ms-(duration-AGE)))
        for alpha in ALPHAS:
            point_errors[f"type EWMA alpha={alpha}"].append(abs(max(0., ewma[category,alpha]-AGE)-(duration-AGE)))
    result = {}
    for cat, rows in curves.items():
        assert sum(v[0] == cat for v in calls.values()) == artifact["held_out_evaluation"][cat]["held_out_calls"]
        result[cat] = dict(survivors=len(rows), predicted=[mean(p) for p in zip(*rows)],
                           observed=[mean(t > h for t in observed[cat]) for h in HORIZONS])
    return dict(source="TraceLab v0.0.1, SyFI Lab, CC-BY-4.0", source_sha256=report["public_source"]["sha256"],
                train_sessions=len(artifact["training_groups"]), heldout_sessions=len(held), heldout_calls=len(calls),
                elapsed_ms=AGE, horizons_ms=HORIZONS, categories=result, history_sources=dict(sources),
                point_mae_seconds={k: mean(v)/1000 for k,v in point_errors.items()},
                comparison="Per-type EWMA sensitivity, initialized with the training-category mean, "
                           "updated at result availability, no user hint. Point comparison only. "
                           "Forecasts use internal tool durations and observed result clocks.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tracelab_gz", type=Path)
    result = replay(parser.parse_args().tracelab_gz)
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n")
    print(result["point_mae_seconds"], result["history_sources"])
