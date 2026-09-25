#!/usr/bin/env python3
"""Stream matched first-tool decisions to compare two pinned timing artifacts."""

from __future__ import annotations

import argparse
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
        actual = max(0.0, float(wait["terminal_ts_ms"]) - float(row["timestamp_ms"]))
        errors = {}
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
        if errors["reference"] is None or errors["candidate"] is None:
            counts["not_jointly_available"] += 1
            continue
        sample = {
            "workflow": key[0], "actual_ms": actual, **errors
        }
        dimensions = ["all"]
        child = invocation.get("is_child") is True
        dimensions.append("child" if child else "root")
        if child:
            has_prior = attrs.get("previous_same_input_status") == "success"
            dimensions.append("child_with_prior" if has_prior else "child_cold")
            if actual >= 2_000:
                dimensions.append("child_long")
                dimensions.append(
                    "child_long_with_prior" if has_prior else "child_long_cold"
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
                "reference": _metrics(samples, "reference"),
                "candidate": _metrics(samples, "candidate"),
            }
            for group, samples in sorted(groups.items())
        },
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
