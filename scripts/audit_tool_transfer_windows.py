#!/usr/bin/env python3
"""Native predictive H2D submission versus all remaining tools' completion."""

from collections import defaultdict
from dataclasses import replace
import argparse
import hashlib
import json
import math
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.summarize_semantic_h2d_ab import records


def history_forecast_audit(arm: Path, artifact: Path) -> dict:
    from beliefkv.predictor.structured_frontier import _conditioned_completed_duration_prior
    from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
    from beliefkv.runtime.sglang_v0520_prediction import NativeToolWaitHint

    artifact_bytes = artifact.read_bytes()
    model = json.loads(artifact_bytes)
    windows = defaultdict(list)
    for path in arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl"):
        active = {}
        for row in records(path):
            attrs = row.get("attributes") or {}
            identity = row.get("invocation_id"), attrs.get("tool_run_id") or attrs.get("tool_call_id")
            if not all(identity):
                continue
            if row["kind"] == "tool_start":
                active[identity] = {
                    "start_ms": row["ts_ms"], "end_ms": None, "attributes": attrs,
                    "workflow_id": row["workflow_id"], "tool_id": identity[1],
                }
            elif row["kind"] == "tool_end" and identity in active:
                window = active.pop(identity)
                window["end_ms"] = row["ts_ms"]
                windows[row["workflow_id"], identity[0]].append(window)
        for identity, window in active.items():
            windows[window["workflow_id"], identity[0]].append(window)
    for values in windows.values():
        values.sort(key=lambda row: row["start_ms"])
    cursor = defaultdict(int)
    groups = {}
    for forecast in records(arm / "opportunities/admission_opportunities.jsonl"):
        if forecast["event"] != "tool_wait_forecast":
            continue
        scope = forecast["workflow_id"], forecast["invocation_id"]
        observed = forecast.get("observed_monotonic_ms")
        if not isinstance(observed, (int, float)):
            continue
        values = windows.get(scope, ())
        index = cursor[scope]
        while (
            index < len(values) and values[index]["end_ms"] is not None
            and values[index]["end_ms"] <= observed
        ):
            index += 1
        cursor[scope] = index
        active = [
            value for value in values[index:]
            if value["start_ms"] <= observed
            and (value["end_ms"] is None or observed < value["end_ms"])
        ]
        if len(active) != 1:
            continue
        window = active[0]
        if window["end_ms"] is None:
            continue
        attrs = window["attributes"]
        if attrs.get("tool_name") != "execute":
            continue
        previous = attrs.get("previous_same_input_duration_ms")
        status = attrs.get("previous_same_input_status")
        detail, duration, margin = None, None, None
        if status == "success":
            detail, duration, margin = (
                "same_input_completed", previous, model.get("repeat_error_p90_ms"),
            )
        elif (
            status == "error" and attrs.get("is_child")
            and isinstance(previous, (int, float)) and previous > 100.
        ):
            detail, duration, margin = (
                "same_failed_input_completed", previous, model.get("failed_repeat_error_p90_ms"),
            )
        elif (
            attrs.get("is_child")
            and (attrs.get("project_class_completed_support") or 0) >= 16
        ):
            detail, duration, margin = (
                "completed_project_command", attrs.get("project_class_duration_median_ms"),
                model.get("project_error_p90_ms"),
            )
        if any(
            not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
            for value in (duration, margin)
        ):
            continue
        residual = _conditioned_completed_duration_prior(
            duration, margin, observed - window["start_ms"],
        )
        if residual is None or not math.isclose(
            residual.quantile(.5), forecast["p50_ms"], abs_tol=1.,
        ):
            continue
        key = PrefillCandidateKey(
            "offline-observation", scope[0], scope[1], forecast["context_id"],
            forecast["context_epoch"], 0,
        )
        old = NativeToolWaitHint(
            key, forecast["p10_ms"], forecast["p50_ms"], forecast["p90_ms"],
            observed, observed + 5000., "offline",
            release_cdf=tuple(tuple(point) for point in forecast.get("release_cdf", ())),
        )
        actual = window["end_ms"] - observed
        generic = old.remaining_quantile(.5, now_ms=observed)
        new = replace(
            old, residual_duration_mass=tuple(zip(residual.values, residual.probability_mass)),
            residual_support_detail=detail,
        )
        specific = new.remaining_quantile(.5, now_ms=observed)
        identity = (*scope, window["tool_id"])
        group = groups.setdefault(identity, {
            "workflow_id": scope[0], "invocation_id": scope[1], "tool_id": window["tool_id"],
            "history_support": detail,
            "tool_duration_ms": window["end_ms"] - window["start_ms"],
            "observations": [],
        })
        group["observations"].append({
            "actual_remaining_ms": actual, "generic_p50_ms": generic,
            "history_p50_ms": specific, "observation_monotonic_ms": observed,
        })
    rows = []
    for group in groups.values():
        observations = group.pop("observations")
        near = [row for row in observations if row["actual_remaining_ms"] <= 1000.]
        first_old = next(
            (row for row in observations if row["generic_p50_ms"] is not None
             and row["generic_p50_ms"] <= 500.), None,
        )
        first_new = next((row for row in observations if row["history_p50_ms"] <= 500.), None)
        errors = {
            name: median([
                abs(row[field] - row["actual_remaining_ms"]) for row in near
                if row[field] is not None
            ]) if any(row[field] is not None for row in near) else None
            for name, field in (
                ("generic_near_end_median_absolute_error_ms", "generic_p50_ms"),
                ("history_near_end_median_absolute_error_ms", "history_p50_ms"),
            )
        }
        rows.append({
            **group, "observations": len(observations), "last_second_observations": len(near),
            "generic_first_500ms_window_actual_lead_ms": (
                first_old["actual_remaining_ms"] if first_old else None
            ),
            "history_first_500ms_window_actual_lead_ms": (
                first_new["actual_remaining_ms"] if first_new else None
            ),
            **errors,
        })
    def summary(group):
        comparable = [
            row for row in group if row["generic_near_end_median_absolute_error_ms"] is not None
            and row["history_near_end_median_absolute_error_ms"] is not None
        ]
        return {
            "matched_independent_tools": len(group),
            "matched_forecasts": sum(row["observations"] for row in group),
            "tools_with_last_second_observations": len(comparable),
            "generic_tool_weighted_last_second_median_absolute_error_ms": (
                median(row["generic_near_end_median_absolute_error_ms"] for row in comparable)
                if comparable else None
            ),
            "history_tool_weighted_last_second_median_absolute_error_ms": (
                median(row["history_near_end_median_absolute_error_ms"] for row in comparable)
                if comparable else None
            ),
            "history_first_windows_in_actual_0_to_500ms": sum(
                0 < (row["history_first_500ms_window_actual_lead_ms"] or 0) <= 500.
                for row in group
            ),
            "generic_first_windows_in_actual_0_to_500ms": sum(
                0 < (row["generic_first_500ms_window_actual_lead_ms"] or 0) <= 500.
                for row in group
            ),
        }

    return {
        "scope": (
            "Diagnostic fixed-trace history-head routing at the forecast's observation time; "
            "all-tool overlap is excluded. Online recorded history plus frozen margins are "
            "inputs; subsequent tool END is used only as the evaluation label. "
            "P50 must match the recorded history head. Shared-host monotonic clocks are "
            "assumed as in this local runner. Each tool contributes one median error; "
            "this is not a heldout calibration, physical opportunity or speedup estimate."
        ),
        "artifact": str(artifact), "artifact_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        **summary(rows),
        "by_history_support": {
            name: summary([row for row in rows if row["history_support"] == name])
            for name in sorted({row["history_support"] for row in rows})
        },
        "tools_duration_at_least_1000ms": summary([
            row for row in rows if row["tool_duration_ms"] >= 1000.
        ]),
        "rows": rows,
    }


def audit(arm: Path, *, artifact: Path | None = None) -> dict:
    ops = [
        row for row in records(arm / "opportunities/admission_opportunities.jsonl")
        if row["event"] == "safe_point_census"
        or row["event"] == "prefetch_native_issued" and row.get("source") == "tool_wait"
    ]
    census = next(row for row in ops if row["event"] == "safe_point_census")
    offset = census["ts_ms"] - census["monotonic_ms"]
    ends = {}
    legacy_ends = defaultdict(list)
    for path in arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl"):
        for row in records(path):
            if row["kind"] != "tool_end":
                continue
            attrs = row.get("attributes") or {}
            identity = attrs.get("tool_run_id") or attrs.get("tool_call_id")
            if identity:
                ends[(row["invocation_id"], identity)] = row["ts_ms"] + offset
            legacy_ends[row["invocation_id"]].append(row["ts_ms"] + offset)
    issued = {
        row["command_id"]: row for row in ops
        if row["event"] == "prefetch_native_issued" and row.get("source") == "tool_wait"
    }
    uses = {
        row["command_id"]: row for row in records(arm / "server/physical_action_use.jsonl")
        if row["event"] == "beliefkv_prefetch_first_service"
    }
    rows = []
    for transfer in records(arm / "server/transfer_telemetry.jsonl"):
        for child in transfer.get("tagged_child_commits", []):
            issue = issued.get(child["command_id"])
            if issue is None:
                continue
            identities = issue.get("active_tool_ids") or []
            matched = [
                ends[(issue["invocation_id"], identity)] for identity in identities
                if (issue["invocation_id"], identity) in ends
            ]
            completion = max(matched) if len(matched) == len(identities) and identities else None
            lead = (
                completion - transfer["submit_ts_ms"]
                if completion is not None else None
            )
            use = uses.get(child["command_id"])
            rows.append({
                "command_id": child["command_id"], "context_id": issue["context_id"],
                "active_tool_ids": identities, "tool_completion_ts_ms": completion,
                "native_submit_ts_ms": transfer["submit_ts_ms"],
                "submit_lead_to_tool_end_ms": lead,
                "actual_bytes": child["num_bytes"],
                "full_first_service_reused": use["full_node_reused"] if use else None,
            })
    leads = [row["submit_lead_to_tool_end_ms"] for row in rows if row["submit_lead_to_tool_end_ms"] is not None]
    result = {
        "scope": "controller submit before tool completion, not before first GPU service",
        "command_count": len(rows), "tool_end_observed_count": len(leads),
        "started_before_tool_end_count": sum(lead > 0 for lead in leads),
        "started_0_to_1000ms_before_tool_end_count": sum(0 < lead <= 1000 for lead in leads),
        "started_100_to_1000ms_before_tool_end_count": sum(100 <= lead <= 1000 for lead in leads),
        "started_at_or_after_tool_end_count": sum(lead <= 0 for lead in leads),
        "median_submit_lead_to_tool_end_ms": median(leads) if leads else None,
        "rows": rows,
    }
    if artifact is not None:
        result["history_forecast_routing"] = history_forecast_audit(arm, artifact)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--predictor-artifact", type=Path,
                        help="Diagnose completed-history head routing on the same fixed trace.")
    args = parser.parse_args()
    result = audit(args.arm, artifact=args.predictor_artifact)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    summary = {key: value for key, value in result.items()
               if key not in ("rows", "history_forecast_routing")}
    if "history_forecast_routing" in result:
        summary["history_forecast_routing"] = {
            key: value for key, value in result["history_forecast_routing"].items() if key != "rows"
        }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
