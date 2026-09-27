#!/usr/bin/env python3
"""Audit actual 100 ms child-tool timing observations without action claims."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from statistics import median


def _events(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    return sorted(values)[int(.95 * (len(values) - 1))]


def _eligible(attrs: dict) -> bool:
    total = attrs.get("project_shape_survivor_100ms_total_median_ms")
    deviation = attrs.get("project_shape_survivor_100ms_deviation_p90_ms")
    support = attrs.get("project_shape_survivor_100ms_support")
    return (
        attrs.get("is_child") is True
        and attrs.get("tool_name") == "execute"
        and attrs.get("previous_same_input_status") != "success"
        and type(total) in (int, float)
        and math.isfinite(total) and total > 1100
        and type(deviation) in (int, float)
        and math.isfinite(deviation) and 0 <= deviation <= 1000
        and type(support) is int and support >= 4
    )


def audit(workflows: Path, *, manifest: Path | None = None) -> dict:
    expected: set[str] | None = None
    if manifest is not None:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        ids = [row["instance_id"] for row in payload["workloads"]]
        if len(ids) != len(set(ids)):
            raise ValueError("manifest has duplicate workflow IDs")
        expected = set(ids)
    paths = sorted(workflows.glob("*/runtime_events.deepagents.jsonl"))
    if not paths:
        raise ValueError(f"no workflow traces in {workflows}")
    observed = {path.parent.name for path in paths}
    if expected is not None and observed - expected:
        raise ValueError("unexpected workflow trace outside frozen manifest")

    counts: Counter[str] = Counter()
    delays: list[float] = []
    leads: list[float] = []
    errors: list[float] = []
    successful_errors: list[float] = []
    failed_errors: list[float] = []
    successful_leads: list[float] = []
    failed_leads: list[float] = []
    predicted_success_500ms_leads: list[float] = []
    predicted_error_500ms_leads: list[float] = []
    unique_input_errors: list[float] = []
    workflow_errors: list[float] = []
    successful_input_keys: set[tuple[str, str, str]] = set()
    successful_input_leads: list[float] = []
    per_workflow: dict[str, dict] = {}
    for path in paths:
        events = _events(path)
        workflow_counts: Counter[str] = Counter()
        workflow_errors_returned: list[float] = []
        unique_inputs: set[tuple[str, str]] = set()
        observed_inputs: set[tuple[str, str]] = set()
        if any(
            row.get("kind") == "workflow_end"
            and (row.get("attributes") or {}).get("outcome") == "completed"
            for row in events
        ):
            counts["completed_workflows"] += 1
        starts: dict[tuple[str, str], dict] = {}
        ends: dict[tuple[str, str], dict] = {}
        signals: dict[tuple[str, str], dict] = {}
        for event in events:
            attrs = event.get("attributes") or {}
            call_id = attrs.get("tool_call_id")
            invocation_id = event.get("invocation_id")
            if not call_id or not invocation_id:
                continue
            key = str(invocation_id), str(call_id)
            if event.get("kind") == "tool_start":
                if key in starts:
                    raise ValueError(f"duplicate tool start: {path}: {key}")
                starts[key] = event
            elif event.get("kind") == "tool_end":
                if key in ends:
                    raise ValueError(f"duplicate tool end: {path}: {key}")
                ends[key] = event
            elif (
                event.get("kind") == "structured_action"
                and attrs.get("beliefkv_tool_wait_early_shadow") is True
            ):
                if key in signals:
                    raise ValueError(f"duplicate early observation: {path}: {key}")
                signals[key] = event
        for key, signal in signals.items():
            if key not in starts:
                raise ValueError(f"orphan early observation: {path}: {key}")
            start_attrs = starts[key].get("attributes") or {}
            if not (
                signal.get("attributes", {}).get("diagnostic_only") is True
                and _eligible(start_attrs)
            ):
                raise ValueError(f"unqualified early observation: {path}: {key}")
        for key, start in starts.items():
            attrs = start.get("attributes") or {}
            if not _eligible(attrs):
                continue
            counts["eligible_tool_starts"] += 1
            workflow_counts["eligible_tool_starts"] += 1
            input_key = (
                key[0], str(attrs.get("input_sha256") or key[1])
            )
            unique_inputs.add(input_key)
            if attrs.get("previous_same_input_status") == "error":
                counts["repeat_after_error_starts"] += 1
                workflow_counts["repeat_after_error_starts"] += 1
            end = ends.get(key)
            signal = signals.get(key)
            if end is None:
                counts["no_tool_end"] += 1
            elif end["ts_ms"] - start["ts_ms"] <= 100:
                counts["returned_by_100ms"] += 1
            if signal is None:
                counts["no_observation"] += 1
                if end is not None and end["ts_ms"] - start["ts_ms"] > 100:
                    counts["missed_live_landmark"] += 1
                continue
            counts["observations"] += 1
            workflow_counts["observations"] += 1
            delay = float(signal["ts_ms"] - start["ts_ms"] - 100)
            if delay < 0:
                raise ValueError(f"early observation precedes 100 ms: {path}: {key}")
            delays.append(delay)
            if end is None:
                counts["observed_without_tool_end"] += 1
                continue
            lead = float(end["ts_ms"] - signal["ts_ms"])
            if lead < 0:
                raise ValueError(f"early observation after tool end: {path}: {key}")
            leads.append(lead)
            counts["observed_returned"] += 1
            duration = float(end["ts_ms"] - start["ts_ms"])
            total = float(attrs["project_shape_survivor_100ms_total_median_ms"])
            error = abs(duration - total)
            errors.append(error)
            workflow_errors_returned.append(error)
            if input_key not in observed_inputs:
                observed_inputs.add(input_key)
                unique_input_errors.append(error)
            if lead >= 500:
                counts["lead_at_least_500ms"] += 1
            if lead >= 1000:
                counts["lead_at_least_1000ms"] += 1
            predicted_500ms_lead = total - (
                float(signal["ts_ms"]) - float(start["ts_ms"])
            ) >= 500
            if (end.get("attributes") or {}).get("status") != "success":
                counts["observed_failed_tools"] += 1
                failed_errors.append(error)
                failed_leads.append(lead)
                if predicted_500ms_lead:
                    predicted_error_500ms_leads.append(lead)
            else:
                successful_errors.append(error)
                successful_leads.append(lead)
                if lead >= 500:
                    counts["successful_lead_at_least_500ms"] += 1
                if lead >= 1000:
                    counts["successful_lead_at_least_1000ms"] += 1
                if predicted_500ms_lead:
                    predicted_success_500ms_leads.append(lead)
                input_hash = attrs.get("input_sha256")
                if not (
                    isinstance(input_hash, str)
                    and len(input_hash) == 64
                    and all(char in "0123456789abcdef" for char in input_hash)
                ):
                    counts["successful_observed_missing_input_sha256"] += 1
                else:
                    success_key = path.parent.name, key[0], input_hash
                    if success_key not in successful_input_keys:
                        successful_input_keys.add(success_key)
                        successful_input_leads.append(lead)
        if workflow_errors_returned:
            workflow_errors.append(median(workflow_errors_returned))
        per_workflow[path.parent.name] = {
            **dict(workflow_counts),
            "distinct_candidate_inputs": len(unique_inputs),
            "distinct_observed_returned_inputs": len(observed_inputs),
            "observed_eta_absolute_error_p50_ms": (
                median(workflow_errors_returned)
                if workflow_errors_returned else None
            ),
        }
    return {
        "expected_workflows": len(expected) if expected is not None else None,
        "traced_workflows": len(paths),
        "missing_workflows": sorted(expected - observed) if expected is not None else [],
        "counts": dict(counts),
        "dispatch_lateness_p50_ms": median(delays) if delays else None,
        "dispatch_lateness_p95_ms": _p95(delays),
        "lead_p50_ms": median(leads) if leads else None,
        "lead_p95_ms": _p95(leads),
        "eta_absolute_error_p50_ms": median(errors) if errors else None,
        "eta_absolute_error_p95_ms": _p95(errors),
        "success_eta_absolute_error_p50_ms": (
            median(successful_errors) if successful_errors else None
        ),
        "successful_observed_return_lead_p50_ms": (
            median(successful_leads) if successful_leads else None
        ),
        "failed_observed_return_lead_p50_ms": (
            median(failed_leads) if failed_leads else None
        ),
        "successful_distinct_inputs": len(successful_input_keys),
        "successful_distinct_inputs_lead_at_least_500ms": sum(
            lead >= 500 for lead in successful_input_leads
        ),
        "successful_distinct_inputs_lead_at_least_1000ms": sum(
            lead >= 1000 for lead in successful_input_leads
        ),
        "success_predicted_500ms_lead": {
            "selected": len(predicted_success_500ms_leads),
            "true_at_least_500ms": sum(
                lead >= 500 for lead in predicted_success_500ms_leads
            ),
            "returned_before_500ms": sum(
                lead < 500 for lead in predicted_success_500ms_leads
            ),
        },
        "error_predicted_500ms_lead": {
            "selected": len(predicted_error_500ms_leads),
            "true_at_least_500ms": sum(
                lead >= 500 for lead in predicted_error_500ms_leads
            ),
            "returned_before_500ms": sum(
                lead < 500 for lead in predicted_error_500ms_leads
            ),
        },
        "failed_eta_absolute_error_p50_ms": (
            median(failed_errors) if failed_errors else None
        ),
        "distinct_input_eta_absolute_error_p50_ms": (
            median(unique_input_errors) if unique_input_errors else None
        ),
        "workflow_weighted_eta_absolute_error_p50_ms": (
            median(workflow_errors) if workflow_errors else None
        ),
        "per_workflow": per_workflow,
        "interpretation": "trace_only_not_physical_transfer_or_action_eligible",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--workload-manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.workflows, manifest=args.workload_manifest)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
