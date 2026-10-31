"""Audit oracle selections against the matching disabled-ranking control."""

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.paper_baselines import ThunderActingDecayWorkingSet, ThunderProgram
from beliefkv.policy.workflow_size_resume import WorkflowSizeWorkingSet
from scripts import run_causal_comparison as causal
from scripts.audit_free_dispatch_service import identity
from scripts.audit_periodic_admission_decisions import (
    inspect as periodic_inspect, observation_phases,
)
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_size_selection import EVENT, ORACLE_RANK, inspect_choice
from scripts.review_matched_work_ranking import verify_ranking_only_pair
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def matched_choice(row):
    inspect_choice(row)
    if row.get("rank_kind") != ORACLE_RANK or (
        row["ordered"] and row.get("rank_before_category") is not True
    ):
        raise ValueError("require the actual global remaining-path oracle")
    ranks = {owner: 0. if rank == 0. else 1. for owner, _, rank, *_ in row["ordered"]}
    groups = {item[0]: item[5][2] for item in row["ordered"] if item[5] is not None}
    policy = WorkflowSizeWorkingSet(
        max(1, row["capacity_tokens"]), rank=lambda owner, now: ranks[owner],
        sibling_group=groups.get, rank_before_category=True, observe_ready=lambda item: True,
    )
    policy.programs = {
        owner: ThunderProgram(
            owner, tokens, steps=1 if category == 1 else 2,
            acting=category == 2, queue_sequence=sequence,
        )
        for owner, category, _rank, tokens, sequence, _group in row["ordered"]
    }
    if row["capacity_tokens"] > ThunderActingDecayWorkingSet(1).buffer_tokens:
        policy._resume_now = row["monotonic_s"]
        policy._resume()
        selected = list(policy.last_selection["selected_in_priority_order"])
        ordered = [item[0] for item in policy.last_selection["ordered"]]
    else:
        selected, ordered = [], []
    actual = row["selected_in_priority_order"]
    return {
        "monotonic_s": row["monotonic_s"], "capacity_tokens": row["capacity_tokens"],
        "oracle_selected": actual, "matched_control_selected": selected,
        "order_changed": ordered != [item[0] for item in row["ordered"]],
        "selected_set_changed": set(actual) != set(selected),
        "newly_selected_invocations": [owner for owner in actual if owner not in selected],
    }


def inspect(records, batches):
    choices = [matched_choice(row) for row in records if row.get("event") == EVENT]
    progress = periodic_inspect(records, batches)
    episodes = {}
    for item in progress["ready_grant_episodes"]:
        key = tuple(item["key"]), item["selection_ms"]
        if key in episodes:
            raise ValueError("duplicate exact periodic service episode")
        episodes[key] = item
    metadata = {
        row["monotonic_s"]: {
            item["key"]["invocation_id"]: item for item in row.get("candidates", ())
        } for row in records if row.get("event") == EVENT
    }
    if len(metadata) != len(choices):
        raise ValueError("duplicate oracle selection timestamp")
    for choice in choices:
        following = []
        for owner in choice["newly_selected_invocations"]:
            member = metadata[choice["monotonic_s"]].get(owner)
            if member is None:
                following.append({"invocation_id": owner, "periodic_episode": None})
                continue
            key = identity(member["key"], grant=True)
            episode = episodes.get((key, choice["monotonic_s"] * 1000.))
            if episode is not None and member["active_after_pressure"] != episode["active_after_pressure"]:
                raise ValueError("selection and periodic grant disagree after pressure")
            following.append({
                "invocation_id": owner, "key": key, "periodic_episode": episode,
            })
        choice["newly_selected_service"] = following
    return choices, progress


def summarize(choices):
    counts, unique, consumed = Counter(), set(), set()
    for choice in choices:
        counts["actual_oracle_controls_replayed"] += 1
        counts["matched_control_order_changes"] += choice["order_changed"]
        counts["matched_control_selected_set_changes"] += choice["selected_set_changed"]
        for item in choice["newly_selected_service"]:
            counts["newly_selected_program_episodes"] += 1
            if item.get("key") is not None:
                unique.add(tuple(item["key"]))
            episode = item["periodic_episode"]
            if episode is None:
                counts["newly_selected_without_matching_periodic_episode"] += 1
                continue
            counts["newly_selected_with_matching_periodic_episode"] += 1
            counts["newly_selected_retained_after_pressure"] += episode["active_after_pressure"]
            if episode["following_native_service_ms"] is not None:
                counts["newly_selected_with_following_exact_service"] += 1
                consumed.add(tuple(item["key"]))
            counts["newly_selected_with_bounded_useful_completion"] += (
                episode["new_completion_before_next_control_or_intervention"] is True
            )
            counts["newly_selected_with_open_observation_window"] += (
                episode["observation_window_end_ms"] is None
            )
    counts["unique_newly_selected_request_attempts"] = len(unique)
    counts["unique_newly_selected_attempts_with_following_service"] = len(consumed)
    return dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("control", "oracle", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve previous matched service observations")
    plans = [causal.load(path) for path in (args.control, args.oracle)]
    for plan in plans:
        causal.comparison.verify_sources(plan)
    specifications = [causal.load(Path(plan["experiment_configuration"])) for plan in plans]
    artifacts = verify_ranking_only_pair(*specifications)
    for name in ("beliefkv/policy/workflow_size_resume.py", "beliefkv/policy/paper_baselines.py"):
        if sha256_file(ROOT / name) != plans[1]["source_sha256"].get(name):
            raise ValueError("local replay differs from the executed policy")
    if args.complete:
        evidence = [verify_execution(path, args.cohort) for path in (args.control, args.oracle)]
        if evidence[0]["work"] != evidence[1]["work"]:
            raise ValueError("completed measured work changed")
    else:
        state = causal.load(args.oracle.with_suffix(".state.json"))
        if state["status"] != "running_full" or not all(
            causal.reservation.alive(state.get(name)) for name in ("watcher", "server", "driver")
        ):
            raise ValueError("live audit requires actual running oracle owners")
    plan, reader = plans[1], SealedRecords()
    arm = Path(plan["run_root"]) / "full"
    records = list(reader(arm / "opportunities/admission_opportunities.jsonl"))
    choices, progress = inspect(records, reader(arm / "server/runtime_audit.jsonl"))
    arrivals_path = Path(plan["workflow_arrivals_path"])
    arrivals = causal.load(arrivals_path)
    if arrivals != plan["workflow_arrivals"]:
        raise ValueError("oracle arrival table changed")
    phases = observation_phases(
        progress, arrivals, causal.load(args.cohort),
        reader(arm / "replay/runtime_events.replay.jsonl"),
    )
    origin = phases["origin_monotonic_ms"] / 1000.
    phase_counts = {}
    for name, phase in phases["phases"].items():
        phase_counts[name] = {
            **summarize([
                row for row in choices if row["monotonic_s"] >= origin + phase["start_s"]
                and (phase["end_s"] is None or row["monotonic_s"] < origin + phase["end_s"])
            ]),
            "complete_phase_observed": phase["complete_phase_observed"],
        }
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "counts": summarize(choices), "phase_counts": phase_counts, "decisions": choices,
        "complete_measured_execution_verified": args.complete,
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            args.control, args.oracle, args.cohort, arrivals_path, *artifacts,
            *(Path(plan["experiment_configuration"]) for plan in plans), Path(__file__),
            ROOT / "scripts/audit_periodic_admission_decisions.py",
            ROOT / "scripts/audit_workflow_size_selection.py",
            ROOT / "scripts/review_matched_work_ranking.py",
        )},
        "scope": {
            "actual_local_selection_reproduced": True,
            "same_candidates_budget_buffer_and_ready_join_protection": True,
            "same_observed_sibling_adjacency": True,
            "disabled_ranking_repaired_control_not_legacy_paper_reference": True,
            "pressure_and_future_state_counterfactual_reconstructed": False,
            "physical_allocation_or_copy_feasibility_counterfactual": False,
            "observed_pauses_and_replacement_regrants_bound_service_windows": True,
            "missing_service_evidence_remains_unknown": True,
            "following_service_is_exclusive_causal_JCT_gain": False,
            "JCT_or_throughput_counterfactual": False,
        },
        "formal_workload_accepted": False,
        "GPU_action": False, "performance_accepted": False,
    }
    write(args.output, result)
    print({"counts": result["counts"], "phase_counts": phase_counts})


if __name__ == "__main__":
    main()
