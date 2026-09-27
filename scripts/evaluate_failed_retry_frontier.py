#!/usr/bin/env python3
"""Compare two frozen frontier models on project-disjoint failed-input retries."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from statistics import median
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.predictor.structured_frontier import (
    FrontierBeliefModel,
    _local_features_from_row,
    _target_eligible,
    _target_right_censored,
    load_evaluation_rows,
)


def _score(samples: list[dict]) -> dict:
    if not samples:
        return {"count": 0, "workflow_count": 0}
    by_workflow: dict[str, list[dict]] = defaultdict(list)
    for sample in samples:
        by_workflow[sample["workflow"]].append(sample)
    result = {"count": len(samples), "workflow_count": len(by_workflow)}
    for model in ("baseline", "candidate"):
        available = [sample for sample in samples if sample[model] is not None]
        errors = sorted(abs(sample["actual_ms"] - sample[model])
                        for sample in available)
        probabilities = [
            (sample[f"{model}_success_probability"],
             sample["status"] == "success")
            for sample in samples
            if sample[f"{model}_success_probability"] is not None
        ]
        result[model] = {
            "available": len(available),
            "absolute_error_p50_ms": median(errors) if errors else None,
            "absolute_error_p90_ms": (
                errors[math.ceil(.9 * len(errors)) - 1] if errors else None
            ),
            "within_500ms": sum(error <= 500 for error in errors),
            "workflow_median_error_p50_ms": (
                median([
                    median(abs(sample["actual_ms"] - sample[model])
                           for sample in group if sample[model] is not None)
                    for group in by_workflow.values()
                    if any(sample[model] is not None for sample in group)
                ]) if available else None
            ),
            "terminal_probability_count": len(probabilities),
            "success_brier": (
                sum((probability - int(success)) ** 2
                    for probability, success in probabilities) / len(probabilities)
                if probabilities else None
            ),
            "success_predicted_at_least_0_9": sum(
                probability >= .9 for probability, _ in probabilities
            ),
            "false_success_predicted_at_least_0_9": sum(
                probability >= .9 and not success
                for probability, success in probabilities
            ),
            "natural_success_predicted_at_least_0_5": sum(
                probability >= .5 and success
                for probability, success in probabilities
            ),
        }
    return result


def evaluate(
    rows: list[dict], baseline: FrontierBeliefModel,
    candidate: FrontierBeliefModel,
) -> dict:
    first: dict[tuple[str, str, str], dict] = {}
    for row in sorted(rows, key=lambda item: float(item["timestamp_ms"])):
        attrs = row.get("trigger_attributes") or {}
        invocation = str(row.get("trigger_invocation_id") or "")
        signature = str(attrs.get("input_sha256") or "")
        if (
            row.get("training_eligible") is not True
            or row.get("trigger_kind") != "tool_start"
            or attrs.get("tool_name") != "execute"
            or attrs.get("is_child") is not True
            or attrs.get("previous_same_input_status") != "error"
            or not invocation or not signature
            or type(attrs.get("previous_same_input_duration_ms")) not in (int, float)
            or not math.isfinite(attrs["previous_same_input_duration_ms"])
            or attrs["previous_same_input_duration_ms"] <= 100
        ):
            continue
        key = str(row["workflow_id"]), invocation, signature
        if key in first:
            continue
        features = next(
            (item for item in row["invocations"]
             if item.get("invocation_id") == invocation), None
        )
        label = next(
            (item for item in row["labels"]
             if item.get("invocation_id") == invocation), None
        )
        if (
            features is None or label is None
            or not _target_eligible(label, "external_wait")
            or _target_right_censored(label, "external_wait")
            or label.get("next_boundary_status") not in ("success", "error")
            or type(label.get("next_boundary_delay_ms")) not in (int, float)
            or not math.isfinite(label["next_boundary_delay_ms"])
            or label["next_boundary_delay_ms"] <= 0
        ):
            continue
        predictions = {}
        for name, model in (("baseline", baseline), ("candidate", candidate)):
            local = _local_features_from_row(
                row, features, tool_feature_contract=model.tool_feature_contract
            )
            if local.state != "wait_tool":
                break
            prediction = model.predict(local)
            wait = prediction.wait_belief
            predictions[name] = (
                wait.residual_duration.quantile(.5)
                if wait.residual_duration.values else None
            )
            predictions[f"{name}_support"] = wait.support_detail
            predictions[f"{name}_success_probability"] = (
                prediction.tool_terminal_distribution.get("success")
            )
        else:
            first[key] = {
                "workflow": key[0],
                "status": label["next_boundary_status"],
                "actual_ms": float(label["next_boundary_delay_ms"]),
                **predictions,
            }
    samples = list(first.values())
    return {
        "status": "project_disjoint_development_not_sealed_action_evidence",
        "first_per_workflow_invocation_input": _score(samples),
        "natural_success": _score([
            row for row in samples if row["status"] == "success"
        ]),
        "natural_error": _score([
            row for row in samples if row["status"] == "error"
        ]),
        "candidate_failed_input_support": sum(
            row["candidate_support"] == "same_failed_input_completed"
            for row in samples
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    models = []
    projects = []
    for path in (args.baseline, args.candidate):
        artifact = json.loads(path.read_text(encoding="utf-8"))
        models.append(FrontierBeliefModel.from_dict(artifact))
        projects.append(set((artifact.get("metadata") or {}).get("fit_projects") or ()))
    rows, _ = load_evaluation_rows(
        [args.dataset_dir], split="calibration", allow_formal_local=True
    )
    evaluation_projects = {
        str(row.get("project") or "unknown") for row in rows
    }
    if not all(project and project.isdisjoint(evaluation_projects)
               for project in projects):
        raise ValueError("calibration projects overlap model fit or provenance missing")
    report = evaluate(rows, *models)
    if (
        report["first_per_workflow_invocation_input"]["count"] == 0
        or report["candidate_failed_input_support"] == 0
    ):
        raise ValueError("no labeled failed retries with candidate timing support")
    report["evaluation_projects"] = sorted(evaluation_projects)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
