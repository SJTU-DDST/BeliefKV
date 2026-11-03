"""Isolate work quantization and prediction error on complete observed combination choices."""

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import replace
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.policy.workflow_combination import (
    CombinationCosts, CombinationResources, _evaluate, choose_combination,
)
from beliefkv.predictor.workflow_continuous_work import (
    ContinuousWorkReadout, fit_training_reference, remaining_work_weight,
)
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from beliefkv.predictor.workflow_rank_input import WorkflowRankInput
from scripts import run_causal_comparison as causal
from scripts.audit_learned_combination_prediction import combination_candidates, pair_quality
from scripts.audit_live_work_prediction import audit, observation_timeline
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_combination_choices import EVENT, canonical, replay_choice
from scripts.review_learned_work_ranking import verify_learned_combination_pair
from scripts.train_workflow_task_diversity import training_population
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


VARIANTS = (
    "actual_cached", "tree_binned", "tree_continuous", "tree_continuous_distribution",
    "oracle_binned", "oracle_continuous",
)


def inspect(rows, observations, costs, profile, model, contract, texts, measured):
    if len(rows) != len(observations):
        raise ValueError("continuous comparison needs each exact observed decision boundary")
    readout, projections = ContinuousWorkReadout(model, contract), {}
    counts, stats, decisions = Counter(), defaultdict(Counter), []
    for row, observation in zip(rows, observations):
        replayed = replay_choice(
            row, costs, profile["identity"], allow_feasible_resource_alternatives=True,
            plan_device_capacity=True,
        )
        counts["combination_entries"] += 1
        if not replayed["search_replayed"]:
            counts["metadata_unavailable"] += 1
            continue
        if not row["decision"]["search_complete"]:
            counts["incomplete_searches_excluded"] += 1
            continue
        if row["monotonic_s"] != observation["monotonic_s"]:
            raise ValueError("continuous comparison has a mismatched observation boundary")
        candidates = combination_candidates(row)
        observed = {entry["key"]["invocation_id"]: entry for entry in observation["candidates"]}
        if any(
            candidate.identity not in observed
            or observed[candidate.identity]["key"] != row["keys"][candidate.identity]
            or "features" not in observed[candidate.identity]
            or observed[candidate.identity].get("predictions", {}).get("tree") is None
            for candidate in candidates
        ):
            counts["missing_exact_observed_inputs"] += 1
            continue
        resources = CombinationResources(**row["resources"])
        reference, required = row["decision"]["reference"], set(row["decision"]["required_reference_members"])
        details, weights = {}, {}
        for candidate in candidates:
            entry = observed[candidate.identity]
            prediction = entry["predictions"]["tree"]
            workflow = candidate.workflow
            if workflow not in projections:
                projections[workflow] = model.task_encoding(texts[workflow])
            projection, _coverage = projections[workflow]
            remaining = entry["observed_remaining_model_work"]
            if not math.isfinite(remaining) or remaining < -1e-6:
                raise ValueError("continuous oracle requires a finite complete-work label")
            if projection is None:
                raise ValueError("observed model support and task projection disagree")
            if any(abs(left - right) > 1e-12 for left, right in zip(
                model.predict(projection, entry["features"]), prediction["probabilities"],
            )):
                raise ValueError("source task metadata differs from the observed prediction input")
            point = readout.predict(
                projection, entry["features"], probabilities=prediction["probabilities"],
                support=prediction["support"],
            )
            counts[point["source"] + "_candidate_observations"] += 1
            counts["unsupported_neutral_candidate_observations"] += prediction["support"] != "calibrated_distribution"
            values = {
                "actual_cached": candidate.weight, "tree_binned": prediction["effective_weight"],
                "tree_continuous": point["effective_weight"],
                "tree_continuous_distribution": point["distribution_effective_weight"],
                "oracle_binned": (1., .5, .25)[bisect_right(model.thresholds, max(0., remaining))],
                "oracle_continuous": remaining_work_weight(max(0., remaining), contract["reference_work"]),
            }
            details[candidate.identity] = {
                "workflow": workflow, "key": entry["key"], "features": entry["features"],
                "remaining_model_work": remaining, "continuous_forecast": point,
                "probabilities": prediction["probabilities"], "weights": values,
            }
            weights[candidate.identity] = values
        counts["complete_searches_with_observed_inputs"] += 1
        selections, truth_rows = {}, [
            replace(candidate, weight=weights[candidate.identity]["oracle_continuous"])
            for candidate in candidates
        ]
        truth_by_id = {candidate.identity: candidate for candidate in truth_rows}
        for name in VARIANTS:
            alternatives = [replace(candidate, weight=weights[candidate.identity][name]) for candidate in candidates]
            result = choose_combination(
                alternatives, reference, resources, costs, required=required,
                clock=lambda: 0, allow_feasible_resource_alternatives=True,
            )
            if name == "actual_cached" and canonical(result) != canonical(row["decision"]):
                raise ValueError("continuous audit failed to reproduce the executed complete search")
            if not required <= set(result["selected"]):
                raise ValueError("continuous work weights revoked required JOIN rights")
            selected = set(result["selected"])
            actual = set(row["decision"]["selected"])
            values = stats[name]
            values["complete_decisions"] += 1
            values["changes_executed_set"] += selected != actual
            values["strict_reference_shrinks"] += selected < set(reference)
            values["new_reference_outsiders"] += bool(selected - set(reference))
            values["selected_agent_observations"] += len(selected)
            values["changed_sets_touch_development"] += any(
                candidate.workflow in measured and candidate.identity in selected ^ actual
                for candidate in candidates
            )
            selections[name] = {
                "selected": result["selected"], "reason": result["reason"],
                "predicted_evaluation": result.get("selected_evaluation"),
                "continuous_truth_evaluation": _evaluate(
                    [truth_by_id[owner] for owner in result["selected"]], resources, costs,
                ),
            }
        for name, result in selections.items():
            changed = set(result["selected"]) != set(selections["tree_binned"]["selected"])
            stats[name]["changes_tree_binned_set"] += changed
            old_truth = selections["tree_binned"]["continuous_truth_evaluation"]
            new_truth = result["continuous_truth_evaluation"]
            if changed and old_truth is not None and new_truth is not None:
                difference = new_truth["score"] - old_truth["score"]
                stats[name]["changed_set_has_higher_true_surrogate_score"] += difference > 1e-12
                stats[name]["changed_set_has_lower_true_surrogate_score"] += difference < -1e-12
        required_rows = [candidate for candidate in candidates if candidate.identity in required]
        protected = {candidate.workflow for candidate in required_rows}
        fitting = [
            candidate for candidate in candidates if candidate.identity not in required
            and _evaluate(required_rows + [candidate], resources, costs) is not None
        ]
        competition = len({candidate.workflow for candidate in fitting}) >= 2 and (
            _evaluate(required_rows + fitting, resources, costs) is None
        )
        counts["planning_ordinary_cross_workflow_capacity_competitions"] += competition
        ordinary = {}
        for candidate in fitting:
            if candidate.workflow in protected:
                continue
            value = details[candidate.identity]
            if candidate.workflow in ordinary and any(
                ordinary[candidate.workflow][key] != value[key]
                for key in ("remaining_model_work", "weights")
            ):
                raise ValueError("same-workflow continuous weights disagree within the boundary")
            ordinary[candidate.workflow] = value
        if competition and ordinary:
            minimum = min(value["remaining_model_work"] for value in ordinary.values())
            shortest = {
                candidate.identity for candidate in fitting if candidate.workflow not in protected
                and details[candidate.identity]["remaining_model_work"] <= minimum + 1e-6
            }
            for name, result in selections.items():
                stats[name]["competitions_with_unprotected_shortest"] += 1
                stats[name]["includes_true_shortest"] += bool(shortest & set(result["selected"]))
        decisions.append({
            "monotonic_s": row["monotonic_s"], "source": row["source"], "reference": reference,
            "required_JOIN_members": sorted(required), "planning_capacity_competition": competition,
            "ordinary_workflows": list(ordinary.values()), "selections": selections,
            "candidate_details": details,
        })
    competing = [row for row in decisions if row["planning_capacity_competition"]]
    return {
        "counts": dict(counts), "selection_counts": {name: dict(value) for name, value in stats.items()},
        "ranking": {name: {
            "all_planning_competitions": pair_quality(competing, name),
            "both_development_workflows": pair_quality(competing, name, measured),
        } for name in VARIANTS}, "decisions": decisions,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "control", "cohort", "dataset", "previous-training-data",
                 "additional-training-data", "model", "model-evaluation", "training-evaluation", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve previous continuous work diagnostics")
    paths = tuple(getattr(args, name.replace("-", "_")) for name in (
        "registration", "control", "cohort", "dataset", "previous-training-data",
        "additional-training-data", "model", "model-evaluation", "training-evaluation",
    ))
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    plan, control, cohort, data, previous, additional, raw, evaluation, training_evaluation = (
        causal.load(path) for path in paths
    )
    for value in (plan, control):
        causal.comparison.verify_sources(value)
    specs = [causal.load(Path(value["experiment_configuration"])) for value in (control, plan)]
    verify_learned_combination_pair(*specs)
    if (
        evaluation["model_sha256"] != pins[str(args.model.resolve())]
        or evaluation["inputs_sha256"].get(str(args.dataset.resolve())) != pins[str(args.dataset.resolve())]
        or evaluation["inputs_sha256"].get(str(args.cohort.resolve())) != pins[str(args.cohort.resolve())]
        or evaluation["candidate_selection_used_test_labels"] is not False
        or evaluation.get("offline_candidate_evaluation_sha256") != pins[str(args.training_evaluation.resolve())]
        or evaluation["inputs_sha256"].get(str(args.training_evaluation.resolve()))
        != pins[str(args.training_evaluation.resolve())]
        or any(
            training_evaluation["inputs_sha256"].get(str(path.resolve())) != pins[str(path.resolve())]
            for path in (args.dataset, args.cohort, args.previous_training_data, args.additional_training_data)
        )
    ):
        raise ValueError("continuous diagnosis requires the selected source-bound tree model")
    # Population validation uses the retained trunk's 59-execution split. The
    # conditional readout was fit on the explicitly recorded expanded 77.
    population_evaluation = {**evaluation, "expanded_split": evaluation["previous_expanded_split"]}
    training, _calibration, _development, population = training_population(
        data, cohort, previous, additional, population_evaluation, pins[str(args.dataset.resolve())],
    )
    if population["expanded_split"] != evaluation["expanded_split"]:
        raise ValueError("continuous reference population differs from the selected readout training")
    model = WorkflowWorkModel.load(args.model, pins[str(args.model.resolve())])
    contract = fit_training_reference(training, model.thresholds)
    executed_model_path = Path(specs[1]["common_environment"]["WORKFLOW_WORK_MODEL"])
    executed_model = causal.load(executed_model_path)
    profile = executed_model["admission_cost_profile"]
    label_path = Path(control["reference_environment"]["BELIEFKV_WORKFLOW_WORK_MODEL"])
    labels = causal.load(label_path)
    policy = ROOT / "beliefkv/policy/workflow_combination.py"
    if (
        sha256_file(policy) != plan["source_sha256"]["beliefkv/policy/workflow_combination.py"]
        or set(labels["workflows"]) != set(plan["workflow_ids"])
        or data["sources_sha256"].get(str(label_path.resolve())) != sha256_file(label_path)
        or profile["kind"] != "r5_frozen_batch_cost_proxy_capacity_v6"
    ):
        raise ValueError("continuous audit requires unchanged executed policy, workload and complete labels")
    for path in (
        executed_model_path, label_path, policy, Path(__file__),
        ROOT / "beliefkv/predictor/workflow_continuous_work.py",
        ROOT / "beliefkv/predictor/workflow_work.py",
        ROOT / "beliefkv/predictor/workflow_conditional_tree.py",
        ROOT / "beliefkv/predictor/workflow_work_readout.py",
        ROOT / "scripts/audit_live_work_prediction.py",
        ROOT / "scripts/audit_workflow_combination_choices.py",
        ROOT / "scripts/train_workflow_task_diversity.py",
    ):
        pins[str(path.resolve())] = sha256_file(path)
    execution = verify_execution(args.registration, args.cohort)
    print(json.dumps({"stage": "complete_execution_verified", "requests": execution["measured_requests"],
                      "training_reference": contract}), flush=True)
    reader, arm = SealedRecords(), Path(plan["run_root"]) / "full"
    rows = [
        row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == EVENT
    ]
    observations = [{
        "monotonic_s": row["monotonic_s"], "candidates": [
            {"key": row["keys"][candidate["identity"]]} for candidate in row.get("candidates", ())
        ],
    } for row in rows]
    timeline = observation_timeline(
        list(reader(arm / "replay/runtime_events.replay.jsonl")),
        (row for row in reader(arm / "server/runtime_audit.jsonl") if row.get("event") == "gpu_service_sample"),
        labels["requests"],
    )
    texts = {row["logical_workflow_id"]: row.get("task_text", row["text"])
             for row in data["rows"] if row["snapshot"] == 0}
    for _when, _priority, _index, event in timeline:
        if isinstance(event, RuntimeEvent) and event.kind is RuntimeEventKind.WORKFLOW_START:
            metadata = WorkflowRankInput.from_dict(event.attributes["workflow_ranking_input"])
            if texts.get(event.workflow_id) != metadata.task_instructions:
                raise ValueError("source task text differs from its actual observed workflow-start metadata")
    totals = {workflow: value["total_work_units"] / 100. for workflow, value in labels["workflows"].items()}
    audit(observations, timeline, labels["requests"], totals, {"tree": model})
    print(json.dumps({"stage": "observed_inputs_reconstructed", "entries": len(rows)}), flush=True)
    result = inspect(
        rows, observations, CombinationCosts(**profile["costs"]), profile, model, contract, texts,
        set(cohort["measured_workflow_ids"]),
    )
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a frozen continuous work diagnostic input changed")
    result.update(
        training_reference=contract, training_population=population, inputs_sha256=pins,
        input_jsonl_prefixes=reader.prefixes, GPU_action=False, online_policy_changed=False,
        analysis_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        scope={
            "same_recorded_candidates_resources_costs_and_required_JOIN_rights": True,
            "actual_complete_search_reproduced_before_counterfactual": True,
            "learned_and_oracle_continuous_weights_use_same_model_work_units_and_training_reference": True,
            "distribution_variant_uses_existing_calibration_sigma_fixed_12_node_quadrature_and_training_class_utility": True,
            "initial_states_use_explicit_training_only_class_prior_not_new_semantic_resolution": True,
            "complete_future_work_is_offline_oracle_label_only": True,
            "new_tree_predictions_are_synchronous_not_actual_cached_worker_predictions": True,
            "full_pool_planning_fit_is_not_immediate_native_capacity": True,
            "all_candidate_population_includes_training_projects": True,
            "whole_run_service_or_JCT_counterfactual_proven": False,
        },
    )
    write(args.output, result)
    print(json.dumps({key: result[key] for key in ("counts", "selection_counts", "ranking")}), flush=True)


if __name__ == "__main__":
    main()
