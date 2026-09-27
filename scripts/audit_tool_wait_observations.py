#!/usr/bin/env python3
"""Audit live child tool survival observations against complete runtime traces."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import median
import sys
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from beliefkv.core.events import RuntimeEvent
from beliefkv.experiments.p6_decision_points import _event_triggers


def _quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def _stats(values: list[float]) -> dict[str, float | int] | None:
    if not values:
        return None
    return {
        "count": len(values),
        "p50_ms": median(values),
        "p90_ms": _quantile(values, .9),
        "p95_ms": _quantile(values, .95),
    }


def _supported_start(attrs: dict[str, Any]) -> bool:
    total = attrs.get("project_shape_survivor_500ms_total_median_ms")
    support = attrs.get("project_shape_survivor_500ms_support")
    return (
        attrs.get("is_child") is True
        and attrs.get("tool_name") == "execute"
        and attrs.get("previous_same_input_status") != "success"
        and type(support) is int and support >= 4
        and type(total) in (int, float)
        and math.isfinite(total) and total > 500
    )


def audit(run_dir: Path, *, expected_workflows: int) -> dict[str, Any]:
    if expected_workflows <= 0:
        raise ValueError("expected_workflows must be positive")
    workloads = run_dir / "intent_workloads"
    summary = json.loads((workloads / "summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((workloads / "manifest.json").read_text(encoding="utf-8"))
    instance_ids = manifest["instance_ids"]
    if (
        len(instance_ids) != expected_workflows
        or len(set(instance_ids)) != len(instance_ids)
        or summary["workflow_count"] != expected_workflows
    ):
        raise ValueError("run does not contain the complete frozen workflow set")
    if any(
        not (workloads / "workflows" / item / "result.json").is_file()
        for item in instance_ids
    ):
        raise ValueError("missing terminal workflow result")
    source = Path(manifest["config"]["workload_manifest"])
    source_items = {
        item["instance_id"]: item["repo"]
        for item in json.loads(source.read_text(encoding="utf-8"))["workloads"]
    }
    traces = []
    metadata = {}
    starts: dict[tuple[str, str], dict[str, Any]] = {}
    candidates = []
    observations = []
    for instance in instance_ids:
        path = workloads / "workflows" / instance / "runtime_events.deepagents.jsonl"
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if not events or events[-1]["kind"] != "workflow_end":
            raise ValueError(f"incomplete workflow trace: {instance}")
        workflow_id = events[0]["workflow_id"]
        if any(event["workflow_id"] != workflow_id for event in events):
            raise ValueError(f"mixed workflow identities: {instance}")
        metadata[workflow_id] = {"project": source_items[instance]}
        traces.extend(RuntimeEvent.from_dict(event) for event in events)
        for event in events:
            kind = event["kind"]
            attrs = event.get("attributes") or {}
            key = workflow_id, str(attrs.get("tool_call_id") or "")
            if kind == "tool_start" and key[1]:
                if key in starts:
                    raise ValueError(f"duplicate tool call: {key}")
                starts[key] = {
                    "start_ms": float(event["ts_ms"]),
                    "invocation_id": event["invocation_id"],
                    "input_sha256": attrs.get("input_sha256"),
                    "prior_total_ms": attrs.get(
                        "project_shape_survivor_500ms_total_median_ms"
                    ),
                    "support": attrs.get("project_shape_survivor_500ms_support"),
                    "supported": _supported_start(attrs),
                    "end_ms": None,
                    "status": None,
                    "observed_ms": None,
                }
                if starts[key]["supported"]:
                    candidates.append(key)
            elif kind == "tool_end" and key[1]:
                call = starts.get(key)
                if call is None or call["end_ms"] is not None:
                    raise ValueError(f"tool end lacks a unique start: {key}")
                if event["invocation_id"] != call["invocation_id"]:
                    raise ValueError(f"tool end changed invocation: {key}")
                call["end_ms"] = float(event["ts_ms"])
                call["status"] = attrs.get("status")
                if call["end_ms"] < call["start_ms"]:
                    raise ValueError(f"negative tool duration: {key}")
            elif kind == "tool_wait_observation":
                call = starts.get(key)
                if call is None or not call["supported"] or call["observed_ms"] is not None:
                    raise ValueError(f"observation lacks supported open call: {key}")
                observed = float(event["ts_ms"])
                elapsed = observed - call["start_ms"]
                total = float(call["prior_total_ms"])
                if (
                    event["invocation_id"] != call["invocation_id"]
                    or call["end_ms"] is not None
                    or not 500 <= elapsed < total
                    or abs(float(attrs["tool_elapsed_ms"]) - elapsed) > 1
                    or abs(float(attrs["tool_wait_shape_eta_ms_p50"])
                           - (total - elapsed)) > 1
                    or abs(float(attrs[
                        "project_shape_survivor_500ms_total_median_ms"
                    ]) - total) > .01
                    or attrs["project_shape_survivor_500ms_support"] != call["support"]
                ):
                    raise ValueError(f"observation violates live tool identity: {key}")
                call["observed_ms"] = observed
                observations.append(key)
    # Recompute the bounded history from committed traces, not from the
    # recorded candidate prior, to detect future-labelled or reordered inputs.
    _event_triggers(
        sorted(traces, key=lambda item: (item.ts_ms, item.workflow_id, item.event_id)),
        workflow_metadata=metadata,
    )
    clock_errors, leads, dispatch_lag = [], [], []
    success_leads, error_leads = [], []
    first_success_by_input: dict[tuple[str, str, str], dict[str, float]] = {}
    success_actionable_workflows: set[str] = set()
    success_actionable_errors: list[float] = []
    predicted_success_500ms_leads: list[float] = []
    predicted_error_500ms_leads: list[float] = []
    success_missing_input_identity = 0
    returned = observed_returned = returned_after_750 = observed_censored = 0
    observed_after_750 = 0
    censored = finished_before_500 = 0
    for key in candidates:
        call = starts[key]
        ended = call["end_ms"]
        if ended is None:
            censored += 1
            if call["observed_ms"] is not None:
                observed_censored += 1
            continue
        duration = ended - call["start_ms"]
        if duration < 500:
            finished_before_500 += 1
        else:
            returned += 1
        if duration >= 750:
            returned_after_750 += 1
        observed = call["observed_ms"]
        if observed is not None:
            observed_returned += 1
            dispatch_lag.append(observed - call["start_ms"] - 500)
            lead = ended - observed
            leads.append(lead)
            if duration >= 750:
                observed_after_750 += 1
            if call["status"] == "success":
                error = abs(duration - call["prior_total_ms"])
                clock_errors.append(error)
                success_leads.append(lead)
                if call["prior_total_ms"] - (observed - call["start_ms"]) >= 500:
                    predicted_success_500ms_leads.append(lead)
                if lead >= 500:
                    success_actionable_workflows.add(key[0])
                    success_actionable_errors.append(error)
                input_sha256 = call["input_sha256"]
                if not isinstance(input_sha256, str) or not input_sha256:
                    success_missing_input_identity += 1
                else:
                    distinct_key = key[0], call["invocation_id"], input_sha256
                    if distinct_key not in first_success_by_input:
                        first_success_by_input[distinct_key] = {
                            "lead_ms": lead, "error_ms": error,
                        }
            elif call["status"] == "error":
                error_leads.append(lead)
                if call["prior_total_ms"] - (observed - call["start_ms"]) >= 500:
                    predicted_error_500ms_leads.append(lead)
    timer = summary["tool_wait_shadow"]
    if (
        timer["published"] != len(observations)
        or timer["errors"] != 0 or timer["dropped"] != 0
        or timer["pending"] != 0
    ):
        raise ValueError("timer counters disagree with complete traces")
    return {
        "run_dir": str(run_dir),
        "workflow_count": expected_workflows,
        "run_commit": (run_dir / "source_commit.txt").read_text().strip(),
        "trace_events": len(traces),
        "supported_tool_starts": len(candidates),
        "right_censored_starts": censored,
        "returned_before_500ms": finished_before_500,
        "returned_after_500ms": returned,
        "returned_after_750ms": returned_after_750,
        "observed_calls": len(observations),
        "observed_returned_calls": observed_returned,
        "observed_censored_calls": observed_censored,
        "observed_returned_after_750ms": observed_after_750,
        "observed_lag_after_500ms": _stats(dispatch_lag),
        "observed_return_lead": _stats(leads),
        "observed_success_return_lead": _stats(success_leads),
        "observed_success_lead_at_least_500ms": sum(
            lead >= 500 for lead in success_leads
        ),
        "observed_success_lead_at_least_1000ms": sum(
            lead >= 1000 for lead in success_leads
        ),
        "observed_success_actionable_500ms_workflows": len(
            success_actionable_workflows
        ),
        "observed_success_actionable_500ms_point_error": _stats(
            success_actionable_errors
        ),
        "observed_success_actionable_500ms_within_500ms": sum(
            error <= 500 for error in success_actionable_errors
        ),
        "observed_success_predicted_500ms_lead": {
            "selected": len(predicted_success_500ms_leads),
            "true_at_least_500ms": sum(
                lead >= 500 for lead in predicted_success_500ms_leads
            ),
            "returned_before_500ms": sum(
                lead < 500 for lead in predicted_success_500ms_leads
            ),
        },
        "observed_error_predicted_500ms_lead": {
            "selected": len(predicted_error_500ms_leads),
            "true_at_least_500ms": sum(
                lead >= 500 for lead in predicted_error_500ms_leads
            ),
            "returned_before_500ms": sum(
                lead < 500 for lead in predicted_error_500ms_leads
            ),
        },
        "observed_error_return_lead": _stats(error_leads),
        "observed_success_point_absolute_error": _stats(clock_errors),
        "observed_success_missing_input_identity": success_missing_input_identity,
        "observed_success_distinct_inputs": len(first_success_by_input),
        "observed_success_distinct_input_lead_at_least_500ms": sum(
            row["lead_ms"] >= 500 for row in first_success_by_input.values()
        ),
        "observed_success_distinct_input_lead_at_least_1000ms": sum(
            row["lead_ms"] >= 1000 for row in first_success_by_input.values()
        ),
        "observed_success_distinct_input_error": _stats([
            row["error_ms"] for row in first_success_by_input.values()
        ]),
        "observed_lead_at_least_500ms": sum(lead >= 500 for lead in leads),
        "timer": timer,
        "scope": (
            "Engineering canary only; accuracy is conditional on support, survival, "
            "observed delivery, and a returned tool. Not independent project holdout."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--expected-workflows", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = audit(args.run_dir, expected_workflows=args.expected_workflows)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
