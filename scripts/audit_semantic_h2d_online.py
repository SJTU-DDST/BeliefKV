#!/usr/bin/env python3
"""Request-grouped online forecast diagnostics, not independent model validation."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import defaultdict
import json
import math
from pathlib import Path
from statistics import median
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def snapshot_records(path: Path, *, allow_partial: bool):
    """Read a fixed byte snapshot without treating a live trailing row as corruption."""
    if not path.exists():
        return
    with path.open("rb") as stream:
        limit = path.stat().st_size
        while stream.tell() < limit:
            line = stream.readline(limit - stream.tell())
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                if not allow_partial or stream.tell() != limit or line.endswith(b"\n"):
                    raise


def error_summary(rows: list[tuple[float, int, float]]) -> dict:
    return {
        "request_count": len(rows),
        "median_signed_error_tokens": median(row[0] for row in rows) if rows else None,
        "median_absolute_error_tokens": median(abs(row[0]) for row in rows) if rows else None,
        "median_actual_remaining_tokens": median(row[1] for row in rows) if rows else None,
        "median_forecast_remaining_tokens": median(row[2] for row in rows) if rows else None,
    }


def trigger_diagnostics(
    starts: list[dict], forecasts: dict, native: dict, terminals: set,
    return_times: dict, clock_offset_ms: float | None,
) -> dict:
    grouped = defaultdict(list)
    for start in starts:
        if start.get("trigger_kind") == "estimated_work":
            grouped[start["child_request_id"]].append(start)
    rows = []
    for rid, group in sorted(grouped.items()):
        start = min(group, key=lambda row: row["ts_ms"])
        history = sorted(
            (row for row in forecasts.get(rid, ()) if type(row.get("ts_ms")) in (int, float)),
            key=lambda row: row["ts_ms"],
        )
        index = bisect_right([row["ts_ms"] for row in history], start["ts_ms"]) - 1
        snapshot = history[index] if index >= 0 else None
        if snapshot is not None and any(
            start.get(trigger_key) is not None
            and not math.isclose(start[trigger_key], snapshot[forecast_key], rel_tol=1e-9)
            for trigger_key, forecast_key in (
                ("forecast_center_tokens", "remaining_tokens"),
                ("forecast_upper_tokens", "upper_tokens"),
            )
        ):
            snapshot = None
        result = native.get(rid)
        generated = start.get("generated_tokens")
        actual = (
            result["attributes"]["output_tokens"] - generated
            if result is not None and type(generated) is int else None
        )
        eos_lead = result["ts_ms"] - start["ts_ms"] if result is not None else None
        return_lead = (
            return_times[rid] + clock_offset_ms - start["ts_ms"]
            if rid in return_times and clock_offset_ms is not None else None
        )
        advanced, predicted, rate = None, None, None
        if snapshot is not None and type(generated) is int:
            advanced = max(0, generated - snapshot["observed_output_tokens"])
            statistic = start.get("effective_work_statistic", start.get("work_statistic"))
            work = (
                snapshot["upper_tokens"] if statistic in ("upper", "upper_after_center_overrun")
                else snapshot["remaining_tokens"]
            )
            predicted = max(1., work - advanced)
            if start.get("remaining_ms", 0) > 0:
                rate = predicted * 1000 / start["remaining_ms"]
        valid_work = actual is not None and actual >= 0 and predicted is not None
        actual_at_rate = actual * 1000 / rate if valid_work and rate else None
        rows.append({
            "request_id": rid, "workflow_id": start.get("workflow_id"),
            "first_trigger_ts_ms": start["ts_ms"],
            "trigger_count": len(group), "natural_return_observed": rid in terminals,
            "causal_forecast_matched": snapshot is not None,
            "forecast_delivery_to_trigger_ms": (
                start["ts_ms"] - snapshot["ts_ms"] if snapshot is not None else None
            ),
            "forecast_observation_age_at_trigger_ms": (
                start["ts_ms"] - snapshot["ts_ms"] + snapshot["observation_age_ms"]
                if snapshot is not None and snapshot.get("observation_age_ms") is not None else None
            ),
            "observed_output_tokens": (
                snapshot["observed_output_tokens"] if snapshot is not None else None
            ),
            "generated_tokens": generated,
            "advanced_since_forecast_tokens": advanced,
            "forecast_center_tokens": (
                snapshot["remaining_tokens"] if snapshot is not None else None
            ),
            "forecast_upper_tokens": (
                snapshot.get("upper_tokens") if snapshot is not None else None
            ),
            "effective_work_statistic": start.get("effective_work_statistic"),
            "projected_remaining_tokens": predicted,
            "actual_remaining_tokens": actual,
            "signed_work_error_tokens": predicted - actual if valid_work else None,
            "predicted_remaining_ms": start.get("remaining_ms"),
            "trigger_tokens_per_second": rate,
            "actual_remaining_tokens_per_second": (
                actual * 1000 / eos_lead if actual is not None and actual > 0
                and eos_lead is not None and eos_lead > 0 else None
            ),
            "lead_to_native_eos_ms": eos_lead, "lead_to_child_return_ms": return_lead,
            "signed_eos_time_error_ms": (
                start["remaining_ms"] - eos_lead
                if start.get("remaining_ms") is not None and eos_lead is not None else None
            ),
            "work_error_at_trigger_rate_ms": (
                (predicted - actual) * 1000 / rate if valid_work and rate else None
            ),
            "actual_work_at_trigger_rate_ms": actual_at_rate,
            "remaining_service_rate_gap_ms": (
                eos_lead - actual_at_rate if actual_at_rate is not None and eos_lead is not None else None
            ),
        })
    known = [row for row in rows if row["lead_to_native_eos_ms"] is not None]
    errors = [
        (row["signed_work_error_tokens"], row["actual_remaining_tokens"],
         row["projected_remaining_tokens"])
        for row in rows if row["signed_work_error_tokens"] is not None
    ]
    return {
        "aggregation": "first estimated-work trigger per child request, not per restored node",
        "request_count": len(rows),
        "missing_native_result_count": len(rows) - len(known),
        "matched_causal_forecast_count": sum(row["causal_forecast_matched"] for row in rows),
        "lead_0_to_500ms_count": sum(0 < row["lead_to_native_eos_ms"] <= 500 for row in known),
        "lead_500_to_2000ms_count": sum(500 < row["lead_to_native_eos_ms"] <= 2000 for row in known),
        "lead_over_2000ms_count": sum(row["lead_to_native_eos_ms"] > 2000 for row in known),
        "at_or_after_native_eos_count": sum(row["lead_to_native_eos_ms"] <= 0 for row in known),
        "work_error_summary": error_summary(errors),
        "interpretation": (
            "Native EOS and child RETURN are evaluation labels only. Work error "
            "at the trigger rate and the remaining rate gap decompose the EOS "
            "time error algebraically; the rate gap is not measured GPU queue time. "
            "Client RETURN uses the first census wall-minus-monotonic clock offset; "
            "missing clocks or labels remain unknown. A future forecast is never matched."
        ),
        "rows": rows,
    }


def sampled_trigger_replay(forecasts: dict, native: dict, terminals: set, threshold: float) -> dict:
    comparisons = {}
    for policy in ("progress_countdown", "snapshot_center"):
        comparisons[policy] = {}
        for horizon in (100., 250., 500.):
            selected = []
            for rid, history in forecasts.items():
                for snapshot in sorted(history, key=lambda row: row.get("ts_ms", math.inf)):
                    rate = snapshot.get("sampled_tokens_per_second")
                    generated = snapshot.get("current_output_tokens")
                    if (
                        snapshot["score"] < threshold or not snapshot.get("notice_active")
                        or type(generated) is not int or generated < 16
                        or not rate or not 0 <= snapshot.get("observation_age_ms", math.inf) <= 1500
                        or type(snapshot.get("ts_ms")) not in (int, float)
                    ):
                        continue
                    work = snapshot["remaining_tokens"]
                    if policy == "progress_countdown":
                        advanced = max(0, generated - snapshot["observed_output_tokens"])
                        if work <= advanced:
                            work = snapshot.get("upper_tokens", 0)
                        if work <= advanced:
                            continue
                        work -= advanced
                    predicted_ms = max(1., work) * 1000 / rate
                    if predicted_ms > horizon:
                        continue
                    result = native.get(rid)
                    lead = result["ts_ms"] - snapshot["ts_ms"] if result is not None else None
                    selected.append({
                        "request_id": rid, "ts_ms": snapshot["ts_ms"],
                        "predicted_ms": predicted_ms, "lead_to_native_eos_ms": lead,
                        "natural_return_observed": rid in terminals,
                    })
                    break
            known = [row for row in selected if row["lead_to_native_eos_ms"] is not None]
            comparisons[policy][str(int(horizon))] = {
                "first_trigger_count": len(selected),
                "natural_return_observed_count": sum(row["natural_return_observed"] for row in selected),
                "missing_native_result_count": len(selected) - len(known),
                "lead_0_to_500ms_count": sum(0 < row["lead_to_native_eos_ms"] <= 500 for row in known),
                "lead_over_2000ms_count": sum(row["lead_to_native_eos_ms"] > 2000 for row in known),
                "at_or_after_native_eos_count": sum(row["lead_to_native_eos_ms"] <= 0 for row in known),
                "median_lead_to_native_eos_ms": median(
                    row["lead_to_native_eos_ms"] for row in known
                ) if known else None,
                "first_trigger_rows": selected,
            }
    return {
        "scope": (
            "Causal replay at forecast delivery only, not continuous scheduler ticks. "
            "No physical capacity, target lifetime or transfer check; no new inference. "
            "Snapshot center retains the old observation's center rather than treating "
            "it as a deterministic endpoint. EOS/RETURN labels never select a trigger. "
            "Coverage and premature triggers must both be reported."
        ),
        "policies": comparisons,
    }


def audit(arm: Path, threshold: float, *, allow_partial: bool = False) -> dict:
    started_ms = time.time() * 1000
    clients = [path for path in arm.glob("client_*") if path.is_dir()]
    if len(clients) != 1 or not allow_partial and not (clients[0] / "summary.json").is_file():
        raise ValueError("audit requires one terminal workload summary; use allow_partial for a live run")
    complete = (clients[0] / "summary.json").is_file()
    children, terminals, last_result, nonterminal = set(), set(), {}, set()
    return_times = {}
    requests_by_invocation = defaultdict(set)
    for path in sorted((clients[0] / "workflows").glob("*/runtime_events.deepagents.jsonl")):
        for row in snapshot_records(path, allow_partial=allow_partial):
            attrs = row.get("attributes") or {}
            invocation = row.get("invocation_id")
            if row["kind"] == "invocation_create" and attrs.get("source") == "deepagents_task":
                children.add(invocation)
            elif row["kind"] == "llm_result" and not attrs.get("runtime_internal"):
                if attrs.get("request_id"):
                    if invocation in last_result:
                        nonterminal.add(last_result[invocation])
                    last_result[invocation] = attrs["request_id"]
                    requests_by_invocation[invocation].add(attrs["request_id"])
                    if attrs.get("tool_call_count", 0):
                        nonterminal.add(attrs["request_id"])
            elif row["kind"] == "return" and attrs.get("source") == "deepagents_task":
                if attrs.get("outcome") == "completed" and invocation in last_result:
                    rid = last_result[invocation]
                    terminals.add(rid)
                    if type(row.get("ts_ms")) in (int, float):
                        return_times[rid] = row["ts_ms"]
                nonterminal.update(requests_by_invocation[invocation] - terminals)
            elif row["kind"] == "invocation_cancel":
                nonterminal.update(requests_by_invocation[invocation] - terminals)
    native = {
        row["attributes"]["request_id"]: row
        for row in snapshot_records(arm / "server/runtime_events.sglang.jsonl", allow_partial=allow_partial)
        if row["kind"] == "llm_result"
        and type(row["attributes"].get("output_tokens")) is int
    }
    forecasts = defaultdict(list)
    starts = []
    clock_offset_ms = None
    for row in snapshot_records(
        arm / "opportunities/admission_opportunities.jsonl", allow_partial=allow_partial,
    ):
        if row["event"] == "semantic_child_forecast":
            forecasts[row["request_id"]].append(row)
        elif row["event"] == "final_stage_latest_start":
            starts.append(row)
        elif row["event"] == "safe_point_census" and clock_offset_ms is None:
            if type(row.get("monotonic_ms")) in (int, float):
                clock_offset_ms = row["ts_ms"] - row["monotonic_ms"]
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
    other = selected.keys() - terminals
    confirmed_other = other if complete else other & nonterminal
    diagnostics = trigger_diagnostics(
        starts, forecasts, native, terminals, return_times, clock_offset_ms,
    )
    replay = sampled_trigger_replay(forecasts, native, terminals, threshold)
    return {
        "schema_version": 2,
        "scope": "development diagnostics; not held-out validation or transfer benefit",
        "audit_started_ts_ms": started_ms, "audit_finished_ts_ms": time.time() * 1000,
        "collection_complete": complete,
        "partial_snapshot": not complete,
        "snapshot_semantics": "fixed byte limit per file; files are not an atomic cross-file snapshot",
        "aggregation": "one first crossing and one last snapshot per request",
        "threshold": threshold,
        "natural_child_created_count": len(children),
        "natural_return_request_count": len(terminals),
        "forecast_request_count": len(forecasts),
        "first_crossing_request_count": len(selected),
        "first_crossing_natural_final_count": len(selected.keys() & terminals),
        "first_crossing_other_count": len(confirmed_other),
        "first_crossing_unresolved_count": len(other - confirmed_other),
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
        "estimated_work_trigger_diagnostics": diagnostics,
        "sampled_work_update_comparison": replay,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    report = json.loads((args.artifact.resolve().parent / "report.json").read_text())
    threshold = report["calibration"]["semantic_event"]["request_operating_point"]["threshold"]
    if type(threshold) not in (int, float) or not 0 <= threshold <= 1:
        raise ValueError("invalid frozen model threshold")
    result = audit(args.arm, float(threshold), allow_partial=args.allow_partial)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
