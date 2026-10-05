#!/usr/bin/env python3
"""Replay recorded tool forecasts against sampled, not continuous, H2D targets."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beliefkv.predictor.action_frontier import ActionTimingCurve
from scripts.summarize_semantic_h2d_ab import records


def probability(row, when_mono, horizon=1000.):
    points = row.get("release_cdf") or ()
    if not points:
        return None
    curve = ActionTimingCurve(
        tuple(point[0] for point in points), tuple(point[1] for point in points), "pooled", 0.,
    )
    age = max(0., when_mono - row["observed_monotonic_ms"])
    past = curve.release_within(age)
    return max(0., min(1., (curve.release_within(age + horizon) - past) / max(1. - past, 1e-9)))


def audit(arm):
    forecasts, opportunities, parked = defaultdict(list), [], []
    offset = None
    samples = []
    for row in records(arm / "opportunities/admission_opportunities.jsonl"):
        kind = row["event"]
        if kind == "safe_point_census":
            if offset is None:
                offset = row["ts_ms"] - row["monotonic_ms"]
        elif kind == "tool_wait_forecast":
            forecasts[(row["context_id"], row["context_epoch"])].append(row)
        elif kind == "session_h2d_opportunity" and row.get("invocation_state") == "wait_tool":
            opportunities.append(row)
        elif kind == "parent_pressure_demoted" and row.get("source") == "tool_wait":
            parked.append(row)
    if offset is None:
        raise ValueError("no paired scheduler clock")
    indices = {
        key: [row["ts_ms"] for row in group] for key, group in forecasts.items()
    }
    counters = Counter()
    for op in opportunities:
        counters["tool_opportunity_samples"] += 1
        if op.get("node_id") is None:
            counters[op.get("reason", "unknown")] += 1
            continue
        counters["host_only_target_samples"] += 1
        counters["capacity_fit_target_samples"] += op.get("fits_current_free_lists") is True
        key = (op["context_id"], op["context_epoch"])
        index = bisect_right(indices.get(key, []), op["ts_ms"]) - 1
        if index < 0:
            counters["target_without_prior_forecast"] += 1
            continue
        hint = forecasts[key][index]
        now = op["ts_ms"] - offset
        age = now - hint["observed_monotonic_ms"]
        if not 0 <= age < 5000:
            counters["target_with_expired_forecast"] += 1
            continue
        p = probability(hint, now)
        mid = max(0., hint["p50_ms"] - age)
        ready = p >= .8 if p is not None else mid <= 1000
        counters["target_with_live_forecast"] += 1
        counters["target_ready_by_cdf"] += bool(ready)
        counters["target_ready_by_median"] += mid <= 1000
        counters["median_ready_but_cdf_rejected"] += mid <= 1000 and not ready
        samples.append({
            "context_id": key[0], "context_epoch": key[1],
            "opportunity_ts_ms": op["ts_ms"],
            "node_id": op["node_id"], "fits_sampled_capacity": op.get("fits_current_free_lists"),
            "forecast_age_ms": age, "remaining_p50_ms": mid,
            "release_within_1000ms": p, "current_policy_ready": ready,
        })
    latest = []
    ends = defaultdict(list)
    for path in arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl"):
        contexts = {}
        for event in records(path):
            if event["kind"] == "llm_submit" and event.get("context_id"):
                contexts[event["invocation_id"]] = (event["context_id"], event["context_epoch"])
            if event["kind"] == "tool_end":
                key = contexts.get(event["invocation_id"])
                if key is not None:
                    ends[key].append(event["ts_ms"] + offset)
    for key, group in forecasts.items():
        for end in ends.get(key, ()):
            index = bisect_right(indices[key], end) - 1
            if index >= 0:
                hint = group[index]
                age = end - offset - hint["observed_monotonic_ms"]
                if 0 <= age < 5000:
                    latest.append(probability(hint, end - offset))
    return {
        "scope": "sampled development replay; not continuous physical authorization",
        "counts": dict(counters), "pressure_parked_count": len(parked),
        "eligible_target_context_count": len({row["context_id"] for row in samples}),
        "last_pre_tool_end_cdf_count": len(latest),
        "last_pre_tool_end_cdf_ge_80_count": sum(p is not None and p >= .8 for p in latest),
        "target_samples": samples, "pressure_parked": parked,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.arm)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in ("target_samples", "pressure_parked")}, indent=2))


if __name__ == "__main__":
    main()
