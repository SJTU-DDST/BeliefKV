"""Fit small whole-workflow priors; preserve the trained progress model and held-out split."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_task_encoder import (
    WorkflowTaskEncoder, encoder_contract, task_features,
)
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from beliefkv.predictor.workflow_work_prior import KIND, TotalWorkPrior
from scripts.improve_workflow_work_distribution import (
    calibration_rejections, selection_score, slices,
)
from scripts.train_workflow_work import write
from scripts.train_workflow_work_distribution import split_rows


def fit_prior(vectors, totals):
    """Choose shrinkage by leave-one-workflow-out error in training only."""
    import numpy as np

    x, y = np.asarray(vectors, dtype=float), np.log1p(totals)
    center, bias = x.mean(0), float(y.mean())
    centered, target = x - center, y - bias
    kernel = centered @ centered.T
    candidates = []
    for alpha in (.001, .01, .1, 1., 10., 100.):
        inverse = np.linalg.inv(kernel + alpha * np.eye(len(y)))
        dual = inverse @ target
        leverage = np.diag(kernel @ inverse) + 1. / len(y)
        residual = (target - kernel @ dual) / (1. - leverage)
        candidates.append({
            "alpha": alpha, "LOO_log_RMSE": float(np.mean(residual ** 2) ** .5),
            "coefficient": (centered.T @ dual).tolist(),
        })
    best = min(candidates, key=lambda row: row["LOO_log_RMSE"])
    return {
        "center": center.tolist(), "coefficient": best["coefficient"], "bias": bias,
        "log_total_sigma": max(.1, best["LOO_log_RMSE"]),
    }, [{key: row[key] for key in ("alpha", "LOO_log_RMSE")} for row in candidates]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "baseline-model", "baseline-evaluation", "encoder", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--encoder-revision", required=True)
    parser.add_argument("--calibrate-location-scale", action="store_true")
    parser.add_argument("--initial-only", action="store_true")
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve prior fits and held-out outcomes")
    inputs = {str(path.resolve()): sha256_file(path) for path in (
        args.dataset, args.cohort, args.baseline_model, args.baseline_evaluation,
    )}
    data, evaluation, baseline, cohort = (
        json.loads(path.read_bytes()) for path in (
            args.dataset, args.baseline_evaluation, args.baseline_model, args.cohort,
        )
    )
    if (
        evaluation["model_sha256"] != inputs[str(args.baseline_model.resolve())]
        or evaluation["inputs_sha256"].get(str(args.dataset.resolve()))
        != inputs[str(args.dataset.resolve())]
    ):
        raise ValueError("fit priors on the exact bound baseline dataset")
    train, calibration, test, split = split_rows(data["rows"], cohort["measured_workflow_ids"])
    if split != evaluation["split_contract"]:
        raise ValueError("prior training must preserve the complete-workflow holdout")
    args.output_dir.mkdir(parents=True)
    plan = {
        "basis": ["task_structure_v1", "semantic_and_structure_v1"],
        "initial_blends": [.25, .5, 1.], "blend_work": baseline["work_thresholds"][0],
        "ridge_selection": "training_whole_workflow_LOO_log_RMSE",
        "candidate_selection": "unchanged_calibration_gates_then_early_ranking_score",
        "test_labels_used_for_selection": False, "split_contract": split,
        "calibrate_location_scale_on_independent_calibration_workflows": args.calibrate_location_scale,
        "prior_activation": "before_first_model_service" if args.initial_only else "service_decay",
        "inputs_sha256": inputs, "GPU_action": False,
    }
    write(args.output_dir / "plan.json", plan)
    initial = [row for row in train if row["snapshot"] == 0]
    totals = [row["total_work"] for row in initial]
    models = {"baseline": baseline}
    metrics = {"baseline": slices(WorkflowWorkModel(baseline), calibration)}
    summaries = {
        "baseline": {
            "calibration": metrics["baseline"], "selection_score": selection_score(metrics["baseline"]),
            "decision_eligible": True,
        },
    }
    contract = encoder_contract(args.encoder, revision=args.encoder_revision)
    for basis in plan["basis"]:
        print("fitting_prior: " + basis, flush=True)
        encoder = WorkflowTaskEncoder(contract) if basis == "semantic_and_structure_v1" else None
        vectors = [
            encoder.encode(row["text"])[0] if encoder else task_features(row["text"])
            for row in initial
        ]
        prior, search = fit_prior(vectors, totals)
        write(args.output_dir / basis / "training_LOO.json", search)
        if args.calibrate_location_scale:
            import math
            import numpy as np

            calibration_initial = [row for row in calibration if row["snapshot"] == 0]
            provisional = TotalWorkPrior({
                **prior, "kind": KIND, "encoding": basis,
                "task_encoder": contract if encoder else None,
                "initial_blend": 1., "blend_work": plan["blend_work"],
                "target": "log1p_complete_logical_model_work",
            })
            residual = np.asarray([
                math.log1p(row["total_work"]) - provisional.task_mean(row["text"])[0]
                for row in calibration_initial
            ])
            prior["bias"] += float(residual.mean())
            prior["log_total_sigma"] = max(.1, float(residual.std()))
            write(args.output_dir / basis / "location_scale_calibration.json", {
                "workflows": len(calibration_initial), "log_mean_correction": float(residual.mean()),
                "calibrated_log_sigma": prior["log_total_sigma"],
                "evaluation_workflows_used": False,
            })
        for blend in plan["initial_blends"]:
            name = basis + "_blend" + str(blend)
            raw = deepcopy(baseline)
            raw["total_work_prior"] = {
                **prior, "kind": KIND, "encoding": basis,
                "task_encoder": contract if encoder else None,
                "initial_blend": blend, "blend_work": plan["blend_work"],
                "target": "log1p_complete_logical_model_work",
                "activation": plan["prior_activation"],
            }
            raw["metadata"].update(
                total_prior_training_workflows=len(initial),
                total_prior_fit_uses_training_only=True,
                online_prior_conditioning_uses_observed_service_only=True,
                evaluation_labels_used_for_candidate_selection=False,
                total_prior_location_scale_calibrated=args.calibrate_location_scale,
            )
            values = slices(WorkflowWorkModel(raw), calibration)
            reasons = calibration_rejections(values, raw)
            if values["first_third"]["long_to_short_rate"] > .1:
                reasons.append("early_calibration_long_to_short_above_0_10")
            raw["metadata"].update(
                decision_eligible=not reasons, decision_rejection_reasons=reasons,
                calibration_distribution_metrics=values["all"],
            )
            models[name], metrics[name] = raw, values
            summaries[name] = {
                "calibration": values, "selection_score": selection_score(values),
                "decision_eligible": not reasons, "decision_rejection_reasons": reasons,
            }
            write(args.output_dir / name / "model.json", raw)
            print(json.dumps({
                "candidate": name, "initial_accuracy": values["initial"]["initial_accuracy"],
                "initial_pair_accuracy": values["initial_pair_ranking"]["accuracy"],
                "early_accuracy": values["first_third"]["workflow_equal_weight_accuracy"],
                "long_to_short": values["all"]["long_to_short_rate"],
                "selection_score": selection_score(values), "reasons": reasons,
            }), flush=True)
    selected = max(
        (name for name, row in summaries.items() if row["decision_eligible"]),
        key=lambda name: summaries[name]["selection_score"],
    )
    write(args.output_dir / "selection.json", {"selected": selected, "calibration_summary": summaries})
    print("selected_before_test_evaluation: " + selected, flush=True)
    held_out = {name: slices(WorkflowWorkModel(models[name]), test) for name in {selected, "baseline"}}
    write(args.output_dir / "model.json", models[selected])
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("a prior training input changed")
    write(args.output_dir / "evaluation.json", {
        "selected": selected, "held_out": held_out, "calibration_summary": summaries,
        "split_contract": split, "inputs_sha256": inputs,
        "model_sha256": sha256_file(args.output_dir / "model.json"),
        "candidate_selection_used_test_labels": False,
        "validation_scope": "fixed_development_workflow_holdout_not_new_confirmatory_test",
        "GPU_action": False, "JCT_gain_proven": False,
    })
    print(json.dumps({
        name: {
            "initial_accuracy": row["initial"]["initial_accuracy"],
            "initial_pair_accuracy": row["initial_pair_ranking"]["accuracy"],
            "early_accuracy": row["first_third"]["workflow_equal_weight_accuracy"],
            "all_accuracy": row["all"]["workflow_equal_weight_accuracy"],
            "long_to_short": row["all"]["long_to_short_rate"],
        } for name, row in held_out.items()
    }), flush=True)


if __name__ == "__main__":
    main()
