"""Improve a fixed predictor with causal admission states from training projects only."""

import argparse
from collections import defaultdict
from copy import deepcopy
import json
import math
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import R5_FEATURES, R5_SCHEMA, WorkflowWorkModel
from beliefkv.predictor.workflow_work_training import DECISION_OBSERVATION, READY_BALANCED
from scripts.audit_live_work_prediction import rank_summary
from scripts.extend_workflow_prediction_training import disjoint_training_rows
from scripts.improve_workflow_work_distribution import calibration_rejections
from scripts.rescore_workflow_predictions import rescore
from scripts.train_workflow_ready_ranking import metrics, score
from scripts.train_workflow_work import capture_split, fit, write
from scripts.train_workflow_work_distribution import split_rows


def require_causal_scope(audit):
    scope = audit["scope"]
    if (
        scope.get("protected_JOIN_identification") != "zero_global_rank_not_reference_category_v2"
        or scope.get("client_published_events_and_observed_native_progress_only_for_features") is not True
        or scope.get("protected_JOIN_pairs_excluded_from_predictor_rank_quality") is not True
        or scope.get("future_original_work_and_logged_paths_are_labels_only") is not True
    ):
        raise ValueError("require the corrected sealed causal competition population")


def augment_training(rows, audit, *, maximum_states=32):
    """Keep original complete-workflow identity; never import held-out observations."""
    import numpy as np

    require_causal_scope(audit)
    if type(maximum_states) is not int or not 1 <= maximum_states <= 128:
        raise ValueError("invalid bounded admission-state count")
    templates, last = {}, defaultdict(int)
    for row in rows:
        workflow = row["logical_workflow_id"]
        last[workflow] = max(last[workflow], row["snapshot"])
        if row["snapshot"] == 0:
            if workflow in templates:
                raise ValueError("ambiguous original training workflow")
            templates[workflow] = row
    if set(last) != templates.keys():
        raise ValueError("admission-state training requires complete original workflows")
    states = defaultdict(dict)
    for decision in audit["decisions"]:
        for candidate in decision["candidates"]:
            workflow = candidate["key"]["root_workflow_id"]
            if workflow not in templates:
                continue
            if not candidate.get("selector_metadata_valid", False) or candidate.get("protected_JOIN"):
                continue
            observed = candidate.get("features")
            remaining = candidate.get("observed_remaining_model_work")
            if (
                not isinstance(observed, (list, tuple)) or len(observed) != len(R5_FEATURES)
                or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                       for value in observed)
                or type(remaining) not in (int, float) or not math.isfinite(remaining) or remaining < 0
            ):
                raise ValueError("invalid causal observed-decision features or work label")
            attained = .01 * observed[0] + observed[1]
            if not math.isclose(
                templates[workflow]["total_work"], attained + remaining, rel_tol=0., abs_tol=1e-6,
            ):
                raise ValueError("admission-state labels differ from complete original work")
            feature_key = tuple(observed)
            if feature_key in states[workflow]:
                continue
            states[workflow][feature_key] = (
                remaining, decision["monotonic_s"], deepcopy(candidate["key"]),
            )
    extra = []
    for workflow, values in sorted(states.items()):
        items = list(values.items())
        indices = np.linspace(0, len(items) - 1, min(len(items), maximum_states), dtype=int)
        template = templates[workflow]
        for index, position in enumerate(indices, 1):
            observed, (remaining, boundary, key) = items[position]
            extra.append({
                **deepcopy(template), "features": list(observed),
                "text": template.get("task_text", template["text"]),
                "snapshot": last[workflow] + index, "event": "admission_competition",
                "attained_work": .01 * observed[0] + observed[1], "remaining_work": remaining,
                "training_observation_kind": DECISION_OBSERVATION,
                "training_observation_boundary_s": boundary, "training_observation_key": key,
            })
    return list(rows) + extra, {
        "added_states": len(extra), "training_workflows_with_observed_states": len(states),
        "maximum_distinct_states_per_workflow": maximum_states,
        "held_out_observations_used_for_training": False,
        "original_rows_unchanged": True,
    }


def live_quality(model, audit, texts, workflows):
    values = rescore(audit["decisions"], {"candidate": model}, texts)
    return rank_summary(values, "candidate", measured=workflows, held_out_only=True)


def selection_score(natural, actual):
    value = actual["metrics"]["model"]["boundary_equal_weight_accuracy"]
    if value is None:
        raise ValueError("calibration has no observed ordinary cross-workflow competition")
    return .5 * score(natural) + .5 * value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "dataset", "cohort", "extra-training-data", "baseline-model",
        "baseline-evaluation", "competition-audit", "output-dir",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve earlier admission-state prediction studies")
    paths = (
        args.dataset, args.cohort, args.extra_training_data, args.baseline_model,
        args.baseline_evaluation, args.competition_audit,
    )
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    data, cohort, extension, base, evaluation, audit = (
        json.loads(path.read_bytes()) for path in paths
    )
    require_causal_scope(audit)
    train, calibration, development, original_split = split_rows(
        data["rows"], cohort["measured_workflow_ids"],
    )
    if (
        data["schema"] != R5_SCHEMA or data["prefill_token_weight"] != .01
        or evaluation["model_sha256"] != pins[str(args.baseline_model.resolve())]
        or evaluation["inputs_sha256"].get(str(args.dataset.resolve())) != pins[str(args.dataset.resolve())]
        or audit["inputs_sha256"].get(str(args.cohort.resolve())) != pins[str(args.cohort.resolve())]
        or extension["training_extension"]["original_dataset_sha256"] != pins[str(args.dataset.resolve())]
    ):
        raise ValueError("admission-state study requires exact source-bound models and populations")
    train = disjoint_training_rows(train, extension["rows"], calibration, development)
    split = capture_split(train, calibration, development, require_capture_disjoint=False)
    if split != evaluation["expanded_split"] or split != extension["training_extension"]["expanded_split"]:
        raise ValueError("preserve complete-workflow training and evaluation splits")
    augmented, augmentation = augment_training(train, audit)
    if capture_split(augmented, calibration, development, require_capture_disjoint=False) != split:
        raise ValueError("admission-state augmentation changed source workflow identities")
    texts = {
        row["logical_workflow_id"]: row.get("task_text", row["text"])
        for row in data["rows"] if row["snapshot"] == 0
    }
    cal_ids = {row["logical_workflow_id"] for row in calibration}
    dev_ids = {row["logical_workflow_id"] for row in development}
    write(args.output_dir / "plan.json", {
        "decision_observation_mass": [0., .25, .5], "epochs": 200,
        "selection": "half_existing_calibration_score_half_actual_calibration_boundary_rank",
        "calibration_gates_unchanged": True, "original_split": original_split,
        "expanded_split": split, "augmentation": augmentation, "inputs_sha256": pins,
        "established_initial_head_preserved": True, "frozen_work_thresholds": base["work_thresholds"],
        "development_labels_used_for_selection_or_training": False,
        "actual_calibration_and_development_are_same_closed_control_not_new_executions": True,
        "GPU_action": False,
    })
    models, summaries = {}, {}
    for mass in (0., .25, .5):
        name = "retained" if not mass else "decision_mass" + str(mass)
        print("fitting: " + name, flush=True)
        started = time.monotonic()
        raw = deepcopy(base) if not mass else fit(
            augmented, calibration, epochs=200, feature_schema=R5_SCHEMA,
            split_mode="workflow", hidden_width=128, vocabulary_size=8192,
            dropout=.1, weight_decay=.05, training_weighting=READY_BALANCED,
            ranking_loss_weight=.1, work_thresholds=base["work_thresholds"],
            decision_observation_mass=mass,
        )
        if mass:
            raw["metadata"].update(
                prefill_token_weight=.01, split_contract=split, decision_eligible=True,
                progress_observation_contract=data["progress_observation_contract"],
                online_features_use_observed_prefix_only=True,
                unseen_project_generalization_claimed=False,
                training_dataset_sha256=pins[str(args.dataset.resolve())],
                training_extension_sha256=pins[str(args.extra_training_data.resolve())],
                causal_competition_audit_sha256=pins[str(args.competition_audit.resolve())],
                candidate_selection_uses_calibration_only=True,
            )
            for key in (
                "admission_cost_profile", "free_dispatch_coalescing_window_ms", "initial_state_head",
            ):
                if key in base:
                    raw[key] = deepcopy(base[key])
        natural = metrics(WorkflowWorkModel(raw), calibration)
        reasons = calibration_rejections(natural, raw)
        raw["metadata"].update(
            calibration_distribution_metrics=natural["all"],
            decision_eligible=not reasons, decision_rejection_reasons=reasons,
        )
        actual = live_quality(WorkflowWorkModel(raw), audit, texts, cal_ids)
        models[name] = raw
        summaries[name] = {
            "calibration": natural, "actual_calibration": actual,
            "selection_score": selection_score(natural, actual),
            "decision_eligible": not reasons, "decision_rejection_reasons": reasons,
            "fit_and_calibration_seconds": time.monotonic() - started,
        }
        write(args.output_dir / name / "model.json", raw)
        write(args.output_dir / name / "calibration.json", summaries[name])
        print(json.dumps({
            "candidate": name, "score": summaries[name]["selection_score"],
            "actual_calibration": actual["metrics"]["model"], "rejections": reasons,
        }), flush=True)
    selected = max(
        (name for name, row in summaries.items() if row["decision_eligible"]),
        key=lambda name: summaries[name]["selection_score"],
    )
    write(args.output_dir / "selection.json", {"selected": selected, "calibration_summary": summaries})
    print("selected_before_development: " + selected, flush=True)
    held_out = {}
    for name in dict.fromkeys(("retained", selected)):
        model = WorkflowWorkModel(models[name])
        held_out[name] = {
            "natural": metrics(model, development),
            "actual_competition": live_quality(model, audit, texts, dev_ids),
        }
    write(args.output_dir / "model.json", models[selected])
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("an immutable admission-state training input changed")
    write(args.output_dir / "evaluation.json", {
        "selected": selected, "held_out": held_out, "calibration_summary": summaries,
        "original_split": original_split, "expanded_split": split, "augmentation": augmentation,
        "inputs_sha256": pins, "model_sha256": sha256_file(args.output_dir / "model.json"),
        "candidate_selection_used_test_labels": False,
        "validation_scope": "reused_development_same_control_not_confirmatory",
        "analysis_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip(),
        "GPU_action": False, "JCT_gain_proven": False,
    })
    print(json.dumps({name: {
        "actual": row["actual_competition"]["metrics"]["model"],
        "initial": row["natural"]["initial_pair_ranking"]["accuracy"],
        "first_request": row["natural"]["first_request_pairs"]["accuracy"],
        "ready": row["natural"]["ready_cross_progress_pairs"]["accuracy"],
        "accuracy": row["natural"]["all"]["workflow_equal_weight_accuracy"],
        "long_to_short": row["natural"]["all"]["long_to_short_rate"],
    } for name, row in held_out.items()}), flush=True)


if __name__ == "__main__":
    main()
