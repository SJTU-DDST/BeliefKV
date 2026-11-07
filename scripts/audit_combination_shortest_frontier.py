"""Compare bounded packing with shortest-work ordering on the same sealed states."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import CombinationResources, _evaluate
from scripts.audit_learned_combination_prediction import combination_candidates, planning_costs
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_combination_choices import EVENT, replay_choice


def greedy_frontier(candidates, required, resources, costs, ranks):
    by_id = {candidate.identity: candidate for candidate in candidates}
    if len(by_id) != len(candidates) or len(required) != len(set(required)) or not set(required) <= by_id.keys():
        raise ValueError("invalid candidate or required JOIN identities")
    if set(ranks) != set(by_id) or any(
        type(rank) not in (int, float) or not math.isfinite(rank) or rank < 0
        for rank in ranks.values()
    ):
        raise ValueError("each bounded candidate requires a finite nonnegative rank")
    selected = [by_id[owner] for owner in required]
    if selected and _evaluate(selected, resources, costs) is None:
        return None
    ordered = sorted(
        (candidate for candidate in candidates if candidate.identity not in required),
        key=lambda candidate: (
            ranks[candidate.identity], candidate.category, candidate.logical_tokens, candidate.sequence,
        ),
    )
    groups = defaultdict(list)
    for candidate in ordered:
        groups[candidate.workflow, candidate.group, candidate.category, ranks[candidate.identity]].append(candidate)
    seen = set()
    for candidate in ordered:
        for peer in groups[candidate.workflow, candidate.group, candidate.category, ranks[candidate.identity]]:
            if peer.identity in seen:
                continue
            seen.add(peer.identity)
            if _evaluate([*selected, peer], resources, costs) is not None:
                selected.append(peer)
    return [candidate.identity for candidate in selected]


def inspect(row, evidence, costs, profile, measured):
    if row["monotonic_s"] != evidence["monotonic_s"] or row["source"] != evidence["source"]:
        raise ValueError("prediction evidence belongs to a different actual boundary")
    replay = replay_choice(
        row, costs, profile["identity"],
        allow_feasible_resource_alternatives=True, plan_device_capacity=True,
    )
    if not replay["search_replayed"] or row["decision"]["search_complete"] is not True:
        raise ValueError("require exact replay of a complete recorded search")
    candidates, resources = combination_candidates(row), CombinationResources(**row["resources"])
    required = row["decision"]["required_reference_members"]
    protected = {candidate.workflow for candidate in candidates if candidate.identity in required}
    labels = {item["workflow"]: item for item in evidence["ordinary_workflows"]}
    if len(labels) != len(evidence["ordinary_workflows"]):
        raise ValueError("duplicate workflow labels at a recorded boundary")
    for candidate in candidates:
        label = labels.get(candidate.workflow)
        if label and (
            not math.isfinite(label["remaining_model_work"]) or label["remaining_model_work"] < -1e-6
            or not math.isclose(candidate.weight, label["weights"]["actual_cached"], abs_tol=1e-12)
        ):
            raise ValueError("candidate work or cached prediction differs from the sealed audit")
    eligible = [
        candidate for candidate in candidates
        if candidate.workflow in labels or candidate.workflow in protected
    ]
    required_rows = [candidate for candidate in candidates if candidate.identity in required]
    if any(
        candidate not in eligible
        and _evaluate([*required_rows, candidate], resources, costs) is not None
        for candidate in candidates
    ):
        raise ValueError("an individually fitting ordinary candidate has no exact work label")
    actual = row["decision"]["selected"]
    if set(actual) != set(evidence["selections"]["actual_cached"]["selected"]):
        raise ValueError("actual selection differs from the prediction audit")
    choices = {
        "actual_combination": list(actual),
        "truth_weighted_combination": evidence["selections"]["oracle_continuous"]["selected"],
    }
    for name, field in (("predicted_greedy", "weight"), ("truth_greedy", "work")):
        ranks = {
            candidate.identity: (
                0. if candidate.workflow in protected else
                2. - candidate.weight if field == "weight" else
                1. + max(0., labels[candidate.workflow]["remaining_model_work"])
            ) for candidate in eligible
        }
        choices[name] = greedy_frontier(eligible, required, resources, costs, ranks)
    for selected in choices.values():
        if selected is not None and not set(required) <= set(selected):
            raise ValueError("a diagnostic choice revoked required JOIN protection")
    minimum = min((item["remaining_model_work"] for item in labels.values()), default=None)
    shortest = {
        workflow for workflow, label in labels.items()
        if label["remaining_model_work"] <= minimum + 1e-6
    } if minimum is not None else set()
    by_id = {candidate.identity: candidate for candidate in candidates}
    summaries = {}
    for name, selected in choices.items():
        evaluation = None if selected is None else _evaluate(
            [by_id[owner] for owner in selected], resources, costs,
        )
        summaries[name] = {
            "selected": selected, "planning_feasible": evaluation is not None,
            "includes_true_shortest_ordinary_workflow": selected is not None and any(
                by_id[owner].workflow in shortest for owner in selected
            ),
            "set_changed_from_actual": selected is not None and set(selected) != set(actual),
            "changes_touch_measured": selected is not None and any(
                by_id[owner].workflow in measured for owner in set(selected) ^ set(actual)
            ),
            "evaluation": evaluation,
        }
    selected_workflows = {by_id[owner].workflow for owner in actual}
    ordinary_selected = selected_workflows & labels.keys()
    passed = []
    for candidate in eligible:
        if (
            candidate.workflow not in labels or candidate.workflow in selected_workflows
            or candidate.unfinished_members != 1 or candidate.active_ready_members != 0
            or sum(peer.workflow == candidate.workflow for peer in candidates) != 1
            or len(ordinary_selected) < 2
        ):
            continue
        work = labels[candidate.workflow]["remaining_model_work"]
        if work + 1e-6 >= min(labels[workflow]["remaining_model_work"] for workflow in ordinary_selected):
            continue
        passed.append({
            "key": row["keys"][candidate.identity], "logical_tokens": candidate.logical_tokens,
            "remaining_model_work": work, "cached_weight": candidate.weight,
            "selected_longer_workflows": sorted(ordinary_selected),
            "truth_weighted_combination_still_omits": candidate.identity not in choices[
                "truth_weighted_combination"
            ],
            "predicted_greedy_includes": candidate.identity in (choices["predicted_greedy"] or ()),
            "truth_greedy_includes": candidate.identity in (choices["truth_greedy"] or ()),
            "measured_workflow": candidate.workflow in measured,
        })
    return {
        "monotonic_s": row["monotonic_s"], "source": row["source"],
        "planning_capacity_competition": evidence["planning_capacity_competition"],
        "required_JOIN_members": required, "ordinary_workflows": evidence["ordinary_workflows"],
        "choices": summaries, "single_frontiers_omitted_for_multiple_longer_workflows": passed,
    }


def totals(decisions):
    result = {"complete_boundaries": len(decisions)}
    for population, rows in (
        ("all_complete_boundaries", decisions),
        ("ordinary_capacity_competitions", [row for row in decisions if row["planning_capacity_competition"]]),
    ):
        counts = Counter({"boundaries": len(rows)})
        workflows = set()
        for row in rows:
            for name, choice in row["choices"].items():
                counts[name + "_planning_feasible"] += choice["planning_feasible"]
                counts[name + "_includes_true_shortest"] += choice[
                    "includes_true_shortest_ordinary_workflow"
                ]
                counts[name + "_set_changes"] += choice["set_changed_from_actual"]
                counts[name + "_changes_touch_measured"] += choice["changes_touch_measured"]
            passed = row["single_frontiers_omitted_for_multiple_longer_workflows"]
            counts["omitted_short_single_frontier_boundaries"] += bool(passed)
            counts["omitted_short_single_frontier_observations"] += len(passed)
            counts["omitted_measured_short_single_frontier_observations"] += sum(
                item["measured_workflow"] for item in passed
            )
            workflows.update(item["key"]["root_workflow_id"] for item in passed)
            for item in passed:
                for name in ("truth_weighted_combination_still_omits", "predicted_greedy_includes",
                             "truth_greedy_includes"):
                    counts["omitted_short_" + name] += item[name]
        result[population] = {**counts, "distinct_omitted_short_single_frontier_workflows": len(workflows)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "prediction-audit", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve evidence and require nice19 CPU-only execution")
    registration = json.loads(args.registration.read_bytes())
    prediction = json.loads(args.prediction_audit.read_bytes())
    cohort = json.loads(args.cohort.read_bytes())
    config_path = Path(registration["experiment_configuration"])
    config = json.loads(config_path.read_bytes())
    model_path = Path(config["common_environment"]["WORKFLOW_WORK_MODEL"])
    profile = json.loads(model_path.read_bytes())["admission_cost_profile"]
    costs = planning_costs(profile)
    policy = ROOT / "beliefkv/policy/workflow_combination.py"
    if sha256_file(policy) != registration["source_sha256"].get(str(policy.relative_to(ROOT))):
        raise ValueError("selection core differs from actual serving")
    paths = (
        args.registration, args.prediction_audit, args.cohort, config_path, model_path,
        policy, Path(__file__), ROOT / "scripts/audit_learned_combination_prediction.py",
        ROOT / "scripts/audit_workflow_combination_choices.py",
        ROOT / "scripts/audit_sealed_wait_prefetch.py",
    )
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    for path in (args.registration, args.cohort, config_path, model_path):
        if prediction["inputs_sha256"].get(str(path.resolve())) != pins[str(path.resolve())]:
            raise ValueError("prediction audit uses a different registered model, cohort or configuration")
    expected = {row["monotonic_s"]: row for row in prediction["decisions"]}
    if len(expected) != len(prediction["decisions"]):
        raise ValueError("ambiguous complete decision timestamp")
    reader = SealedRecords()
    opportunities = Path(registration["run_root"]) / "full/opportunities/admission_opportunities.jsonl"
    reader.prefixes[str(opportunities)] = prediction["input_jsonl_prefixes"][str(opportunities)]
    decisions = [
        inspect(row, expected[row["monotonic_s"]], costs, profile, set(cohort["measured_workflow_ids"]))
        for row in reader(opportunities) if row.get("event") == EVENT
        and row.get("monotonic_s") in expected
    ]
    if len(decisions) != len(expected) or any(
        sha256_file(Path(path)) != digest for path, digest in pins.items()
    ):
        raise ValueError("an exact boundary or pinned input changed")
    output = {
        **totals(decisions), "decisions": decisions,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "inputs_sha256": pins, "input_jsonl_prefixes": reader.prefixes,
        "scope": {
            "same_actual_bounded_candidates_resources_and_required_JOIN": True,
            "same_group_adjacency_without_waiting_for_unready_siblings": True,
            "truth_is_remaining_total_model_work_not_tool_critical_path": True,
            "only_complete_actual_searches_used": True,
            "planning_fit_is_not_immediate_native_fit_or_service": True,
            "same_history_diagnostic_not_future_execution_simulation": True,
            "no_JCT_or_throughput_gain_estimate": True,
            "training_projects_included_not_held_out_accuracy": True,
        },
        "GPU_action": False, "performance_accepted": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(output, stream, indent=2, allow_nan=False)
    print(json.dumps(totals(decisions)), flush=True)


if __name__ == "__main__":
    main()
