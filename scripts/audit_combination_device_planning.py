"""Compare instantaneous and pool-bound planning on sealed complete searches."""

import argparse
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import (
    CombinationCandidate, CombinationCosts, CombinationResources, _evaluate, choose_combination,
)
from scripts import run_causal_comparison as causal
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_combination_choices import replay_choice
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def competition(candidates, required, resources, costs):
    protected_workflows = {row.workflow for row in candidates if row.identity in required}
    coverage = []
    for mask in range(1, 1 << len(candidates)):
        rows = [row for index, row in enumerate(candidates) if mask & (1 << index)]
        if not set(required) <= {row.identity for row in rows}:
            continue
        if _evaluate(rows, resources, costs) is not None:
            coverage.append({row.workflow for row in rows} - protected_workflows)
    available = set().union(*coverage) if coverage else set()
    return {
        "any_feasible_subset": bool(coverage),
        "feasible_unprotected_workflows": len(available),
        "multiple_workflows_each_have_a_feasible_selection": len(available) >= 2,
        "cannot_cover_all_feasible_workflows": (
            len(available) >= 2 and not any(available <= covered for covered in coverage)
        ),
    }


def compare_choice(row, costs, identity, device_capacity):
    replay_choice(row, costs, identity, allow_feasible_resource_alternatives=True)
    observed = row["decision"]
    if "evaluated_subsets" not in observed:
        return {"kind": "metadata_entry"}
    if not observed["search_complete"]:
        return {
            "kind": "actual_deadline" if observed["reason"].startswith("soft_deadline_")
            else "early_return",
            "reason": observed["reason"],
        }
    candidates = []
    for raw in row["candidates"]:
        value = dict(raw)
        value["missing_extents"] = tuple(
            (tuple(key) if isinstance(key, list) else key, tokens)
            for key, tokens in value["missing_extents"]
        )
        candidates.append(CombinationCandidate(**value))
    resources = CombinationResources(**row["resources"])
    if type(device_capacity) is not int or device_capacity <= 0 or (
        device_capacity < resources.device_tokens + resources.cold_device_tokens
    ):
        raise ValueError("capacity census contradicts the recorded device summary")
    planned = replace(resources, device_capacity_tokens=device_capacity)
    full_costs = replace(costs, decode_cost_basis="full_batch")
    kwargs = {
        "required": observed["required_reference_members"],
        "allow_feasible_resource_alternatives": True, "clock": lambda: 0,
    }
    before = choose_combination(candidates, observed["reference"], resources, full_costs, **kwargs)
    after = choose_combination(candidates, observed["reference"], planned, full_costs, **kwargs)
    if not set(kwargs["required"]) <= set(after["selected"]):
        raise ValueError("planned combination dropped required JOIN rights")
    return {
        "kind": "complete_search", "source": row["source"],
        "monotonic_s": row["monotonic_s"],
        "strict": before, "planned": after,
        "strict_competition": competition(candidates, kwargs["required"], resources, full_costs),
        "planned_competition": competition(candidates, kwargs["required"], planned, full_costs),
        "selected_set_changed": set(before["selected"]) != set(after["selected"]),
        "new_reference_external_members": sorted(set(after["selected"]) - set(observed["reference"])),
        "resources": row["resources"], "planned_device_capacity_tokens": device_capacity,
    }


def summarize(choices):
    counts, variants = Counter(), {"strict": Counter(), "planned": Counter()}
    for row in choices:
        counts[row["kind"]] += 1
        if row["kind"] != "complete_search":
            continue
        counts["planning_changed_full_batch_selected_set"] += row["selected_set_changed"]
        counts["planning_selected_more_members"] += len(row["planned"]["selected"]) > len(row["strict"]["selected"])
        counts["planning_selected_fewer_members"] += len(row["planned"]["selected"]) < len(row["strict"]["selected"])
        for name in variants:
            decision, contest = row[name], row[name + "_competition"]
            tally = variants[name]
            for field in (
                "any_feasible_subset", "multiple_workflows_each_have_a_feasible_selection",
                "cannot_cover_all_feasible_workflows",
            ):
                tally[field] += contest[field]
            tally["no_feasible_subset"] += not contest["any_feasible_subset"]
            tally["strict_reference_shrink"] += set(decision["selected"]) < set(decision["reference"])
            tally["reference_external_selected_members"] += len(
                set(decision["selected"]) - set(decision["reference"])
            )
            tally["selected_members_including_repeated_decisions"] += len(decision["selected"])
            evaluation = decision.get("selected_evaluation")
            if name == "planned" and evaluation is not None:
                tally["selected_evaluation_requires_native_capacity_wait"] += not evaluation[
                    "current_device_summary_fits"
                ]
    return {"counts": dict(counts), "variants": {key: dict(value) for key, value in variants.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "choice-report", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve previous planning diagnostics")
    completion = verify_execution(args.registration, args.cohort)
    plan, prior = causal.load(args.registration), causal.load(args.choice_report)
    if prior["registration"] != str(args.registration.resolve()) or (
        prior.get("complete_measured_execution_verified") is not True
    ):
        raise ValueError("require a complete sealed choice report for the same registration")
    config_path = Path(plan["experiment_configuration"])
    config = causal.load(config_path)
    artifact_path = Path(config["common_environment"]["WORKFLOW_WORK_MODEL"])
    if sha256_file(artifact_path) != config["common_environment"]["WORKFLOW_WORK_MODEL_SHA256"]:
        raise ValueError("frozen cost artifact changed")
    profile = causal.load(artifact_path)["admission_cost_profile"]
    if profile["kind"] != "r5_frozen_batch_cost_proxy_capacity_v4":
        raise ValueError("this differential requires the original complete v4 search history")
    capacity_path = Path(plan["run_root"]) / "full/server/native_capacity_census.json"
    capacity = causal.load(capacity_path)["capacity"]
    if capacity["pool_layout"] != "static_full_only":
        raise ValueError("pool-bound planning requires observed FULL-only capacity")
    journal = str(Path(plan["run_root"]) / "full/opportunities/admission_opportunities.jsonl")
    reader = SealedRecords()
    reader.prefixes[journal] = prior["input_jsonl_prefixes"][journal]
    costs = CombinationCosts(**profile["costs"])
    choices = [
        compare_choice(row, costs, profile["identity"], capacity["device_full_tokens"])
        for row in reader(Path(journal))
        if row.get("event") == "workflow_combination_selection"
    ]
    if len(choices) != prior["counts"]["combination_passes"]:
        raise ValueError("differential changed the complete recorded scope")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(), **summarize(choices),
        "decisions": [row for row in choices if row["kind"] == "complete_search"],
        "exclusions": dict(Counter(
            (row["kind"] + ":" + row.get("reason", ""))
            for row in choices if row["kind"] != "complete_search"
        )),
        "observed_capacity": capacity,
        "complete_measured_work_and_owned_exit_verified": completion["actual_owned_exit_verified"],
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            args.registration, args.choice_report, args.cohort, config_path, artifact_path,
            capacity_path, Path(__file__), ROOT / "beliefkv/policy/workflow_combination.py",
            ROOT / "scripts/audit_workflow_combination_choices.py",
        )},
        "scope": {
            "every_legacy_search_first_reproduced_with_its_actual_cutoff": True,
            "only_complete_actual_searches_have_hypothetical_enumeration": True,
            "same_future_work_weights_costs_candidates_logical_budget_slots_and_JOIN_rights": True,
            "planned_scope_changes_only_the_device_capacity_bound": True,
            "whole_pool_geometry_is_not_immediate_physical_feasibility": True,
            "required_device_capacity_wait_may_delay_selected_work": True,
            "native_allocation_locks_Host_and_DMA_checks_not_removed": True,
            "new_online_search_deadlines_or_following_service_simulated": False,
            "JCT_or_throughput_improvement_proven": False,
        },
        "GPU_action": False, "performance_accepted": False,
    }
    write(args.output, result)
    print(summarize(choices))


if __name__ == "__main__":
    main()
