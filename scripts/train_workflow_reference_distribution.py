"""Evaluate independent conditional progress references on unchanged workflow holdouts."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import R5_SCHEMA, WorkflowWorkModel
from beliefkv.predictor.workflow_work_reference import (
    ReferenceWorkCandidate, WorkflowWorkReferences, fit_references,
)
from scripts.extend_workflow_prediction_training import disjoint_training_rows
from scripts.improve_workflow_work_distribution import calibration_rejections
from scripts.train_workflow_ready_ranking import metrics, score
from scripts.train_workflow_work import capture_split, write
from scripts.train_workflow_work_distribution import split_rows


class CachedCandidate:
    """Memoize only pure model results across repeated diagnostic slices."""

    def __init__(self, model):
        self.model, self.cache = model, {}

    def __getattr__(self, name):
        return getattr(self.model, name)

    def predict(self, projection, features):
        key = projection_key(projection), tuple(features)
        if key not in self.cache:
            self.cache[key] = self.model.predict(projection, features)
        return self.cache[key]


def projection_key(projection):
    import numpy as np
    from beliefkv.predictor.workflow_progress_tree import TreeProjection
    from beliefkv.predictor.workflow_work import InitialWorkProjection

    if isinstance(projection, TreeProjection):
        return ("conditional_progress_tree", projection_key(projection.progress_projection), projection.task_structure)
    if isinstance(projection, InitialWorkProjection):
        return (
            projection_key(projection.progress_projection),
            None if projection.initial_projection is None else projection_key(projection.initial_projection),
            projection.initial_coverage,
        )
    return np.asarray(projection).tobytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "extra-training-data", "baseline-model",
                 "baseline-evaluation", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve earlier reference selection and development evidence")
    paths = (args.dataset, args.cohort, args.extra_training_data,
             args.baseline_model, args.baseline_evaluation)
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    data, cohort, extra, raw, evaluation = (json.loads(path.read_bytes()) for path in paths)
    train, calibration, test, original_split = split_rows(data["rows"], cohort["measured_workflow_ids"])
    if (
        data["schema"] != R5_SCHEMA or data["prefill_token_weight"] != .01
        or evaluation["model_sha256"] != pins[str(args.baseline_model.resolve())]
        or evaluation["inputs_sha256"].get(str(args.dataset.resolve())) != pins[str(args.dataset.resolve())]
        or extra["training_extension"]["original_dataset_sha256"] != pins[str(args.dataset.resolve())]
    ):
        raise ValueError("require exact source-bound predictor and training-only extension")
    train = disjoint_training_rows(train, extra["rows"], calibration, test)
    split = capture_split(train, calibration, test, require_capture_disjoint=False)
    if split != evaluation["expanded_split"] or split != extra["training_extension"]["expanded_split"]:
        raise ValueError("conditional references must preserve every complete-workflow holdout")
    for field in ("project", "task_template", "source_workflow_sha256"):
        if {row[field] for row in train} & {row[field] for row in calibration + test}:
            raise ValueError("conditional references overlap held-out " + field)
    settings = [
        {"blend": blend, "bandwidth": bandwidth, "task_weight": task_weight,
         "progress_only": progress_only}
        for bandwidth, task_weight in ((.35, 0.), (.7, 1.))
        for blend in (.25, .5) for progress_only in (True, False)
    ]
    write(args.output_dir / "plan.json", {
        "candidates": settings, "maximum_states_per_training_workflow": 64,
        "neighbors": 8, "minimum_surviving_training_workflows": 4,
        "selection": "unchanged_calibration_support_gates_then_existing_ready_ranking_score",
        "original_split": original_split, "expanded_split": split,
        "reference_scope": "training_only_complete_original_projects_numeric_progress_and_labels",
        "reference_model_never_reads_query_future_total_or_remaining_work": True,
        "development_labels_used_for_selection": False, "GPU_action": False,
        "inputs_sha256": pins, "frozen_work_thresholds": raw["work_thresholds"],
    })
    base = WorkflowWorkModel(raw)
    references_raw = fit_references(train, base)
    references = WorkflowWorkReferences(references_raw)
    write(args.output_dir / "references.json", references_raw)
    candidates = {"baseline": CachedCandidate(base)}
    for index, values in enumerate(settings):
        candidates[f"reference_{index}"] = CachedCandidate(ReferenceWorkCandidate(base, references, **values))
    summaries = {}
    for name, model in candidates.items():
        started = time.monotonic()
        values = metrics(model, calibration)
        reasons = calibration_rejections(values, raw)
        summaries[name] = {
            "calibration": values, "selection_score": score(values),
            "settings": None if name == "baseline" else settings[int(name.split("_")[1])],
            "decision_eligible": not reasons, "decision_rejection_reasons": reasons,
            "evaluation_seconds": time.monotonic() - started,
        }
        write(args.output_dir / name / "calibration.json", summaries[name])
        print(json.dumps({
            "candidate": name, "score": score(values), "rejections": reasons,
            "initial": values["initial_pair_ranking"]["accuracy"],
            "first_request": values["first_request_pairs"]["accuracy"],
            "ready_pairs": values["ready_cross_progress_pairs"]["accuracy"],
            "accuracy": values["all"]["workflow_equal_weight_accuracy"],
            "long_to_short": values["all"]["long_to_short_rate"],
        }), flush=True)
    selected = max(
        (name for name in summaries if summaries[name]["decision_eligible"]),
        key=lambda name: summaries[name]["selection_score"],
    )
    write(args.output_dir / "selection.json", {
        "selected": selected, "calibration_summary": summaries, "held_out_evaluated": False,
    })
    print("selected_before_development: " + selected, flush=True)
    held_out = {
        name: metrics(candidates[name], test) for name in dict.fromkeys(("baseline", selected))
    }
    selected_model = deepcopy(raw)
    if selected != "baseline":
        selected_model["conditional_progress_references"] = {
            "references": references_raw, **summaries[selected]["settings"],
        }
        selected_model["metadata"].update(
            decision_eligible=False, decision_rejection_reasons=["offline_adapter_not_serving_qualified"],
        )
    write(args.output_dir / "model.json", selected_model)
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a frozen conditional-reference study input changed")
    write(args.output_dir / "evaluation.json", {
        "selected": selected, "held_out": held_out, "calibration_summary": summaries,
        "original_split": original_split, "expanded_split": split, "inputs_sha256": pins,
        "model_sha256": sha256_file(args.output_dir / "model.json"),
        "candidate_selection_used_test_labels": False,
        "validation_scope": "reused_fixed_development_holdout_not_confirmatory",
        "deployment_adapter_implemented": False, "GPU_action": False, "JCT_gain_proven": False,
    })
    print(json.dumps({name: {
        "initial": values["initial_pair_ranking"]["accuracy"],
        "first_request": values["first_request_pairs"]["accuracy"],
        "ready_pairs": values["ready_cross_progress_pairs"]["accuracy"],
        "accuracy": values["all"]["workflow_equal_weight_accuracy"],
        "NLL": values["all"]["workflow_equal_weight_NLL"],
        "long_to_short": values["all"]["long_to_short_rate"],
    } for name, values in held_out.items()}), flush=True)


if __name__ == "__main__":
    main()
