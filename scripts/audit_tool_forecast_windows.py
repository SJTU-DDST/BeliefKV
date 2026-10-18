#!/usr/bin/env python3
"""Compare recorded tool forecasts with proven tool-round completion boundaries."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_prediction import NativeToolWaitHint
from scripts.summarize_semantic_h2d_ab import runtime_clock_offset


def identity(row: dict) -> tuple:
    return tuple(row.get(field) for field in (
        "workflow_id", "invocation_id", "context_id", "context_epoch",
    ))


def finite(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def distribution(values) -> dict:
    values = [value for value in values if finite(value)]
    if not values:
        return {"count": 0}
    return {
        "count": len(values), "p50": float(np.median(values)),
        "p90": float(np.quantile(values, .9)), "min": min(values), "max": max(values),
    }


def remaining_at_acceptance(row: dict, now_ms: float) -> float | None:
    key = PrefillCandidateKey(
        "offline", row["workflow_id"], row["invocation_id"], row["context_id"],
        row["context_epoch"], 0,
    )
    issued = row["observed_monotonic_ms"]
    hint = NativeToolWaitHint(
        key, row["p10_ms"], row["p50_ms"], row["p90_ms"],
        issued, issued + 5000., "offline-diagnostic",
        release_cdf=tuple(tuple(point) for point in row.get("release_cdf", ())),
        residual_duration_mass=tuple(
            tuple(point) for point in row.get("residual_duration_mass", ())
        ),
        residual_support_detail=row.get("residual_support_detail"),
    )
    return hint.remaining_quantile(.5, now_ms=now_ms)


def evaluate(lifecycle: dict, records) -> dict:
    groups = {}
    for row in lifecycle["wait_event_attribution"]["rows"]:
        if row["source"] != "tool_wait":
            continue
        key = identity(row)
        if key in groups:
            raise ValueError("tool-round labels are not unique")
        groups[key] = {
            "label": row, "forecasts": [], "plans": [],
            "after_completion_forecasts": 0,
        }
    counts = Counter()
    offset = None
    for row in records:
        event = row.get("event")
        if offset is None:
            offset = runtime_clock_offset(row)
        if event not in ("tool_wait_forecast", "wait_prefetch_plan"):
            continue
        if event == "wait_prefetch_plan" and row.get("source") != "tool_wait":
            continue
        counts[f"{event}_records"] += 1
        group = groups.get(identity(row))
        if group is None:
            counts["records_without_round_label"] += 1
            continue
        label = group["label"]
        completion = label.get("completion_ts_ms")
        if (
            label.get("completion_evidence") != "whole_tool_request_round"
            or not finite(completion)
        ):
            counts["records_with_unproven_completion"] += 1
            continue
        accepted = row.get("ts_ms")
        if not finite(accepted):
            counts["records_with_invalid_timestamp"] += 1
            continue
        lead = completion - accepted
        if lead <= 0:
            counts[f"{event}_at_or_after_completion"] += 1
            group["after_completion_forecasts"] += event == "tool_wait_forecast"
            continue
        if event == "tool_wait_forecast":
            issued = row.get("observed_monotonic_ms")
            if offset is None or not finite(issued):
                counts["forecasts_without_clock_anchor"] += 1
                continue
            now_ms = accepted - offset
            age = now_ms - issued
            if not 0 <= age < 5000.:
                counts["forecasts_expired_or_clock_reversed"] += 1
                continue
            predicted = remaining_at_acceptance(row, now_ms)
            if predicted is None:
                counts["forecasts_without_surviving_timing_support"] += 1
                continue
            group["forecasts"].append({
                "accepted_ts_ms": accepted, "actual_remaining_ms": lead,
                "predicted_remaining_ms": predicted, "forecast_age_ms": age,
                "signed_error_ms": predicted - lead,
                "support": row.get("residual_support_detail") or (
                    "release_cdf" if row.get("release_cdf") else "unexpired_quantiles"
                ),
            })
        else:
            amount = (row.get("planned_pool_bytes") or {}).get("kv")
            predicted, horizon = row.get("predicted_remaining_ms"), row.get("start_window_ms")
            if type(amount) is not int or amount <= 0:
                counts["plans_without_positive_full_bytes"] += 1
                continue
            if not finite(predicted) or not finite(horizon) or horizon <= 0:
                counts["positive_full_plans_without_timing"] += 1
                continue
            predicted_ready, actual_ready = predicted <= horizon, lead <= horizon
            category = (
                "timely_candidate" if predicted_ready and actual_ready
                else "predicted_too_early" if predicted_ready
                else "missed_recorded_window" if actual_ready
                else "not_yet_due"
            )
            service_ms = row.get("h2d_p90_ms")
            service_known = finite(service_ms) and service_ms >= 0
            group["plans"].append({
                "ts_ms": accepted, "category": category, "full_bytes": amount,
                "actual_remaining_ms": lead, "predicted_remaining_ms": predicted,
                "start_window_ms": horizon, "reason": row.get("reason"),
                "fits_current_free_lists": row.get("fits_current_free_lists"),
                "h2d_p90_ms": service_ms if service_known else None,
                "actual_lead_minus_h2d_p90_ms": lead - service_ms if service_known else None,
                "predicted_lead_minus_h2d_p90_ms": (
                    predicted - service_ms if service_known else None
                ),
                "service_category": (
                    "recorded_h2d_p90_unavailable" if not service_known
                    else "actual_lead_below_recorded_h2d_p90" if lead < service_ms
                    else "actual_lead_at_least_recorded_h2d_p90"
                ),
            })
    rows = []
    for group in groups.values():
        label = group["label"]
        forecasts = sorted(group["forecasts"], key=lambda row: row["accepted_ts_ms"])
        near = [row for row in forecasts if row["actual_remaining_ms"] <= 1000.]
        first_window = next(
            (row for row in forecasts if row["predicted_remaining_ms"] <= 500.), None,
        )
        rows.append({
            **{field: label.get(field) for field in (
                "workflow_id", "invocation_id", "context_id", "context_epoch",
                "observed_tool_call_count", "completion_evidence",
                "remaining_native_full_host_hit_bytes", "early_started_and_reused_full_bytes",
            )},
            "accepted_pre_completion_forecasts": len(forecasts),
            "last_second_forecasts": len(near),
            "last_pre_completion_forecast": forecasts[-1] if forecasts else None,
            "last_second_median_signed_error_ms": (
                statistics.median(row["signed_error_ms"] for row in near) if near else None
            ),
            "last_second_median_absolute_error_ms": (
                statistics.median(abs(row["signed_error_ms"]) for row in near) if near else None
            ),
            "first_predicted_500ms_window_actual_lead_ms": (
                first_window["actual_remaining_ms"] if first_window else None
            ),
            "forecast_records_at_or_after_completion": group["after_completion_forecasts"],
            "positive_full_plans_with_timing": group["plans"],
        })

    def forecast_summary(selected):
        near = [row for row in selected if row["last_second_forecasts"] > 0]
        leads = [
            row["first_predicted_500ms_window_actual_lead_ms"]
            for row in selected if row["first_predicted_500ms_window_actual_lead_ms"] is not None
        ]
        return {
            "independent_tool_rounds": len(selected),
            "rounds_with_pre_completion_forecasts": sum(
                row["accepted_pre_completion_forecasts"] > 0 for row in selected
            ),
            "rounds_with_last_second_forecasts": len(near),
            "tool_weighted_last_second_signed_error_ms": distribution(
                row["last_second_median_signed_error_ms"] for row in near
            ),
            "tool_weighted_last_second_absolute_error_ms": distribution(
                row["last_second_median_absolute_error_ms"] for row in near
            ),
            "first_500ms_window_lead_ms": distribution(leads),
            "first_500ms_window_in_actual_0_to_500ms": sum(0 < value <= 500. for value in leads),
            "first_500ms_window_more_than_500ms_early": sum(value > 500. for value in leads),
        }

    def plan_summary(categories, *, service_only=False):
        result = {}
        for category in categories:
            for free_only in (False, True):
                selected = {}
                snapshots = 0
                for row in rows:
                    matched = [
                        plan for plan in row["positive_full_plans_with_timing"]
                        if plan["service_category" if service_only else "category"] == category
                        and (not service_only or plan["category"] == "timely_candidate")
                        and (not free_only or plan["fits_current_free_lists"] is True)
                    ]
                    if matched:
                        selected[identity(row)] = max(plan["full_bytes"] for plan in matched)
                        snapshots += len(matched)
                name = category + ("_with_observed_free_capacity" if free_only else "")
                result[name] = {
                    "snapshots": snapshots, "independent_tool_rounds": len(selected),
                    "sum_per_round_max_observed_full_bytes": sum(selected.values()),
                }
        return result

    return {
        "scope": (
            "Fixed-trace diagnostic at forecast acceptance and recorded FULL-plan times. "
            "Uses the runtime's surviving residual mass/CDF/quantile logic and proven "
            "whole-tool-round completion as evaluation labels only. Single-call rounds "
            "are reported separately from sequential/parallel multi-call rounds. Plan "
            "samples are selected by the recorded runtime, not continuous coverage. "
            "A timely candidate means both leads are inside the upper start window; "
            "the service categories separately compare actual lead with recorded H2D "
            "P90. This estimate excludes planning/enqueue time and is not actual "
            "transfer completion or proof of feasible restoration. Missing service "
            "estimates remain unknown, not zero. "
            "Categories overlap across snapshots of one round. Per-round maximum FULL "
            "bytes may share pages and do not establish achievable unique bytes, actual "
            "transfers, residence, admission, hidden stalls, heldout accuracy or speedup."
        ),
        "counts": dict(counts), "scheduler_wall_minus_monotonic_ms": offset,
        "forecast_timing": forecast_summary(rows),
        "single_tool_round_forecast_timing": forecast_summary([
            row for row in rows if row["observed_tool_call_count"] == 1
        ]),
        "multi_tool_round_forecast_timing": forecast_summary([
            row for row in rows if (row["observed_tool_call_count"] or 0) > 1
        ]),
        "positive_full_plan_window_categories": plan_summary((
            "timely_candidate", "predicted_too_early", "missed_recorded_window", "not_yet_due",
        )),
        "timely_full_plan_service_categories": plan_summary((
            "actual_lead_at_least_recorded_h2d_p90",
            "actual_lead_below_recorded_h2d_p90", "recorded_h2d_p90_unavailable",
        ), service_only=True),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path)
    parser.add_argument("--opportunity-read-bytes", type=int)
    args = parser.parse_args()
    if args.opportunity_read_bytes is not None and args.opportunity_read_bytes < 1:
        parser.error("--opportunity-read-bytes must be positive")
    for output in (args.output, args.summary_output):
        if output is not None and output.exists():
            raise FileExistsError(output)
    if (
        args.summary_output is not None
        and args.output.resolve() == args.summary_output.resolve()
    ):
        raise ValueError("summary and detailed output must use different paths")
    audit_bytes = args.lifecycle_audit.read_bytes()
    lifecycle = json.loads(audit_bytes)
    arm = Path(lifecycle["arm"]).resolve()
    source = arm / "opportunities/admission_opportunities.jsonl"
    digest = hashlib.sha256()
    read_bytes = 0

    def records():
        nonlocal read_bytes
        with source.open("rb") as stream:
            for line in stream:
                if not line.endswith(b"\n"):
                    raise ValueError("incomplete opportunity evidence line")
                if (
                    args.opportunity_read_bytes is not None
                    and read_bytes + len(line) > args.opportunity_read_bytes
                ):
                    raise ValueError("opportunity read limit splits an evidence line")
                digest.update(line)
                read_bytes += len(line)
                yield json.loads(line)
                if read_bytes == args.opportunity_read_bytes:
                    break
        if (
            args.opportunity_read_bytes is not None
            and read_bytes != args.opportunity_read_bytes
        ):
            raise ValueError("opportunity source is shorter than the requested prefix")

    result = evaluate(lifecycle, records())
    result["sources"] = {
        "lifecycle_audit": str(args.lifecycle_audit.resolve()),
        "lifecycle_audit_sha256": hashlib.sha256(audit_bytes).hexdigest(),
        "opportunities": str(source), "opportunities_sha256": digest.hexdigest(),
        "opportunities_read_bytes": read_bytes,
        "opportunities_read_bytes_limit": args.opportunity_read_bytes,
        "audit_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(result, indent=2, allow_nan=False) + "\n").encode()
    args.output.write_bytes(encoded)
    summary = {key: value for key, value in result.items() if key != "rows"}
    summary["detailed_evidence"] = {
        "path": str(args.output.resolve()),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "tool_round_rows": len(result["rows"]),
    }
    if args.summary_output is not None:
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        args.summary_output.write_text(
            json.dumps(summary, indent=2, allow_nan=False) + "\n"
        )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
