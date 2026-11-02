"""Merge a small seed ensemble into one portable R5 MLP and calibrate it."""

import argparse
from collections import Counter
from copy import deepcopy
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import R5_SCHEMA, WorkflowWorkModel
from scripts.improve_workflow_work_distribution import calibration_rejections, selection_score, slices
from scripts.train_workflow_work import fit, probabilities, write
from scripts.train_workflow_work_distribution import split_rows


def merge(models):
    """Block-diagonal hidden layers compute the exact mean calibrated logits."""
    import numpy as np

    reference = models[0]
    for model in models:
        if any(model.get(name) != reference.get(name) for name in (
            "schema", "features", "vocabulary", "idf", "center", "scale",
            "work_thresholds", "distribution_contract",
        )) or any(model.get(name) is not None for name in (
            "task_encoder", "total_work_prior", "task_text_encoding",
        )):
            raise ValueError("merge only matching feature/label encodings without optional priors")
    result = deepcopy(reference)
    hidden = [np.asarray(model["hidden_weight"]) for model in models]
    weight = np.zeros((sum(item.shape[0] for item in hidden), sum(item.shape[1] for item in hidden)))
    first = second = 0
    for item in hidden:
        weight[first:first + item.shape[0], second:second + item.shape[1]] = item
        first += item.shape[0]
        second += item.shape[1]
    result.update(
        static_weight=np.concatenate([model["static_weight"] for model in models], axis=1).tolist(),
        dynamic_weight=np.concatenate([model["dynamic_weight"] for model in models], axis=1).tolist(),
        bias=np.concatenate([model["bias"] for model in models]).tolist(),
        hidden_weight=weight.tolist(),
        hidden_bias=np.concatenate([model["hidden_bias"] for model in models]).tolist(),
        output_weight=np.concatenate([
            np.asarray(model["output_weight"]) / model["temperature"] / len(models) for model in models
        ]).tolist(),
        output_bias=sum(
            np.asarray(model["output_bias"]) / model["temperature"] / len(models) for model in models
        ).tolist(),
        temperature=1.,
    )
    result["metadata"].update(
        ensemble_kind="block_diagonal_mean_calibrated_logits",
        ensemble_members=[{
            "seed": model["metadata"]["seed"],
            "hidden_width": len(model["bias"]),
            "temperature": model["temperature"],
        } for model in models],
        decision_eligible=True,
    )
    return result


def calibrate(raw, rows):
    import numpy as np
    from scipy.optimize import minimize_scalar

    model = WorkflowWorkModel(raw)
    logs = np.log(np.maximum(probabilities(model, rows), 1e-12))
    labels = np.searchsorted(raw["work_thresholds"], [row["remaining_work"] for row in rows], side="right")
    counts = Counter(row["workflow"] for row in rows)
    weights = np.asarray([1. / counts[row["workflow"]] / len(counts) for row in rows])

    def nll(log_temperature):
        scores = logs / math.exp(log_temperature)
        scores -= scores.max(axis=1, keepdims=True)
        return float(np.sum(weights * (
            np.log(np.exp(scores).sum(axis=1)) - scores[np.arange(len(rows)), labels]
        )))

    raw["temperature"] = math.exp(minimize_scalar(nll, bounds=(-1.6, 3.), method="bounded").x)
    return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "baseline-model", "baseline-evaluation", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve ensemble attempts")
    inputs = {str(path.resolve()): sha256_file(path) for path in (
        args.dataset, args.cohort, args.baseline_model, args.baseline_evaluation,
    )}
    data, cohort, baseline, evaluation = (
        json.loads(path.read_bytes()) for path in (
            args.dataset, args.cohort, args.baseline_model, args.baseline_evaluation,
        )
    )
    if (
        evaluation["model_sha256"] != inputs[str(args.baseline_model.resolve())]
        or evaluation["inputs_sha256"].get(str(args.dataset.resolve()))
        != inputs[str(args.dataset.resolve())]
    ):
        raise ValueError("retain the exact baseline data and complete-execution split")
    train, calibration, test, split = split_rows(data["rows"], cohort["measured_workflow_ids"])
    if split != evaluation["split_contract"]:
        raise ValueError("ensemble complete-workflow split changed")
    args.output_dir.mkdir(parents=True)
    write(args.output_dir / "plan.json", {
        "additional_seeds": [7, 42], "hidden_width": 64, "epochs": 200,
        "ensemble": "equal_weight_mean_logits", "selection": "calibration_eligibility_and_early_score",
        "inputs_sha256": inputs, "split_contract": split,
        "test_labels_used_for_selection": False, "GPU_action": False,
    })
    members = [baseline]
    for seed in (7, 42):
        print("fitting_seed: " + str(seed), flush=True)
        raw = fit(
            train, calibration, epochs=200, seed=seed, split_mode="workflow",
            hidden_width=64, vocabulary_size=8192, dropout=.1, weight_decay=.05,
            feature_schema=R5_SCHEMA,
        )
        raw["metadata"].update(
            prefill_token_weight=.01, split_contract=split, decision_eligible=True,
            progress_observation_contract=data["progress_observation_contract"],
        )
        members.append(raw)
        write(args.output_dir / f"seed{seed}.json", raw)
    candidates = {
        "baseline": baseline,
        "ensemble_21_7": calibrate(merge(members[:2]), calibration),
        "ensemble_21_7_42": calibrate(merge(members), calibration),
    }
    summaries = {}
    for name, raw in candidates.items():
        values = slices(WorkflowWorkModel(raw), calibration)
        reasons = calibration_rejections(values, raw)
        raw["metadata"].update(
            decision_eligible=not reasons, decision_rejection_reasons=reasons,
            calibration_distribution_metrics=values["all"],
        )
        summaries[name] = {
            "calibration": values, "selection_score": selection_score(values),
            "decision_eligible": not reasons, "reasons": reasons,
        }
        write(args.output_dir / name / "model.json", raw)
        print(json.dumps({
            "candidate": name, "score": selection_score(values), "reasons": reasons,
            "initial": values["initial"]["initial_accuracy"],
            "early": values["first_third"]["workflow_equal_weight_accuracy"],
        }), flush=True)
    selected = max(
        (name for name, row in summaries.items() if row["decision_eligible"]),
        key=lambda name: summaries[name]["selection_score"],
    )
    write(args.output_dir / "selection.json", {"selected": selected, "calibration_summary": summaries})
    print("selected_before_test_evaluation: " + selected, flush=True)
    held_out = {name: slices(WorkflowWorkModel(candidates[name]), test) for name in {selected, "baseline"}}
    write(args.output_dir / "model.json", candidates[selected])
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("an ensemble training input changed")
    write(args.output_dir / "evaluation.json", {
        "selected": selected, "held_out": held_out, "calibration_summary": summaries,
        "model_sha256": sha256_file(args.output_dir / "model.json"),
        "split_contract": split, "inputs_sha256": inputs,
        "candidate_selection_used_test_labels": False,
        "validation_scope": "fixed_development_workflow_holdout_not_new_confirmatory_test",
        "GPU_action": False, "JCT_gain_proven": False,
    })
    print(json.dumps({
        name: {
            "initial": row["initial"]["initial_accuracy"],
            "early": row["first_third"]["workflow_equal_weight_accuracy"],
            "all": row["all"]["workflow_equal_weight_accuracy"],
            "long_to_short": row["all"]["long_to_short_rate"],
            "initial_pairs": row["initial_pair_ranking"],
        } for name, row in held_out.items()
    }), flush=True)


if __name__ == "__main__":
    main()
