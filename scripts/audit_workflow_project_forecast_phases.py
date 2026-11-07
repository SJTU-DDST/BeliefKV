"""Locate R5 forecast errors in existing project-isolated folds without refitting."""

import argparse
from collections import defaultdict
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_continuous_work import remaining_work_weight
from beliefkv.predictor.workflow_work import R5_FEATURES, before_observed_work
from beliefkv.predictor.workflow_work_training import project_task_weights
from scripts.evaluate_workflow_continuous_projects import ranking_metrics
from scripts.train_workflow_work import write


PHASES = ("all", "before_model_progress", "early_progress", "middle_progress", "late_progress")


def phase(row):
    if before_observed_work(row["features"]):
        return "before_model_progress"
    attained = row["attained_work"] / row["total_work"]
    return (
        "early_progress" if attained < 1. / 3.
        else "middle_progress" if attained < 2. / 3.
        else "late_progress"
    )


def validate_rows(rows):
    seen, ownership = set(), {}
    for row in rows:
        identity = row["workflow"], row["snapshot"]
        owner = row["project"], row["source_workflow_sha256"]
        values = [row[name] for name in (
            "attained_work", "remaining_work", "total_work", "reference_work",
            "raw_binned_weight", "raw_continuous_weight", "binned_log_work", "continuous_log_work",
        )]
        if (
            identity in seen or identity[0] in ownership and ownership[identity[0]] != owner
            or any(type(value) is not str or not value for value in (identity[0], *owner))
            or type(identity[1]) is not int or identity[1] < 0
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in values)
            or min(values[:3]) < 0 or row["total_work"] <= 0 or row["reference_work"] <= 0
            or not math.isclose(
                row["total_work"], row["attained_work"] + row["remaining_work"],
                abs_tol=1e-6, rel_tol=0.,
            )
            or not 0 <= row["raw_binned_weight"] <= 1
            or not 0 <= row["raw_continuous_weight"] <= 1
            or len(row["features"]) != len(R5_FEATURES)
            or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                   for value in row["features"])
            or type(row["supported"]) is not bool
            or type(row["true_class"]) is not int or row["true_class"] not in (0, 1, 2)
        ):
            raise ValueError("phase audits require unique finite conserved causal forecast rows")
        seen.add(identity)
        ownership[identity[0]] = owner
    if not rows:
        raise ValueError("phase audits require evaluated project forecasts")


def summarize(rows):
    import numpy as np

    validate_rows(rows)
    weights = project_task_weights(rows, np.ones(len(rows)))
    target = np.asarray([
        remaining_work_weight(row["remaining_work"], row["reference_work"]) for row in rows
    ])
    result = {
        "projects": len({row["project"] for row in rows}),
        "executions": len({row["workflow"] for row in rows}),
        "snapshots": len(rows),
        "supported_project_equal_mass": float(np.sum(weights * [row["supported"] for row in rows])),
    }
    for method in ("binned", "continuous"):
        scores = [row["raw_" + method + "_weight"] for row in rows]
        result[method] = {
            "raw_project_equal_utility_MSE": float(np.sum(
                weights * (np.asarray(scores) - target) ** 2,
            )),
            "raw_project_equal_log_work_RMSE": float(np.sqrt(np.sum(
                weights * (
                    np.asarray([row[method + "_log_work"] for row in rows])
                    - np.log1p([row["remaining_work"] for row in rows])
                ) ** 2,
            ))),
            "raw_initial": ranking_metrics(rows, scores, phase="initial"),
            "raw_ready": ranking_metrics(rows, scores, phase="ready"),
            "raw_same_true_class_ready": ranking_metrics(
                rows, scores, phase="ready", within_class_only=True,
            ),
        }
    return result


def inspect(directory):
    paths = [directory / "plan.json", directory / "evaluation.json"]
    pins = {
        str(path.resolve()): sha256_file(path) for path in (
            Path(__file__), ROOT / "scripts/evaluate_workflow_continuous_projects.py",
            ROOT / "beliefkv/predictor/workflow_work_training.py",
            ROOT / "beliefkv/predictor/workflow_continuous_work.py",
            ROOT / "beliefkv/predictor/workflow_work.py",
        )
    }

    def load(path):
        content = path.read_bytes()
        import hashlib

        pins[str(path.resolve())] = hashlib.sha256(content).hexdigest()
        return json.loads(content)

    plan, evaluation = (load(path) for path in paths)
    count = plan["outer_folds"]
    if (
        len(plan["folds"]) != count or len(evaluation["folds"]) != count
        or evaluation.get("all_projects_evaluated_once") is not True
        or plan.get("vocabulary_scaling_both_MLPs_and_tree_refitted_inside_each_fold") is not True
        or plan.get("calibration_uses_a_separate_project_fold") is not True
        or plan.get("original_calibration_safety_gates_retained") is not True
    ):
        raise ValueError("use complete separately refitted project-isolated forecast folds")
    seen_projects, reports = set(), []
    for index, split in enumerate(plan["folds"]):
        groups = [set(split[name]["projects"]) for name in ("training", "calibration", "evaluation")]
        if (
            any(left & right for i, left in enumerate(groups) for right in groups[i + 1:])
            or seen_projects & groups[2]
        ):
            raise ValueError("whole projects must stay isolated and be evaluated once")
        seen_projects.update(groups[2])
        prediction_path = directory / f"fold_{index}" / "predictions.json"
        summary_path = directory / f"fold_{index}" / "evaluation.json"
        paths.extend((prediction_path, summary_path))
        rows, summary = (load(path) for path in (prediction_path, summary_path))
        if (
            summary["fold"] != index or evaluation["folds"][index] != summary
            or {row["project"] for row in rows} != groups[2]
            or {row["source_workflow_sha256"] for row in rows}
            != set(split["evaluation"]["executions"])
        ):
            raise ValueError("phase forecasts differ from the registered outer evaluation population")
        validate_rows(rows)
        phases = defaultdict(list)
        for row in rows:
            phases["all"].append(row)
            phases[phase(row)].append(row)
        reports.append({
            "fold": index,
            "calibration_decision_eligible": summary["calibration_decision_eligible"],
            "calibration_rejections": summary["calibration_rejections"],
            "phases": {name: summarize(phases[name]) if phases[name] else None for name in PHASES},
        })
    if len(seen_projects) != plan["projects"]:
        raise ValueError("outer forecast folds omit original projects")
    import numpy as np

    means = {}
    for name in PHASES:
        means[name] = {}
        for method in ("binned", "continuous"):
            metrics = {}
            for population in ("raw_initial", "raw_ready", "raw_same_true_class_ready"):
                values = [
                    report["phases"][name][method][population]["project_pair_equal_weight_accuracy"]
                    for report in reports if report["phases"][name] is not None
                ]
                values = [value for value in values if value is not None]
                metrics[population] = {
                    "mean_fold_project_pair_accuracy": float(np.mean(values)) if values else None,
                    "folds_with_pairs": len(values),
                }
            means[name][method] = metrics
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a project forecast or its phase auditor changed during analysis")
    return {
        "folds": reports, "mean_of_within_fold_metrics": means,
        "inputs_sha256": pins,
        "outer_projects": len(seen_projects),
        "phases_use_future_total_only_as_retrospective_analysis_strata": True,
        "all_scores_are_raw_diagnostics_not_deployment_eligibility": True,
        "cross_fold_predictions_compared_against_each_other": False,
        "correlated_snapshots_are_not_independent_projects": True,
        "new_model_fitted": False, "GPU_action": False, "JCT_improvement_claimed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fold-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output.exists():
        raise ValueError("hide CUDA and preserve prior prediction evidence")
    result = inspect(args.fold_dir)
    write(args.output, result)
    print(json.dumps(result["mean_of_within_fold_metrics"]), flush=True)


if __name__ == "__main__":
    main()
