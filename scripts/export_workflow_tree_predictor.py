"""Export the selected CPU work tree to bounded NumPy serving inference after paired review."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_conditional_tree import KIND
from beliefkv.predictor.workflow_work import WorkflowWorkModel, before_observed_work
from scripts.audit_workflow_call_decomposition import selected_candidate_model
from scripts.train_workflow_branch_call_trees import matrix, work_probabilities
from scripts.train_workflow_work import probabilities, write
from scripts.train_workflow_work_progress_trees import portable_tree


def portable_head(candidate):
    if candidate.head["target"] != "work":
        raise ValueError("only the evaluated direct-work readout has a serving export")
    return {
        "kind": KIND, "features": list(candidate.head["features"]),
        "representation_sha256": candidate.head["representation_sha256"],
        "trees": [portable_tree(tree["tree_structure"])
                  for tree in candidate.trees[0].dump_model()["tree_info"]],
        "maximum_depth": candidate.head["parameters"]["max_depth"], "blend": candidate.blend,
        "calibration_offset": candidate.head["calibration_offset"],
        "calibration_sigma": candidate.head["calibration_sigma"],
    }


def review_improvement(evaluation, competition):
    selected = evaluation["selected"]
    old, new = evaluation["held_out"]["retained"], evaluation["held_out"][selected]
    metrics = competition["models"]
    old_actual = metrics["retained"]["both_held_out_measured"]["metrics"]["model"]
    new_actual = metrics["selected_candidate"]["both_held_out_measured"]["metrics"]["model"]
    if (
        new["ready_cross_progress_pairs"]["accuracy"] <= old["ready_cross_progress_pairs"]["accuracy"]
        or new["all"]["workflow_equal_weight_accuracy"] < old["all"]["workflow_equal_weight_accuracy"]
        or new["all"]["long_to_short_rate"] > old["all"]["long_to_short_rate"]
        or any(new_actual[key] <= old_actual[key] for key in (
            "boundary_equal_weight_accuracy", "unique_workflow_pair_equal_weight_accuracy",
        ))
    ):
        raise ValueError("candidate lacks the paired development and actual-competition improvement")


def main():
    import numpy as np

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("candidate-model", "candidate-evaluation", "competition", "baseline-model",
                 "dataset", "cohort", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve previous serving exports")
    paths = (
        args.candidate_model, args.candidate_evaluation, args.competition,
        args.baseline_model, args.dataset, args.cohort,
    )
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    package, evaluation, competition, baseline, data, _cohort = (
        json.loads(path.read_bytes()) for path in paths
    )
    if (
        evaluation["model_sha256"] != pins[str(args.candidate_model.resolve())]
        or evaluation["inputs_sha256"].get(str(args.dataset.resolve())) != pins[str(args.dataset.resolve())]
        or evaluation["inputs_sha256"].get(str(args.cohort.resolve())) != pins[str(args.cohort.resolve())]
        or competition.get("offline_tree_readout_adapter_used") is not True
        or competition.get("offline_tree_serving_eligibility_granted") is not False
        or any(competition["inputs_sha256"].get(str(path.resolve())) != pins[str(path.resolve())]
               for path in (
                   args.candidate_model, args.candidate_evaluation, args.baseline_model, args.dataset, args.cohort,
               ))
    ):
        raise ValueError("require the exact selected package and its already completed paired audit")
    review_improvement(evaluation, competition)
    candidate = selected_candidate_model(package, evaluation, baseline, offline_tree=True)
    for path, digest in data["sources_sha256"].items():
        if path in pins and pins[path] != digest:
            raise ValueError("serving export source identities conflict")
        pins[path] = digest
    for path in (
        Path(__file__), ROOT / "beliefkv/predictor/workflow_conditional_tree.py",
        ROOT / "beliefkv/predictor/workflow_progress_tree.py",
        ROOT / "beliefkv/predictor/workflow_work.py",
        ROOT / "beliefkv/predictor/workflow_work_readout.py",
        ROOT / "scripts/audit_workflow_call_decomposition.py",
        ROOT / "scripts/train_workflow_branch_call_trees.py",
        ROOT / "scripts/train_workflow_work_progress_trees.py",
    ):
        pins[str(path.resolve())] = sha256_file(path)
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a serving export source changed")
    raw = deepcopy(baseline)
    raw["conditional_progress_tree_head"] = portable_head(candidate)
    raw["metadata"].update(
        split_contract=evaluation["expanded_split"],
        calibration_distribution_metrics=deepcopy(
            evaluation["calibration_summary"][evaluation["selected"]]["calibration"]["all"],
        ),
        conditional_progress_readout={
            "training_executions": evaluation["expanded_training_executions"],
            "training_projects": evaluation["training_projects"],
            "trunk_training_executions": evaluation["previous_training_executions"],
            "calibration_method": "fixed_workflow_heldout_log_work_residual_offset_and_sigma",
            "selection_used_development_labels": False,
            "selection_score": evaluation["calibration_summary"][evaluation["selected"]]["selection_score"],
            "serving_requires_lightgbm": False, "representation_evaluated_once_per_prediction": True,
        },
        validation_scope="reused_fixed_development_holdout_not_confirmatory",
        decision_eligible=True, decision_rejection_reasons=[],
    )
    model = WorkflowWorkModel(raw)
    rows = data["rows"]
    x, valid = matrix(candidate.base, rows)
    observed = np.asarray([row["features"] for row in rows])
    before = np.asarray([before_observed_work(row["features"]) for row in rows])
    use = valid & ~before
    original = probabilities(candidate.base, rows)
    predicted = candidate.predicted_log_work(x, observed)
    own = work_probabilities(
        predicted, candidate.thresholds, candidate.head["calibration_offset"],
        candidate.head["calibration_sigma"],
    )
    expected = original.copy()
    expected[use] = (1. - candidate.blend) * original[use] + candidate.blend * own[use]
    actual = probabilities(model, rows)
    maximum_error = float(np.max(np.abs(actual - expected)))
    if not np.allclose(actual, expected, atol=1e-12, rtol=0.) or (
        not np.array_equal(actual[before], original[before])
    ):
        raise ValueError("portable serving probabilities differ from the selected CPU predictions")
    if any(raw[key] != baseline[key] for key in (
        "initial_state_head", "work_thresholds", "admission_cost_profile",
        "free_dispatch_coalescing_window_ms", "distribution_contract",
    )):
        raise ValueError("serving export changed the retained initial head, scheduler or support contract")
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a serving export input changed during parity checking")
    write(args.output_dir / "model.json", raw)
    write(args.output_dir / "evaluation.json", {
        **evaluation, "model_sha256": sha256_file(args.output_dir / "model.json"),
        "offline_candidate_model_sha256": pins[str(args.candidate_model.resolve())],
        "offline_candidate_evaluation_sha256": pins[str(args.candidate_evaluation.resolve())],
        "inputs_sha256": pins, "split_contract": evaluation["expanded_split"],
        "deployment_adapter_implemented": True, "GPU_action": False, "JCT_gain_proven": False,
        "serving_export_parity": {
            "source_snapshots": len(rows), "post_initial_snapshots": int(use.sum()),
            "initial_condition_snapshots": int(before.sum()), "maximum_abs_probability_error": maximum_error,
            "initial_condition_probabilities_bitwise_unchanged": True,
            "same_thresholds_cost_profile_dispatch_window_and_support_contract": True,
            "serving_lightgbm_dependency": False,
        },
    })
    print(json.dumps({
        "selected": evaluation["selected"], "source_snapshots": len(rows),
        "maximum_abs_probability_error": maximum_error, "GPU_action": False,
    }), flush=True)


if __name__ == "__main__":
    main()
