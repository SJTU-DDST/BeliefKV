"""Trace actual oracle set changes against the matched nonranking local rule."""

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
    EVENT as GRANT_EVENT, inspect as periodic_inspect, observation_phases,
)
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_size_selection import EVENT, ORACLE_RANK, finite, inspect_choice
from scripts.review_matched_work_ranking import verify_ranking_only_pair
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def matched_reference(row):
    inspect_choice(row)
    if row.get("rank_kind") != ORACLE_RANK or (
        row["ordered"] and row.get("rank_before_category") is not True
    ):
        raise ValueError("require an actual global ranked-oracle selection")
    metadata, ranks, work = {}, {}, {}
    for item in row.get("candidates", ()):
        key = identity(item["key"], grant=True)
        if key[2] in metadata:
            raise ValueError("duplicate oracle candidate metadata")
        metadata[key[2]] = item
    for owner, _category, rank, _tokens, _sequence, _group in row["ordered"]:
        item = metadata.get(owner)
        prediction = item.get("prediction") if item is not None else None
        remaining = prediction.get("remaining_path_s") if isinstance(prediction, dict) else None
        if (
            prediction is None or prediction.get("evidence") != "oracle"
            or not finite(remaining) or remaining < 0
            or rank not in (0., remaining + 1.) or item.get("bucket") != rank
        ):
            raise ValueError("actual oracle rank differs from its bound remaining work")
        workflow = item["key"]["root_workflow_id"]
        if workflow in work and work[workflow] != remaining:
            raise ValueError("one boundary has inconsistent workflow remaining work")
        work[workflow] = remaining
        ranks[owner] = 0. if rank == 0. else 1.
    if set(metadata) != {item[0] for item in row["ordered"]}:
        raise ValueError("oracle metadata differs from the actual candidate set")
    groups = {item[0]: item[5][2] for item in row["ordered"] if item[5] is not None}
    policy = WorkflowSizeWorkingSet(
        max(1, row["capacity_tokens"]), rank=lambda owner, now: ranks[owner],
        sibling_group=groups.get, rank_before_category=True, observe_ready=lambda item: True,
    )
    policy.programs = {
        owner: ThunderProgram(
            owner, tokens, steps=1 if category == 1 else 2,
            acting=category == 2, queue_sequence=sequence,
        ) for owner, category, _rank, tokens, sequence, _group in row["ordered"]
    }
    buffer = ThunderActingDecayWorkingSet(1).buffer_tokens
    reference, order = [], []
    if row["capacity_tokens"] > buffer:
        policy._resume_now = row["monotonic_s"]
        policy._resume()
        reference = list(policy.last_selection["selected_in_priority_order"])
        order = [item[0] for item in policy.last_selection["ordered"]]
    selected = row["selected_in_priority_order"]
    return {
        "monotonic_s": row["monotonic_s"], "capacity_tokens": row["capacity_tokens"],
        "oracle_selected": selected, "matched_control_selected": reference,
        "candidate_order_changed": order != [item[0] for item in row["ordered"]],
        "selected_set_changed": set(reference) != set(selected),
        "newly_selected_invocations": [owner for owner in selected if owner not in reference],
        "periodic_service": [],
    }


def totals(decisions):
    counts = Counter({name: 0 for name in (
        "actual_oracle_selections_replayed", "selected_set_changes",
        "candidate_order_changes", "newly_selected_agent_episodes",
        "newly_selected_with_exact_ready_grant", "newly_selected_retained_after_pressure",
        "newly_selected_followed_by_exact_service", "newly_selected_with_bounded_useful_service",
        "newly_selected_with_bounded_useful_completion", "newly_selected_open_service_window",
    )})
    unique, served, bounded = set(), set(), set()
    for decision in decisions:
        counts["actual_oracle_selections_replayed"] += 1
        counts["selected_set_changes"] += decision["selected_set_changed"]
        counts["candidate_order_changes"] += decision["candidate_order_changed"]
        counts["newly_selected_agent_episodes"] += len(decision["newly_selected_invocations"])
        for member in decision["periodic_service"]:
            key, episode = tuple(member["key"]), member["episode"]
            unique.add(key)
            counts["newly_selected_with_exact_ready_grant"] += episode is not None
            if episode is None:
                continue
            counts["newly_selected_retained_after_pressure"] += episode["active_after_pressure"]
            following = episode["following_native_service_ms"] is not None
            useful = episode["new_service_before_next_control_or_intervention"] is True
            counts["newly_selected_followed_by_exact_service"] += following
            counts["newly_selected_with_bounded_useful_service"] += useful
            counts["newly_selected_with_bounded_useful_completion"] += (
                episode["new_completion_before_next_control_or_intervention"] is True
            )
            counts["newly_selected_open_service_window"] += episode["observation_window_end_ms"] is None
            if following:
                served.add(key)
            if useful:
                bounded.add(key)
    counts.update(
        unique_newly_selected_request_attempts=len(unique),
        unique_newly_selected_attempts_followed_by_service=len(served),
        unique_newly_selected_attempts_with_bounded_useful_service=len(bounded),
    )
    return dict(counts)


def inspect(records, batches):
    records = list(records)
    progress = periodic_inspect(records, batches)
    episodes = {}
    for episode in progress["ready_grant_episodes"]:
        key = episode["selection_ms"], tuple(episode["key"])
        if key in episodes:
            raise ValueError("duplicate exact periodic grant episode")
        episodes[key] = episode
    grants = {}
    for row in records:
        if row.get("event") == GRANT_EVENT:
            if row["monotonic_s"] in grants:
                raise ValueError("duplicate periodic control clock")
            grants[row["monotonic_s"]] = row
    decisions = []
    for row in records:
        if row.get("event") != EVENT:
            continue
        choice = matched_reference(row)
        grant = grants.get(row["monotonic_s"])
        if grant is None or set(grant["resumed_program_ids"]) != set(choice["oracle_selected"]):
            raise ValueError("actual oracle selection differs from its exact periodic grant")
        members = {item["invocation_id"]: item for item in grant["programs"]}
        metadata = {item["key"]["invocation_id"]: item for item in row.get("candidates", ())}
        for owner in choice["newly_selected_invocations"]:
            key = identity(metadata[owner]["key"], grant=True)
            if identity(members[owner]["key"], grant=True) != key:
                raise ValueError("oracle grant changed the selected request attempt")
            choice["periodic_service"].append({
                "invocation_id": owner, "key": key,
                "episode": episodes.get((row["monotonic_s"] * 1000., key)),
            })
        decisions.append(choice)
    return {
        "counts": totals(decisions), "decisions": decisions,
        "periodic_progress_counts": progress["counts"],
        "control_observations": progress["control_observations"],
        "scope": {
            "actual_oracle_reproduced_from_frozen_policy": True,
            "local_reference_is_matched_nonranking_Full_not_paper_ThunderAgent": True,
            "reference_preserves_JOIN_protection_categories_groups_buffer_and_candidates": True,
            "periodic_grant_requires_same_boundary_and_request_attempt": True,
            "useful_progress_bounded_by_next_control_or_observed_same_agent_intervention": True,
            "open_or_missing_progress_is_unknown_not_failure_or_zero_latency": True,
            "episode_and_unique_attempt_counts_reported_separately": True,
            "other_admission_paths_and_counterfactual_pressure_not_reconstructed": True,
            "reference_native_physical_fit_not_proven": True,
            "JCT_throughput_or_whole_run_counterfactual": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "control", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve prior actual-oracle service evidence")
    plan, control = (causal.load(path) for path in (args.registration, args.control))
    for item in (plan, control):
        causal.comparison.verify_sources(item)
    configs = [Path(item["experiment_configuration"]) for item in (control, plan)]
    artifacts = verify_ranking_only_pair(
        *(causal.load(path) for path in configs), allow_legacy_control_declaration=True,
    )
    sources = [ROOT / name for name in (
        "beliefkv/policy/workflow_size_resume.py", "beliefkv/policy/paper_baselines.py",
        "beliefkv/runtime/workflow_oracle_admission.py",
    )]
    for path in sources:
        if sha256_file(path) != plan["source_sha256"].get(str(path.relative_to(ROOT))):
            raise ValueError("local audit differs from the actual serving policy")
    if args.complete:
        executions = [verify_execution(path, args.cohort) for path in (args.control, args.registration)]
        if executions[0]["work"] != executions[1]["work"]:
            raise ValueError("matched complete measured work changed")
    else:
        state = causal.load(args.registration.with_suffix(".state.json"))
        if state["status"] != "running_full" or any(
            not causal.reservation.alive(state.get(name)) for name in ("watcher", "server", "driver")
        ):
            raise ValueError("live audit requires actual serving owners")
    arm, reader = Path(plan["run_root"]) / "full", SealedRecords()
    result = inspect(
        reader(arm / "opportunities/admission_opportunities.jsonl"),
        reader(arm / "server/runtime_audit.jsonl"),
    )
    arrivals = Path(plan["workflow_arrivals_path"])
    phases = observation_phases(
        result, causal.load(arrivals), causal.load(args.cohort),
        reader(arm / "replay/runtime_events.replay.jsonl"),
    )
    origin = phases["origin_monotonic_ms"] / 1000.
    result["phase_counts"] = {
        name: {
            **totals([
                row for row in result["decisions"] if row["monotonic_s"] >= origin + phase["start_s"]
                and (phase["end_s"] is None or row["monotonic_s"] < origin + phase["end_s"])
            ]),
            "complete_phase_observed": phase["complete_phase_observed"],
        } for name, phase in phases["phases"].items()
    }
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        registration=str(args.registration.resolve()), input_jsonl_prefixes=reader.prefixes,
        complete_measured_execution_verified=args.complete,
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in (
            args.registration, args.control, args.cohort, arrivals, *configs, *artifacts, *sources,
            Path(__file__), ROOT / "scripts/audit_workflow_size_selection.py",
            ROOT / "scripts/audit_periodic_admission_decisions.py",
            ROOT / "scripts/audit_free_dispatch_service.py",
        )},
        GPU_action=False, serving_process_changed=False, performance_accepted=False,
    )
    write(args.output, result)
    print({"counts": result["counts"], "phase_counts": result["phase_counts"]})


if __name__ == "__main__":
    main()
