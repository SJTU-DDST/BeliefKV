#!/usr/bin/env python3
"""Stream matched first-tool decisions to compare two pinned timing artifacts."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import defaultdict
import json
import math
from pathlib import Path
import sys

import orjson

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from beliefkv.predictor.structured_frontier import (
    FrontierBeliefModel, WaitBeliefKind, _local_features_from_row,
)
from scripts.audit_repeated_tool_timing import _quantile


def _rows(path: Path):
    with path.open("rb") as stream:
        for line in stream:
            if line.strip():
                yield orjson.loads(line)


def _metrics(rows: list[dict], name: str) -> dict:
    selected = [r for r in rows if r.get(name) is not None]
    errors = [r[name] for r in selected]
    workflows = defaultdict(list)
    for row in selected:
        workflows[row["workflow"]].append(row[name])
    return {
        "observations": len(selected),
        "p50_absolute_error_ms": _quantile(errors, .5),
        "p90_absolute_error_ms": _quantile(errors, .9),
        "p95_absolute_error_ms": _quantile(errors, .95),
        "within_500ms": (
            sum(error <= 500 for error in errors) / len(errors) if errors else None
        ),
        "workflow_count": len(workflows),
        "workflow_weighted_p50_ms": _quantile(
            [_quantile(values, .5) for values in workflows.values()], .5
        ),
    }


def _start_trigger_quality(rows: list[dict], name: str) -> dict:
    eligible = [
        row for row in rows
        if type(row.get(f"{name}_forecast_ms")) in (int, float)
        and math.isfinite(row[f"{name}_forecast_ms"])
    ]
    imminent = [
        row for row in eligible if row[f"{name}_forecast_ms"] <= 500
    ]
    predicted_long = [
        row for row in eligible if row[f"{name}_forecast_ms"] >= 2_000
    ]
    actual_long = sum(row["actual_ms"] >= 2_000 for row in eligible)
    true_long = sum(row["actual_ms"] >= 2_000 for row in predicted_long)
    return {
        "predicted_imminent_count": len(imminent),
        "imminent_false_over_2s": sum(
            row["actual_ms"] > 2_000 for row in imminent
        ),
        "predicted_long_count": len(predicted_long),
        "actual_long_count": actual_long,
        "long_precision": (
            true_long / len(predicted_long) if predicted_long else None
        ),
        "long_recall": true_long / actual_long if actual_long else None,
    }


def _checkpoint_groups_metrics(groups: dict) -> dict:
    return {
        name: {
            "joint_samples": len(samples),
            "zero_remaining_baseline": _metrics(samples, "zero"),
            "reference": _metrics(samples, "reference"),
            "candidate": _metrics(samples, "candidate"),
            "false_imminent_with_over_2s_remaining": {
                side: sum(
                    sample[f"{side}_forecast_ms"] <= 500
                    and sample["actual_ms"] > 2_000
                    for sample in samples
                )
                for side in ("reference", "candidate")
            },
        }
        for name, samples in sorted(groups.items())
    }


def _fixed_clock_checkpoints(
    waits: dict, first_snapshots: dict,
    reference: FrontierBeliefModel, candidate: FrontierBeliefModel,
) -> dict:
    groups = defaultdict(list)
    counts = defaultdict(int)
    for key, (row, invocation) in first_snapshots.items():
        wait = waits[key]
        start = wait.get("start_ts_ms")
        if type(start) not in (int, float) or not math.isfinite(start):
            continue
        end = float(wait["terminal_ts_ms"])
        if start > float(row["timestamp_ms"]):
            continue
        attrs = row.get("trigger_attributes") or {}
        for threshold in (500, 2_000):
            timestamp = float(start) + threshold
            if timestamp < float(row["timestamp_ms"]) or timestamp >= end:
                continue
            counts[f"alive_after_{threshold}ms"] += 1
            actual = end - timestamp
            live = {**invocation, "active_tool_elapsed_ms": threshold}
            forecasts = {}
            errors = {}
            for name, model in (("reference", reference), ("candidate", candidate)):
                belief = model.predict(_local_features_from_row(
                    row, live,
                    tool_feature_contract=model.tool_feature_contract,
                )).wait_belief
                if (
                    belief is None or belief.kind is not WaitBeliefKind.TOOL
                    or not belief.residual_duration.values
                ):
                    errors[name] = None
                    break
                forecast = belief.residual_duration.quantile(.5)
                forecasts[name] = forecast
                errors[name] = abs(actual - forecast)
            if len(forecasts) != 2:
                counts[f"joint_unavailable_after_{threshold}ms"] += 1
                continue
            sample = {
                "workflow": key[0], "actual_ms": actual, "zero": actual,
                **errors,
                "reference_forecast_ms": forecasts["reference"],
                "candidate_forecast_ms": forecasts["candidate"],
            }
            base = f"after_{threshold}ms"
            dimensions = [base]
            if invocation.get("is_child") is True:
                dimensions.append(f"{base}_child")
                if attrs.get("tool_name") == "execute":
                    dimensions.append(f"{base}_child_execute")
            if end - start >= 2_000:
                dimensions.append(f"{base}_long")
                if invocation.get("is_child") is True:
                    dimensions.append(f"{base}_child_long")
            for dimension in dimensions:
                groups[dimension].append(sample)
    return {
        "semantics": (
            "counterfactual fixed-clock checks conditional on tool survival, "
            "with TOOL_START features frozen except elapsed time; no observed "
            "scheduler decision at the check time and no physical action evidence"
        ),
        "counts": dict(counts),
        "groups": _checkpoint_groups_metrics(groups),
    }


def _ongoing_checkpoints(
    dataset: Path, waits: dict, first_attributes: dict,
    reference: FrontierBeliefModel, candidate: FrontierBeliefModel,
) -> dict:
    by_invocation = defaultdict(list)
    for (workflow, tool_call_id), wait in waits.items():
        start = wait.get("start_ts_ms")
        if (
            type(start) not in (int, float)
            or not math.isfinite(start)
            or (workflow, tool_call_id) not in first_attributes
        ):
            continue
        by_invocation[(workflow, str(wait.get("invocation_id") or ""))].append(
            (float(start), float(wait["terminal_ts_ms"]), tool_call_id)
        )
    for entries in by_invocation.values():
        entries.sort()
    starts = {
        key: [entry[0] for entry in entries]
        for key, entries in by_invocation.items()
    }
    prior_max_end = {}
    for key, entries in by_invocation.items():
        seen_end = float("-inf")
        ends = []
        for _, end, _ in entries:
            ends.append(seen_end)
            seen_end = max(seen_end, end)
        prior_max_end[key] = ends
    checkpoints = (500, 2_000)
    observed = set()
    groups = defaultdict(list)
    counts = defaultdict(int)
    for row in _rows(dataset / "frontier_decision_points.jsonl"):
        workflow = str(row.get("workflow_id") or "")
        timestamp = float(row.get("timestamp_ms") or 0)
        for invocation in row.get("invocations") or ():
            if invocation.get("state") != "wait_tool":
                continue
            identity = workflow, str(invocation.get("invocation_id") or "")
            entries = by_invocation.get(identity)
            if not entries:
                continue
            index = bisect_right(starts[identity], timestamp) - 1
            if index < 0:
                continue
            start, end, call_id = entries[index]
            if not start <= timestamp < end:
                continue
            if prior_max_end[identity][index] > timestamp:
                counts["ambiguous_overlapping_tool_wait"] += 1
                continue
            attrs = first_attributes[(workflow, call_id)]
            for threshold in checkpoints:
                key = workflow, call_id, threshold
                if timestamp - start < threshold or key in observed:
                    continue
                observed.add(key)
                counts[f"first_snapshot_after_{threshold}ms"] += 1
                elapsed = max(0.0, timestamp - start)
                actual = end - timestamp
                # Restore only this live tool's TOOL_START metadata, as the
                # runtime does; other invocations' trigger attributes are never reused.
                snapshot = {
                    **row, "trigger_invocation_id": identity[1],
                    "trigger_attributes": attrs,
                }
                live = {**invocation, "active_tool_elapsed_ms": elapsed}
                errors = {}
                forecasts = {}
                for name, model in (("reference", reference), ("candidate", candidate)):
                    prediction = model.predict(_local_features_from_row(
                        snapshot, live,
                        tool_feature_contract=model.tool_feature_contract,
                    ))
                    belief = prediction.wait_belief
                    if (
                        belief is None or belief.kind is not WaitBeliefKind.TOOL
                        or not belief.residual_duration.values
                    ):
                        errors[name] = None
                        continue
                    forecast = belief.residual_duration.quantile(.5)
                    forecasts[name] = forecast
                    errors[name] = abs(forecast - actual)
                if errors["reference"] is None or errors["candidate"] is None:
                    counts[f"joint_unavailable_after_{threshold}ms"] += 1
                    continue
                sample = {
                    "workflow": workflow, "actual_ms": actual,
                    "zero": actual, **errors,
                    "reference_forecast_ms": forecasts["reference"],
                    "candidate_forecast_ms": forecasts["candidate"],
                }
                base = f"after_{threshold}ms"
                dimensions = [base]
                if invocation.get("is_child") is True:
                    dimensions.append(f"{base}_child")
                    if attrs.get("tool_name") == "execute":
                        dimensions.append(f"{base}_child_execute")
                if end - start >= 2_000:
                    dimensions.append(f"{base}_long")
                    if invocation.get("is_child") is True:
                        dimensions.append(f"{base}_child_long")
                for dimension in dimensions:
                    groups[dimension].append(sample)
    return {
        "semantics": (
            "first observed WAIT_TOOL decision at/after each elapsed threshold, "
            "joined by workflow/invocation/tool-call and causal start metadata; "
            "completed, unambiguous waits only; not a physical-action evaluation"
        ),
        "counts": dict(counts),
        "groups": _checkpoint_groups_metrics(groups),
    }


def compare(
    dataset: Path, reference: FrontierBeliefModel,
    candidate: FrontierBeliefModel,
) -> dict:
    waits = {
        (str(row.get("workflow_id") or ""), str(row.get("tool_call_id") or "")): row
        for row in _rows(dataset / "external_waits.jsonl")
        if row.get("training_eligible_survival") is True
        and row.get("censored") is not True
    }
    seen = set()
    first_attributes = {}
    first_snapshots = {}
    groups = defaultdict(list)
    counts = defaultdict(int)
    for row in _rows(dataset / "frontier_decision_points.jsonl"):
        if row.get("trigger_kind") != "tool_start":
            continue
        attrs = row.get("trigger_attributes") or {}
        key = str(row.get("workflow_id") or ""), str(attrs.get("tool_call_id") or "")
        wait = waits.get(key)
        if wait is None or key in seen:
            continue
        invocation = next((
            item for item in row.get("invocations") or ()
            if item.get("invocation_id") == row.get("trigger_invocation_id")
            and item.get("state") == "wait_tool"
        ), None)
        if invocation is None or wait.get("invocation_id") != row.get("trigger_invocation_id"):
            counts["identity_or_state_unavailable"] += 1
            continue
        seen.add(key)
        first_attributes[key] = dict(attrs)
        first_snapshots[key] = (row, invocation)
        actual = max(0.0, float(wait["terminal_ts_ms"]) - float(row["timestamp_ms"]))
        errors = {}
        forecasts = {}
        for name, model in (("reference", reference), ("candidate", candidate)):
            features = _local_features_from_row(
                row, invocation, tool_feature_contract=model.tool_feature_contract,
            )
            prediction = model.predict(features)
            belief = prediction.wait_belief
            if (
                belief is None or belief.kind is not WaitBeliefKind.TOOL
                or not belief.residual_duration.values
            ):
                errors[name] = None
                counts[f"{name}_unavailable"] += 1
                continue
            value = belief.residual_duration.quantile(.5)
            errors[name] = abs(value - actual) if math.isfinite(value) else None
            forecasts[name] = value
        if errors["reference"] is None or errors["candidate"] is None:
            counts["not_jointly_available"] += 1
            continue
        sample = {
            "workflow": key[0], "actual_ms": actual, "zero": actual, **errors,
            **{f"{name}_forecast_ms": forecast
               for name, forecast in forecasts.items()},
        }
        dimensions = ["all"]
        child = invocation.get("is_child") is True
        dimensions.append("child" if child else "root")
        if child:
            is_execute = attrs.get("tool_name") == "execute"
            has_prior = (
                is_execute
                and attrs.get("previous_same_input_status") == "success"
                and type(attrs.get("previous_same_input_duration_ms"))
                in (int, float)
            )
            has_project_prior = (
                is_execute
                and type(attrs.get("project_class_duration_median_ms"))
                in (int, float)
                and int(attrs.get("project_class_completed_support") or 0) >= 16
            )
            dimensions.append("child_with_prior" if has_prior else "child_cold")
            if is_execute:
                dimensions.append("child_execute")
                dimensions.append(
                    "child_execute_with_prior" if has_prior
                    else "child_execute_cold"
                )
            if not has_prior:
                dimensions.append(
                    "child_cold_with_project_prior"
                    if has_project_prior else "child_cold_no_project_prior"
                )
            if actual >= 2_000:
                dimensions.append("child_long")
                if is_execute:
                    dimensions.append("child_execute_long")
                dimensions.append(
                    "child_long_with_prior" if has_prior else "child_long_cold"
                )
                if not has_prior:
                    dimensions.append(
                        "child_long_cold_with_project_prior"
                        if has_project_prior else "child_long_cold_no_project_prior"
                    )
        for dimension in dimensions:
            groups[dimension].append(sample)
    return {
        "status": "first_tool_start_matched_model_comparison_not_physical_gain",
        "dataset": str(dataset.resolve()),
        "reference_contract": reference.tool_feature_contract,
        "candidate_contract": candidate.tool_feature_contract,
        "completed_waits": len(waits),
        "matched_tool_starts": len(seen),
        "counts": dict(counts),
        "groups": {
            group: {
                "joint_samples": len(samples),
                "zero_remaining_baseline": _metrics(samples, "zero"),
                "reference": _metrics(samples, "reference"),
                "candidate": _metrics(samples, "candidate"),
                "tool_start_trigger_quality": {
                    name: _start_trigger_quality(samples, name)
                    for name in ("reference", "candidate")
                },
            }
            for group, samples in sorted(groups.items())
        },
        "ongoing_checkpoints": _ongoing_checkpoints(
            dataset, waits, first_attributes, reference, candidate,
        ),
        "fixed_clock_checkpoints": _fixed_clock_checkpoints(
            waits, first_snapshots, reference, candidate,
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--reference-model", type=Path, required=True)
    parser.add_argument("--candidate-model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reference = FrontierBeliefModel.load(args.reference_model)
    candidate = FrontierBeliefModel.load(args.candidate_model)
    result = compare(args.dataset_dir, reference, candidate)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
