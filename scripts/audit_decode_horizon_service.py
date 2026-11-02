"""Associate horizon64's extra selections with actual grants and bounded native service."""

import argparse
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import CombinationCosts, CombinationResources, choose_combination
from scripts import run_causal_comparison as causal
from scripts.audit_learned_combination_prediction import combination_candidates
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_combination_choices import EVENT, inspect as choices
from scripts.review_learned_work_ranking import verify_learned_combination_pair
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def inspect(records, batches, costs, profile_identity, measured):
    if costs.decode_steps != 64:
        raise ValueError("require the actual declared 64-step commitment horizon")
    actual = choices(
        records, batches, costs, profile_identity, allow_feasible_resource_alternatives=True,
        plan_device_capacity=True, include_all_selected_service=True,
    )
    counts, comparisons = Counter(), []
    for choice in actual["decisions"]:
        counts["entries"] += 1
        if not choice["search_replayed"]:
            counts["metadata_unavailable"] += 1
            continue
        if not choice["search_complete"]:
            counts["incomplete_searches_excluded"] += 1
            continue
        if not choice["all_selected_metadata_complete"]:
            counts["selected_metadata_incomplete"] += 1
            continue
        row = records[choice["journal_sequence"]]
        if row["event"] != EVENT or row["monotonic_s"] != choice["monotonic_s"]:
            raise ValueError("a choice changed its exact journal boundary")
        alternative = choose_combination(
            combination_candidates(row), row["decision"]["reference"],
            CombinationResources(**row["resources"]), replace(costs, decode_steps=16),
            required=row["decision"]["required_reference_members"], clock=lambda: 0,
            allow_feasible_resource_alternatives=True,
        )
        actual_set, old_set = set(choice["selected"]), set(alternative["selected"])
        extra, removed = actual_set - old_set, old_set - actual_set
        counts["complete_searches"] += 1
        counts["horizon_changes_selected_set"] += bool(extra or removed)
        counts["horizon_selects_more_members"] += len(actual_set) > len(old_set)
        counts["horizon_selects_fewer_members"] += len(actual_set) < len(old_set)
        if not extra:
            continue
        source = choice["source"]
        services = {
            service["invocation_id"]: service for service in choice[
                "all_periodic_service" if source == "periodic_decay_resume" else "all_free_service"
            ]
        }
        episodes = []
        for owner in sorted(extra):
            key = row["keys"][owner]
            service = services.get(owner)
            episode = service.get("episode") if service is not None else None
            separate = bool(service and service.get("separate_tool_replacement"))
            grant = bool(service) and (
                choice.get("following_periodic_grant_verified", False)
                if source == "periodic_decay_resume" else service["actual_grant_verified"]
            )
            direct = grant and not separate
            value = {
                "invocation_id": owner, "key": key,
                "in_original_reference": owner in row["decision"]["reference"],
                "measured_workflow": key["root_workflow_id"] in measured,
                "actual_grant_verified": grant, "direct_combination_grant": direct,
                "separate_tool_replacement": separate, "progress_episode": episode,
            }
            episodes.append(value)
            counts["extra_selected_episodes"] += 1
            counts["extra_reference_members"] += value["in_original_reference"]
            counts["extra_measured_episodes"] += value["measured_workflow"]
            counts["extra_actual_grants"] += grant
            counts["extra_direct_grants"] += direct
            counts["extra_without_direct_combination_progress_evidence"] += not direct or episode is None
            counts["extra_separate_tool_replacement_grants"] += separate
            if direct and episode is not None:
                retained = episode.get("selection_retained", episode.get("active_after_pressure"))
                if type(retained) is not bool:
                    raise ValueError("progress lacks observed eligibility at grant")
                counts["extra_verified_eligible_at_grant"] += retained
                if source == "periodic_decay_resume":
                    counts["extra_periodic_retained_after_pressure"] += episode["active_after_pressure"]
                counts["extra_open_progress_windows"] += episode["observation_window_end_ms"] is None
                counts["extra_followed_by_exact_native_service"] += episode["following_native_service_ms"] is not None
                counts["extra_bounded_new_useful_completions"] += (
                    episode["new_completion_before_next_control_or_intervention"] is True
                )
        comparisons.append({
            "source": source, "monotonic_s": choice["monotonic_s"],
            "journal_sequence": choice["journal_sequence"],
            "actual_horizon64_selected": choice["selected"],
            "hypothetical_horizon16_selected": alternative["selected"],
            "removed_relative_to_horizon16": sorted(removed), "extra_members": episodes,
        })
    return {
        "counts": dict(counts), "comparisons": comparisons,
        "original_reference_new_member_audit_counts": actual["counts"],
        "scope": {
            "same_actual_snapshot_weights_resources_and_required_JOIN_rights": True,
            "horizon16_is_local_counterfactual_not_a_complete_run": True,
            "only_complete_searches_compared": True,
            "retained_reference_members_included": True,
            "exact_grant_and_attempt_session_native_progress_required": True,
            "progress_bounded_by_next_control_or_real_intervention": True,
            "separate_A_to_B_tool_replacement_excluded": True,
            "free_eligibility_is_observed_at_publication_not_assumed_after_pressure": True,
            "following_exact_service_is_not_exclusive_causal_attribution": True,
            "missing_or_open_episode_evidence_is_unknown": True,
            "whole_run_JCT_or_throughput_gain_proven": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "control", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve earlier horizon-service evidence")
    plans = [causal.load(path) for path in (args.control, args.registration)]
    for plan in plans:
        causal.comparison.verify_sources(plan)
    specs = [causal.load(Path(plan["experiment_configuration"])) for plan in plans]
    verify_learned_combination_pair(*specs)
    model_path = Path(specs[1]["common_environment"]["WORKFLOW_WORK_MODEL"])
    if sha256_file(model_path) != specs[1]["common_environment"]["WORKFLOW_WORK_MODEL_SHA256"]:
        raise ValueError("actual horizon model changed")
    profile = causal.load(model_path)["admission_cost_profile"]
    if profile["kind"] != "r5_frozen_batch_cost_proxy_capacity_v6":
        raise ValueError("require the unchanged native-capacity planning contract")
    plan = plans[1]
    if sha256_file(ROOT / "beliefkv/policy/workflow_combination.py") != plan["source_sha256"].get(
        "beliefkv/policy/workflow_combination.py",
    ):
        raise ValueError("analysis selection code differs from actual serving")
    if args.complete:
        verify_execution(args.registration, args.cohort)
    else:
        state = causal.load(args.registration.with_suffix(".state.json"))
        if state["status"] != "running_full" or not all(
            causal.reservation.alive(state.get(name)) for name in ("watcher", "server", "driver")
        ):
            raise ValueError("live horizon audit requires actual running owners")
    reader, arm = SealedRecords(), Path(plan["run_root"]) / "full"
    records = list(reader(arm / "opportunities/admission_opportunities.jsonl"))
    result = inspect(
        records, reader(arm / "server/runtime_audit.jsonl"),
        CombinationCosts(**profile["costs"]), profile["identity"],
        set(causal.load(args.cohort)["measured_workflow_ids"]),
    )
    result.update(
        input_jsonl_prefixes=reader.prefixes, complete_measured_execution_verified=args.complete,
        analysis_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip(),
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in (
            args.registration, args.control, args.cohort, model_path, Path(__file__),
            ROOT / "scripts/audit_workflow_combination_choices.py",
            ROOT / "scripts/audit_periodic_admission_decisions.py",
            ROOT / "scripts/audit_free_dispatch_service.py",
            ROOT / "beliefkv/policy/workflow_combination.py",
        )},
        GPU_action=False, performance_accepted=False,
    )
    write(args.output, result)
    print(json.dumps(result["counts"]), flush=True)


if __name__ == "__main__":
    main()
