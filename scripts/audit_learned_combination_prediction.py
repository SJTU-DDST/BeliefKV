"""Separate cached prediction, model error and fixed combination objectives on sealed history."""

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import replace
from itertools import combinations
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import (
    CombinationCandidate, CombinationCosts, CombinationResources, _evaluate, choose_combination,
)
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from scripts import run_causal_comparison as causal
from scripts.audit_live_work_prediction import audit, observation_timeline
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_combination_choices import EVENT, canonical, replay_choice
from scripts.review_learned_work_ranking import verify_learned_combination_pair
from scripts.train_workflow_work import write


def planning_costs(profile):
    objectives = {
        "r5_frozen_batch_cost_proxy_capacity_v6": "benefit_per_cost",
        "r5_frozen_batch_cost_proxy_capacity_v7": "benefit",
    }
    costs = CombinationCosts(**profile["costs"])
    if (
        profile.get("kind") not in objectives
        or costs.decode_cost_basis != "full_batch"
        or costs.selection_objective != objectives[profile["kind"]]
    ):
        raise ValueError("diagnostic requires the declared full-pool planning profile and objective")
    return costs


def combination_candidates(row):
    values = []
    for raw in row["candidates"]:
        fields = dict(raw)
        fields["missing_extents"] = tuple(
            (tuple(key) if isinstance(key, list) else key, tokens)
            for key, tokens in fields["missing_extents"]
        )
        values.append(CombinationCandidate(**fields))
    return values


def pair_quality(decisions, name, measured=None):
    by_boundary, by_pair = [], defaultdict(list)
    for decision in decisions:
        group = []
        for left, right in combinations(decision["ordinary_workflows"], 2):
            a, b = left["workflow"], right["workflow"]
            if measured is not None and not {a, b} <= measured:
                continue
            truth = left["remaining_model_work"] - right["remaining_model_work"]
            if abs(truth) < 1e-9:
                continue
            margin = left["weights"][name] - right["weights"][name]
            correct = .5 if abs(margin) < 1e-10 else float(truth * margin < 0)
            group.append(correct)
            by_pair[tuple(sorted((a, b)))].append(correct)
        if group:
            by_boundary.append(sum(group) / len(group))
    return {
        "boundary_equal_weight_accuracy": (
            sum(by_boundary) / len(by_boundary) if by_boundary else None
        ),
        "unique_workflow_pair_equal_weight_accuracy": (
            sum(sum(values) / len(values) for values in by_pair.values()) / len(by_pair)
            if by_pair else None
        ),
        "boundaries": len(by_boundary), "unique_workflow_pairs": len(by_pair),
        "pair_observations": sum(map(len, by_pair.values())),
    }


def inspect(rows, observations, costs, profile, thresholds, measured):
    if len(rows) != len(observations):
        raise ValueError("each observed combination requires its exact reconstructed boundary")
    counts, decisions = Counter(), []
    for row, observation in zip(rows, observations):
        original = replay_choice(
            row, costs, profile["identity"],
            allow_feasible_resource_alternatives=True, plan_device_capacity=True,
        )
        counts["combination_entries"] += 1
        if not original["search_replayed"]:
            counts["metadata_unavailable"] += 1
            continue
        if not row["decision"]["search_complete"]:
            counts["incomplete_search_excluded_from_weight_counterfactual"] += 1
            continue
        if row["monotonic_s"] != observation["monotonic_s"]:
            raise ValueError("reconstructed prediction belongs to another decision boundary")
        candidates = combination_candidates(row)
        observed = {item["key"]["invocation_id"]: item for item in observation["candidates"]}
        if any(
            candidate.identity not in observed
            or observed[candidate.identity]["key"] != row["keys"][candidate.identity]
            or "observed_remaining_model_work" not in observed[candidate.identity]
            for candidate in candidates
        ):
            counts["missing_exact_observed_inputs"] += 1
            continue
        resources = CombinationResources(**row["resources"])
        reference = row["decision"]["reference"]
        required = set(row["decision"]["required_reference_members"])
        weights = {}
        for candidate in candidates:
            item = observed[candidate.identity]
            remaining = item["observed_remaining_model_work"]
            if not math.isfinite(remaining) or remaining < -1e-6:
                raise ValueError("invalid complete-work diagnostic label")
            synchronous = item["predictions"].get("synchronous")
            weights[candidate.identity] = {
                "actual_cached": candidate.weight,
                "synchronous": synchronous["effective_weight"] if synchronous else .5,
                "oracle_three_bins": (1., .5, .25)[bisect_right(thresholds, max(0., remaining))],
                "oracle_continuous": 1. / (1. + max(0., remaining) / thresholds[0]),
            }
        results = {}
        for name in ("actual_cached", "synchronous", "oracle_three_bins", "oracle_continuous"):
            alternatives = [
                replace(candidate, weight=weights[candidate.identity][name])
                for candidate in candidates
            ]
            result = choose_combination(
                alternatives, reference, resources, costs, required=required,
                clock=lambda: 0, allow_feasible_resource_alternatives=True,
            )
            if name == "actual_cached" and canonical(result) != canonical(row["decision"]):
                raise ValueError("complete cached selection differs from executed bounded policy")
            if not required <= set(result["selected"]):
                raise ValueError("diagnostic weights revoked required JOIN rights")
            results[name] = {
                key: result.get(key) for key in (
                    "selected", "reason", "selected_evaluation", "reference_evaluation",
                )
            }
        counts["complete_searches_with_labels"] += 1
        actual = set(results["actual_cached"]["selected"])
        for name, result in results.items():
            changed = set(result["selected"]) != actual
            counts[name + "_changes_executed_set"] += changed
            counts[name + "_strictly_shrinks_reference"] += (
                set(result["selected"]) < set(reference)
            )
            counts[name + "_new_reference_outsiders"] += bool(set(result["selected"]) - set(reference))
        required_rows = [candidate for candidate in candidates if candidate.identity in required]
        fitting = [
            candidate for candidate in candidates if candidate.identity not in required
            and _evaluate(required_rows + [candidate], resources, costs) is not None
        ]
        competitive = len({candidate.workflow for candidate in fitting}) >= 2 and (
            _evaluate(required_rows + fitting, resources, costs) is None
        )
        counts["planning_feasible_ordinary_cross_workflow_capacity_competitions"] += competitive
        ordinary, protected = {}, {candidate.workflow for candidate in required_rows}
        for candidate in fitting:
            if candidate.workflow in protected:
                continue
            value = {
                "workflow": candidate.workflow,
                "remaining_model_work": observed[candidate.identity]["observed_remaining_model_work"],
                "weights": weights[candidate.identity],
            }
            if candidate.workflow in ordinary and value != ordinary[candidate.workflow]:
                raise ValueError("same-workflow candidates have inconsistent work or probabilities")
            ordinary[candidate.workflow] = value
        decisions.append({
            "monotonic_s": row["monotonic_s"], "source": row["source"],
            "reference": reference, "required_JOIN_members": sorted(required),
            "planning_capacity_competition": competitive,
            "ordinary_workflows": list(ordinary.values()), "selections": results,
        })
    competitive = [row for row in decisions if row["planning_capacity_competition"]]
    quality = {}
    for name in ("actual_cached", "synchronous", "oracle_three_bins", "oracle_continuous"):
        quality[name] = {
            "all_complete_planning_competitions": pair_quality(competitive, name),
            "both_development_workflows": pair_quality(competitive, name, measured),
        }
    return {"counts": dict(counts), "ranking": quality, "decisions": decisions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "control", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve previous observed prediction diagnostics")
    plan, control = (causal.load(path) for path in (args.registration, args.control))
    for value in (plan, control):
        causal.comparison.verify_sources(value)
    specs = [causal.load(Path(value["experiment_configuration"])) for value in (control, plan)]
    verify_learned_combination_pair(*specs)
    policy = ROOT / "beliefkv/policy/workflow_combination.py"
    if sha256_file(policy) != plan["source_sha256"].get("beliefkv/policy/workflow_combination.py"):
        raise ValueError("diagnostic combination policy differs from the actual serving policy")
    model_path = Path(specs[1]["common_environment"]["WORKFLOW_WORK_MODEL"])
    label_path = Path(control["reference_environment"]["BELIEFKV_WORKFLOW_WORK_MODEL"])
    pins = {str(path.resolve()): sha256_file(path) for path in (
        args.registration, args.control, args.cohort, model_path, label_path,
        Path(plan["experiment_configuration"]), Path(control["experiment_configuration"]),
        Path(__file__), ROOT / "scripts/audit_live_work_prediction.py",
        ROOT / "scripts/audit_workflow_combination_choices.py",
        ROOT / "beliefkv/policy/workflow_combination.py",
    )}
    reader, arm = SealedRecords(), Path(plan["run_root"]) / "full"
    rows = [
        row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == EVENT
    ]
    observations = [{
        "monotonic_s": row["monotonic_s"],
        "candidates": [
            {"key": row["keys"][candidate["identity"]]}
            for candidate in row.get("candidates", ())
        ],
    } for row in rows]
    labels = causal.load(label_path)
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    native = (
        row for row in reader(arm / "server/runtime_audit.jsonl")
        if row.get("event") == "gpu_service_sample"
    )
    timeline = observation_timeline(events, native, labels["requests"])
    model = WorkflowWorkModel.load(model_path, pins[str(model_path.resolve())])
    totals = {
        workflow: value["total_work_units"] / 100.
        for workflow, value in labels["workflows"].items()
    }
    audit(observations, timeline, labels["requests"], totals, {"synchronous": model})
    profile = model.raw["admission_cost_profile"]
    costs = planning_costs(profile)
    result = inspect(
        rows, observations, costs, profile, model.thresholds,
        set(causal.load(args.cohort)["measured_workflow_ids"]),
    )
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("an immutable combination diagnostic input changed")
    result.update(
        analysis_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip(),
        actual_cost_profile={
            "kind": profile["kind"], "identity": profile["identity"],
            "decode_cost_basis": costs.decode_cost_basis,
            "selection_objective": costs.selection_objective,
        },
        input_jsonl_prefixes=reader.prefixes, inputs_sha256=pins, GPU_action=False,
        scope={
            "actual_cached_weights_and_fixed_resource_cost_JOIN_contract": True,
            "only_complete_searches_compared_under_changed_weights": True,
            "client_publication_is_not_proof_of_native_ingest_at_that_time": True,
            "synchronous_features_use_observed_events_and_coarse_native_output_only": True,
            "future_complete_model_work_is_offline_label_only": True,
            "oracle_variants_are_model_work_not_tool_critical_path": True,
            "full_pool_planning_fit_is_not_immediate_native_fit": True,
            "all_workflow_population_includes_training_projects": True,
            "whole_run_service_or_JCT_counterfactual_proven": False,
        },
    )
    write(args.output, result)
    print(json.dumps({"counts": result["counts"], "ranking": result["ranking"]}), flush=True)


if __name__ == "__main__":
    main()
