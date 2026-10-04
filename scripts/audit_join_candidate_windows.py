#!/usr/bin/env python3
"""Intersect sampled JOIN restore targets with pre-EOS child forecasts."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.summarize_semantic_h2d_ab import records


def audit(arm: Path, threshold: float, *, sample_max_age_ms: float = 1500.) -> dict:
    opportunities, forecasts = defaultdict(list), defaultdict(list)
    clock = None
    for row in records(arm / "opportunities/admission_opportunities.jsonl"):
        if row["event"] == "safe_point_census" and clock is None:
            clock = row["ts_ms"] - row["monotonic_ms"]
        elif row["event"] == "session_h2d_opportunity":
            if row.get("invocation_state") == "wait_join":
                opportunities[row["invocation_id"]].append(row)
        elif row["event"] == "semantic_child_forecast":
            forecasts[row["request_id"]].append(row)
    if clock is None:
        raise ValueError("missing paired scheduler wall/monotonic clock")
    native = {
        row["attributes"]["request_id"]: row
        for row in records(arm / "server/runtime_events.sglang.jsonl")
        if row["kind"] == "llm_result" and row.get("attributes", {}).get("request_id")
    }
    for group in (*opportunities.values(), *forecasts.values()):
        group.sort(key=lambda row: row["ts_ms"])

    rows = []
    for path in sorted(arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl")):
        joins, last_requests, returns, submitted = {}, {}, {}, {}
        internal_calls = defaultdict(list)
        for row in records(path):
            attrs = row.get("attributes") or {}
            join_id, invocation = row.get("join_id"), row.get("invocation_id")
            if row["kind"] == "join_create":
                joins[join_id] = {
                    "members": row["member_invocation_ids"],
                    "mode": attrs.get("mode"),
                }
            elif row["kind"] == "join_wait" and join_id in joins:
                joins[join_id].update(parent=invocation, start=row["ts_ms"] + clock)
            elif row["kind"] == "join_satisfied" and join_id in joins:
                joins[join_id]["end"] = row["ts_ms"] + clock
            elif row["kind"] == "llm_submit" and not attrs.get("runtime_internal"):
                if attrs.get("request_id"):
                    submitted[attrs["request_id"]] = row["ts_ms"] + clock
            elif row["kind"] == "llm_result" and not attrs.get("runtime_internal"):
                if attrs.get("request_id"):
                    last_requests[invocation] = attrs["request_id"]
            elif row["kind"] == "call" and attrs.get("runtime_internal"):
                internal_calls[invocation].append(row["ts_ms"] + clock)
            elif (
                row["kind"] == "return" and attrs.get("source") == "deepagents_task"
                and attrs.get("outcome") == "completed" and invocation in last_requests
            ):
                returns[invocation] = (row["ts_ms"] + clock, last_requests[invocation])

        for join_id, join in joins.items():
            if not (
                join.get("mode") == "all" and "start" in join and "end" in join
                and join["members"] and all(child in returns for child in join["members"])
            ):
                continue
            child = max(join["members"], key=lambda member: returns[member][0])
            return_ms, rid = returns[child]
            result = native.get(rid)
            if result is None or result.get("invocation_id") != child:
                continue
            eos_ms, start_ms = result["ts_ms"], submitted.get(rid)
            target_samples = [
                row for row in opportunities.get(join["parent"], ())
                if join["start"] <= row["ts_ms"] < return_ms
            ]
            times = [row["ts_ms"] for row in target_samples]
            before_eos = [
                row for row in target_samples
                if row.get("node_id") is not None and row["ts_ms"] < eos_ms
            ]
            final_targets = [
                row for row in before_eos
                if start_ms is not None and row["ts_ms"] >= start_ms
            ]
            accepted = [row for row in forecasts.get(rid, ()) if row["ts_ms"] < eos_ms]
            overlapping_calls = [
                ts for ts in internal_calls[join["parent"]]
                if join["start"] <= ts < return_ms
            ]
            intersections = []
            for forecast in accepted:
                index = bisect_right(times, forecast["ts_ms"]) - 1
                if index < 0:
                    continue
                sample = target_samples[index]
                if (
                    sample.get("node_id") is None
                    or forecast["ts_ms"] - sample["ts_ms"] > sample_max_age_ms
                ):
                    continue
                intersections.append({
                    "forecast_ts_ms": forecast["ts_ms"],
                    "sample_age_ms": forecast["ts_ms"] - sample["ts_ms"],
                    "fits_sampled_free_lists": sample.get("fits_current_free_lists"),
                    "score": forecast["score"],
                    "remaining_tokens": forecast["remaining_tokens"],
                    "lead_to_child_return_ms": return_ms - forecast["ts_ms"],
                    "lead_to_native_eos_ms": eos_ms - forecast["ts_ms"],
                    "observation_age_ms": forecast.get("observation_age_ms"),
                })
            rows.append({
                "task": path.parent.name, "join_id": join_id,
                "child_invocation_id": child, "final_request_id": rid,
                "child_return_ts_ms": return_ms, "native_eos_ts_ms": eos_ms,
                "final_request_submit_ts_ms": start_ms,
                "eos_to_return_ms": return_ms - eos_ms,
                "internal_calls_on_waiting_parent": len(overlapping_calls),
                "last_restore_sample_to_final_submit_ms": (
                    start_ms - before_eos[-1]["ts_ms"]
                    if start_ms is not None and before_eos else None
                ),
                "wait_join_sample_count": len(target_samples),
                "restore_target_samples_before_eos": len(before_eos),
                "restore_target_samples_during_final_request": len(final_targets),
                "capacity_fit_samples_during_final_request": sum(
                    row.get("fits_current_free_lists") is True for row in final_targets
                ),
                "accepted_forecasts_before_eos": len(accepted),
                "sampled_target_forecast_intersections": len(intersections),
                "sampled_fit_phase_intersections": sum(
                    row["fits_sampled_free_lists"] is True and row["score"] >= threshold
                    for row in intersections
                ),
                "reason_counts": dict(Counter(row["reason"] for row in target_samples)),
                "last_pre_eos_forecast_with_sampled_target": (
                    intersections[-1] if intersections else None
                ),
            })
    return {
        "scope": "development window audit, not a transfer permit or continuous residency proof",
        "clock": "paired scheduler wall/monotonic clock, client/server on same host",
        "sample_max_age_ms": sample_max_age_ms, "phase_threshold": threshold,
        "complete_all_join_count": len(rows),
        "joins_with_restore_target_before_eos": sum(
            row["restore_target_samples_before_eos"] > 0 for row in rows
        ),
        "joins_with_restore_target_during_final_request": sum(
            row["restore_target_samples_during_final_request"] > 0 for row in rows
        ),
        "joins_with_sampled_fit_phase_intersection": sum(
            row["sampled_fit_phase_intersections"] > 0 for row in rows
        ),
        "joins_with_internal_calls_on_waiting_parent": sum(
            row["internal_calls_on_waiting_parent"] > 0 for row in rows
        ),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--threshold", type=float)
    source.add_argument("--artifact", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    threshold = args.threshold
    if args.artifact is not None:
        report = json.loads((args.artifact.resolve().parent / "report.json").read_text())
        threshold = report["calibration"]["semantic_event"]["request_operating_point"]["threshold"]
    if type(threshold) not in (int, float) or not 0 <= threshold <= 1:
        parser.error("phase threshold must be a probability")
    result = audit(args.arm, threshold)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}, indent=2))


if __name__ == "__main__":
    main()
