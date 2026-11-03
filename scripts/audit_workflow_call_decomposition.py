"""Diagnose remaining-call and observed per-call cost limits on sealed ready competition."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import R5_FEATURES, WorkflowWorkModel
from beliefkv.predictor.workflow_work_training import remaining_call_counts
from scripts.audit_live_work_prediction import rank_summary
from scripts.extend_workflow_prediction_training import disjoint_training_rows
from scripts.rescore_workflow_predictions import rescore
from scripts.train_workflow_work import write
from scripts.train_workflow_work_distribution import split_rows


def per_call_costs(rows):
    import numpy as np

    _remaining, finals = remaining_call_counts(rows)
    last = {}
    for row in rows:
        if row["workflow"] not in last or row["snapshot"] > last[row["workflow"]]["snapshot"]:
            last[row["workflow"]] = row
    costs = {"root": [], "child": [], "total": []}
    for identity, row in last.items():
        values = dict(zip(R5_FEATURES, row["features"]))
        costs["total"].append(row["total_work"] / finals[identity]["total"])
        for owner in ("root", "child"):
            calls = finals[identity][owner]
            if calls:
                work = .01 * values[owner + "_input_tokens"] + values[owner + "_output_tokens"]
                costs[owner].append(work / calls)
    if any(not values for values in costs.values()):
        raise ValueError("per-call cost priors require complete training work for both agent roles")
    return {name: float(np.median(values)) for name, values in costs.items()}


def selected_candidate_model(raw, evaluation, baseline, *, offline_tree=False):
    selected = evaluation.get("selected")
    if selected in (None, "retained") or evaluation.get("candidate_selection_used_test_labels") is not False:
        raise ValueError("require a candidate chosen before development evaluation")
    if offline_tree:
        from scripts.train_workflow_branch_call_trees import KIND, ConditionalTreeCandidate

        calibration = evaluation.get("calibration_summary", {}).get(selected, {})
        if (
            raw.get("kind") != KIND or raw.get("base_model") != baseline
            or raw.get("metadata", {}).get("decision_eligible") is not False
            or evaluation.get("deployment_adapter_implemented") is not False
            or calibration.get("decision_eligible") is not True
            or calibration.get("decision_rejection_reasons") != []
            or raw.get("blend") != calibration.get("blend")
        ):
            raise ValueError("require the exact offline tree package and its successful prior calibration")
        return ConditionalTreeCandidate(WorkflowWorkModel(baseline), raw["head"], raw["blend"])
    if (
        raw["metadata"].get("decision_eligible") is not True
        or raw["work_thresholds"] != baseline["work_thresholds"]
    ):
        raise ValueError("require an eligible serving model or an explicit offline audit adapter")
    return WorkflowWorkModel(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "previous-training-data", "labels", "audit", "baseline-model", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--candidate-model", type=Path)
    parser.add_argument("--candidate-evaluation", type=Path)
    parser.add_argument("--offline-tree-readout", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve previous decomposition diagnostics")
    if (args.candidate_model is None) != (args.candidate_evaluation is None):
        raise ValueError("a selected candidate needs both its model and prior calibration evaluation")
    if args.offline_tree_readout and args.candidate_model is None:
        raise ValueError("an offline tree audit requires its selected candidate package")
    original_paths = (
        args.dataset, args.cohort, args.previous_training_data, args.labels, args.audit, args.baseline_model,
    )
    paths = original_paths + (
        (args.candidate_model, args.candidate_evaluation) if args.candidate_model is not None else ()
    )
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    data, cohort, extra, labels, audit, baseline = (json.loads(path.read_bytes()) for path in original_paths)
    if (
        data["sources_sha256"].get(str(args.labels.resolve())) != pins[str(args.labels.resolve())]
        or audit["inputs_sha256"].get(str(args.labels.resolve())) != pins[str(args.labels.resolve())]
        or audit["inputs_sha256"].get(str(args.cohort.resolve())) != pins[str(args.cohort.resolve())]
        or audit["scope"].get("protected_JOIN_identification")
        != "zero_global_rank_not_reference_category_v2"
        or not audit["scope"]["client_published_events_and_observed_native_progress_only_for_features"]
        or extra["training_extension"]["original_dataset_sha256"] != pins[str(args.dataset.resolve())]
    ):
        raise ValueError("require common complete labels, the fixed holdout and corrected causal competition")
    training, calibration, development, _split = split_rows(data["rows"], cohort["measured_workflow_ids"])
    training = disjoint_training_rows(training, extra["rows"], calibration, development)
    models = {"retained": WorkflowWorkModel(baseline)}
    if args.candidate_model is not None:
        candidate = json.loads(args.candidate_model.read_bytes())
        evaluation = json.loads(args.candidate_evaluation.read_bytes())
        if (
            evaluation["model_sha256"] != pins[str(args.candidate_model.resolve())]
            or evaluation["inputs_sha256"].get(str(args.dataset.resolve()))
            != pins[str(args.dataset.resolve())]
            or evaluation["inputs_sha256"].get(str(args.cohort.resolve()))
            != pins[str(args.cohort.resolve())]
            or evaluation["original_split"] != _split
        ):
            raise ValueError("require a calibration-selected eligible model with exact source and holdouts")
        models["selected_candidate"] = selected_candidate_model(
            candidate, evaluation, baseline, offline_tree=args.offline_tree_readout,
        )
    prior = per_call_costs(training)
    _remaining, finals = remaining_call_counts(data["rows"])
    logical, texts = {}, {}
    for row in data["rows"]:
        if row["snapshot"] == 0:
            identity = row["logical_workflow_id"]
            if identity in logical:
                raise ValueError("the complete source contains an ambiguous logical workflow")
            logical[identity], texts[identity] = finals[row["workflow"]], row["text"]
    declared = Counter(row["workflow_id"] for row in labels["requests"].values())
    if declared != {identity: value["total"] for identity, value in logical.items()}:
        raise ValueError("snapshot call totals differ from the exact complete source requests")
    decisions = rescore(audit["decisions"], models, texts)
    diagnostics = Counter()
    names = (
        "future_call_count_only", "future_calls_observed_mean_cost",
        "future_branch_calls_observed_branch_cost",
    )
    for decision in decisions:
        for candidate in decision["candidates"]:
            if "features" not in candidate:
                continue
            values = dict(zip(R5_FEATURES, candidate["features"]))
            workflow = candidate["key"]["root_workflow_id"]
            final = logical[workflow]
            remaining = {owner: final[owner] - values[owner + "_completed_calls"]
                         for owner in ("root", "child")}
            if any(count < 0 for count in remaining.values()) or (
                values["completed_calls"] != values["root_completed_calls"] + values["child_completed_calls"]
            ):
                raise ValueError("causal observed calls exceed their complete source label")
            calls = sum(remaining.values())
            attained = .01 * values["input_tokens"] + values["output_tokens"]
            mean = attained / values["completed_calls"] if values["completed_calls"] else prior["total"]
            branch_cost = 0.
            for owner in ("root", "child"):
                count = values[owner + "_completed_calls"]
                work = .01 * values[owner + "_input_tokens"] + values[owner + "_output_tokens"]
                branch_cost += remaining[owner] * (work / count if count else prior[owner])
            for name, estimate in zip(names, (calls, calls * mean, branch_cost)):
                candidate["predictions"][name] = {
                    "effective_weight": -float(estimate), "support": "offline_future_call_oracle",
                }
            diagnostics["candidate_observations"] += 1
            diagnostics["no_completed_calls_uses_training_prior"] += values["completed_calls"] == 0
    measured = set(cohort["measured_workflow_ids"])
    summaries = {name: {
        "all_workflows": rank_summary(decisions, name, measured=measured),
        "both_held_out_measured": rank_summary(decisions, name, measured=measured, held_out_only=True),
    } for name in (*models, *names)}
    for path in (Path(__file__), ROOT / "beliefkv/predictor/workflow_work_training.py"):
        pins[str(path.resolve())] = sha256_file(path)
    if args.offline_tree_readout:
        path = ROOT / "scripts/train_workflow_branch_call_trees.py"
        pins[str(path)] = sha256_file(path)
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a frozen decomposition diagnostic input changed")
    write(args.output, {
        "models": summaries, "training_only_median_per_call_cost": prior,
        "training_workflows": len({row["workflow"] for row in training}),
        "diagnostics": dict(diagnostics), "decisions": decisions,
        "inputs_sha256": pins, "source_scope": audit["scope"],
        "future_call_counts_are_labels_never_online_features": True,
        "offline_tree_readout_adapter_used": args.offline_tree_readout,
        "offline_tree_serving_eligibility_granted": False,
        "observed_cost_includes_inflight_work_and_is_not_an_ideal_cost_oracle": True,
        "JCT_upper_bound_or_service_or_GPU_evidence": False,
    })
    print(json.dumps({"models": summaries, "training_only_median_per_call_cost": prior}), flush=True)


if __name__ == "__main__":
    main()
