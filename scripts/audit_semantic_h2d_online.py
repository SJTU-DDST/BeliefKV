#!/usr/bin/env python3
"""Request-grouped online forecast diagnostics, not independent model validation."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.summarize_semantic_h2d_ab import records


def error_summary(rows: list[tuple[float, int, float]]) -> dict:
    return {
        "request_count": len(rows),
        "median_signed_error_tokens": median(row[0] for row in rows) if rows else None,
        "median_absolute_error_tokens": median(abs(row[0]) for row in rows) if rows else None,
        "median_actual_remaining_tokens": median(row[1] for row in rows) if rows else None,
        "median_forecast_remaining_tokens": median(row[2] for row in rows) if rows else None,
    }


def audit(arm: Path, threshold: float) -> dict:
    clients = list(arm.glob("client_*/summary.json"))
    if len(clients) != 1:
        raise ValueError("audit requires one terminal workload summary")
    children, terminals, last_result = set(), set(), {}
    for path in sorted((clients[0].parent / "workflows").glob("*/runtime_events.deepagents.jsonl")):
        for row in records(path):
            attrs = row.get("attributes") or {}
            invocation = row.get("invocation_id")
            if row["kind"] == "invocation_create" and attrs.get("source") == "deepagents_task":
                children.add(invocation)
            elif row["kind"] == "llm_result" and not attrs.get("runtime_internal"):
                if attrs.get("request_id"):
                    last_result[invocation] = attrs["request_id"]
            elif (
                row["kind"] == "return"
                and attrs.get("source") == "deepagents_task"
                and attrs.get("outcome") == "completed"
                and invocation in last_result
            ):
                terminals.add(last_result[invocation])
    native = {
        row["attributes"]["request_id"]: row
        for row in records(arm / "server/runtime_events.sglang.jsonl")
        if row["kind"] == "llm_result"
        and type(row["attributes"].get("output_tokens")) is int
    }
    forecasts = defaultdict(list)
    starts = []
    for row in records(arm / "opportunities/admission_opportunities.jsonl"):
        if row["event"] == "semantic_child_forecast":
            forecasts[row["request_id"]].append(row)
        elif row["event"] == "final_stage_latest_start":
            starts.append(row)
    selected = {
        rid: match for rid, rows in forecasts.items()
        if (match := next((row for row in rows if row["score"] >= threshold), None))
        is not None
    }
    work_errors = {}
    for name in (
        "first_threshold_crossing", "last_accepted_snapshot",
        "last_pre_native_result_snapshot",
    ):
        errors = []
        for rid in terminals & forecasts.keys() & native.keys():
            if name == "first_threshold_crossing":
                snapshot = selected.get(rid)
            elif name == "last_pre_native_result_snapshot":
                snapshot = next((
                    row for row in reversed(forecasts[rid])
                    if row.get("ts_ms", float("inf")) < native[rid]["ts_ms"]
                ), None)
            else:
                snapshot = forecasts[rid][-1]
            if snapshot is None:
                continue
            actual = native[rid]["attributes"]["output_tokens"] - snapshot["observed_output_tokens"]
            if actual < 0:
                continue
            prediction = snapshot["remaining_tokens"]
            errors.append((prediction - actual, actual, prediction))
        work_errors[name] = error_summary(errors)
        work_errors[name]["within_100_actual_remaining_tokens"] = error_summary(
            [row for row in errors if row[1] <= 100]
        )
    before_eos, after_eos, unknown_eos = 0, 0, 0
    for row in starts:
        result = native.get(row["child_request_id"])
        if result is None:
            unknown_eos += 1
        elif row["ts_ms"] < result["ts_ms"]:
            before_eos += 1
        else:
            after_eos += 1
    return {
        "scope": "development diagnostics; not held-out validation or transfer benefit",
        "aggregation": "one first crossing and one last snapshot per request",
        "threshold": threshold,
        "natural_child_created_count": len(children),
        "natural_return_request_count": len(terminals),
        "forecast_request_count": len(forecasts),
        "first_crossing_request_count": len(selected),
        "first_crossing_natural_final_count": len(selected.keys() & terminals),
        "first_crossing_other_count": len(selected.keys() - terminals),
        "work_errors": work_errors,
        "work_error_interpretation": (
            "The last accepted snapshot may arrive after native EOS and observe "
            "zero remaining work. Use the separate pre-native-result series "
            "to assess work prediction before generation ends; neither series "
            "is child RETURN wall-clock accuracy."
        ),
        "latest_start_count": len(starts),
        "latest_start_before_native_result_count": before_eos,
        "latest_start_at_or_after_native_result_count": after_eos,
        "latest_start_missing_native_result_count": unknown_eos,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads((args.artifact.resolve().parent / "report.json").read_text())
    threshold = report["calibration"]["semantic_event"]["request_operating_point"]["threshold"]
    if type(threshold) not in (int, float) or not 0 <= threshold <= 1:
        raise ValueError("invalid frozen model threshold")
    result = audit(args.arm, float(threshold))
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
