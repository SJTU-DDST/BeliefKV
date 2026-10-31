"""Evaluate local oracle choices on a sealed matched-control prefix."""

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
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact
from scripts import run_causal_comparison as causal
from scripts.audit_free_dispatch_service import identity
from scripts.audit_periodic_admission_decisions import (
    inspect as periodic_inspect, observation_phases,
)
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_size_selection import (
    EVENT, MATCHED_CONTROL_RANK, finite, inspect_choice,
)
from scripts.train_workflow_work import write


def opportunity(row):
    inspect_choice(row)
    if row.get("rank_kind") != MATCHED_CONTROL_RANK or (
        row["ordered"] and row.get("rank_before_category") is not True
    ):
        raise ValueError("require the explicit matched global control")
    metadata, ranks, candidates, workflow_work = {}, {}, [], {}
    buffer = ThunderActingDecayWorkingSet(1).buffer_tokens
    for item in row.get("candidates", ()):
        key = identity(item["key"], grant=True)
        if key[2] in metadata:
            raise ValueError("duplicate matched-control candidate")
        metadata[key[2]] = item
    for owner, category, control_rank, tokens, sequence, group in row["ordered"]:
        item = metadata.get(owner)
        prediction = item.get("prediction") if item is not None else None
        work = prediction.get("remaining_path_s") if isinstance(prediction, dict) else None
        if (
            prediction is None or prediction.get("evidence") != "oracle"
            or not finite(work) or work < 0 or control_rank not in (0., 1.)
            or item.get("bucket") != control_rank
        ):
            raise ValueError("matched control lacks its logged oracle work or constant decision rank")
        workflow = item["key"]["root_workflow_id"]
        if workflow in workflow_work and workflow_work[workflow] != work:
            raise ValueError("one control boundary has inconsistent cached workflow work")
        workflow_work[workflow] = work
        # Zero is the existing protected ready-JOIN rank in this exact control.
        ranks[owner] = 0. if control_rank == 0. else work + 1.
        candidates.append({
            "invocation_id": owner, "workflow_id": workflow,
            "context_tokens": tokens, "complete_budget_tokens": tokens + buffer,
            "control_rank": control_rank, "oracle_remaining_path_s": work,
            "oracle_rank": ranks[owner], "protected_join": control_rank == 0.,
        })
    if set(metadata) != {item[0] for item in row["ordered"]}:
        raise ValueError("matched control candidate metadata differs from the actual decision")
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
    if row["capacity_tokens"] > buffer:
        policy._resume_now = row["monotonic_s"]
        policy._resume()
        selected = list(policy.last_selection["selected_in_priority_order"])
        order = [item[0] for item in policy.last_selection["ordered"]]
    else:
        selected, order = [], []
    fitting = [
        item for item in candidates if item["complete_budget_tokens"] <= row["capacity_tokens"]
    ]
    competing = (
        len({item["workflow_id"] for item in fitting}) > 1
        and sum(item["complete_budget_tokens"] for item in fitting) > row["capacity_tokens"]
    )
    work = [item["oracle_remaining_path_s"] for item in fitting]
    return {
        "monotonic_s": row["monotonic_s"], "capacity_tokens": row["capacity_tokens"],
        "candidates": candidates,
        "control_selected": row["selected_in_priority_order"], "oracle_selected": selected,
        "order_changed": order != [item[0] for item in row["ordered"]],
        "selected_set_changed": set(selected) != set(row["selected_in_priority_order"]),
        "cross_workflow_individually_fitting_set_competition": competing,
        "fitting_remaining_work_ratio": max(work) / min(work) if work and min(work) > 0 else None,
        "zero_remaining_work_candidate": bool(work) and min(work) == 0.,
    }


def totals(decisions):
    counts = Counter()
    for row in decisions:
        counts["actual_control_decisions_replayed"] += 1
        counts["nonempty_candidate_decisions"] += bool(row["candidates"])
        counts["cross_workflow_set_competitions"] += row[
            "cross_workflow_individually_fitting_set_competition"
        ]
        counts["oracle_order_changes"] += row["order_changed"]
        counts["oracle_selected_set_changes"] += row["selected_set_changed"]
        counts["competitions_with_work_ratio_ge_2"] += (
            row["cross_workflow_individually_fitting_set_competition"]
            and row["fitting_remaining_work_ratio"] is not None
            and row["fitting_remaining_work_ratio"] >= 2.
        )
    return dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve prior local-oracle observations")
    plan = causal.load(args.registration)
    causal.comparison.verify_sources(plan)
    policy_sources = (
        "beliefkv/policy/workflow_size_resume.py", "beliefkv/policy/paper_baselines.py",
        "beliefkv/runtime/workflow_oracle_admission.py",
    )
    for name in policy_sources:
        if sha256_file(ROOT / name) != plan["source_sha256"].get(name):
            raise ValueError("local evaluation differs from the executed policy")
    common = plan["reference_environment"]
    artifact_path = Path(common["WORKFLOW_WORK_MODEL"])
    artifact = WorkflowWorkOracleArtifact.load(
        artifact_path, common["WORKFLOW_WORK_MODEL_SHA256"],
    )
    if artifact.raw.get("remaining_work_ranking_enabled") is not False:
        raise ValueError("require a declared disabled-ranking control")
    arm, reader = Path(plan["run_root"]) / "full", SealedRecords()
    journal = arm / "opportunities/admission_opportunities.jsonl"
    decisions, action_counts, latest_agents = [], Counter(), None
    observed_actions = {
        "agent_predicted_tool_replacement", "predicted_tool_kv_evicted",
        "agent_full_offload_paused", "workflow_remaining_pressure_pause",
        "agent_join_blocked_capacity_admitted", "agent_join_free_capacity_admitted",
        "agent_join_siblings_coadmitted", "agent_dispatch_free_capacity_admitted",
    }
    for row in reader(journal):
        event = row.get("event")
        if event == EVENT:
            decisions.append(opportunity(row))
        elif event == "admission_runtime_state":
            latest_agents = row.get("agent_admission")
        if event in observed_actions:
            action_counts[event] += 1
    arrivals_path = Path(plan["workflow_arrivals_path"])
    arrivals = causal.load(arrivals_path)
    if arrivals != plan["workflow_arrivals"]:
        raise ValueError("phase arrival table differs from the experiment")
    phases = observation_phases(
        periodic_inspect(reader(journal), ()), arrivals, causal.load(args.cohort),
        reader(arm / "replay/runtime_events.replay.jsonl"),
    )
    origin = phases["origin_monotonic_ms"] / 1000.
    phase_counts = {}
    for name, phase in phases["phases"].items():
        selected = [
            row for row in decisions if row["monotonic_s"] >= origin + phase["start_s"]
            and (phase["end_s"] is None or row["monotonic_s"] < origin + phase["end_s"])
        ]
        phase_counts[name] = {
            **totals(selected), "complete_phase_observed": phase["complete_phase_observed"],
        }
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(args.registration.resolve()),
        "registration_sha256": sha256_file(args.registration),
        "counts": totals(decisions), "phase_counts": phase_counts, "decisions": decisions,
        "observed_action_event_counts": dict(action_counts),
        "latest_recorded_admission_counts": (latest_agents or {}).get("counts"),
        "latest_cached_tool_retention_sources": dict(Counter(
            state["source"] for state in
            (latest_agents or {}).get("workflow_tool_pressure_last_states", {}).values()
        )),
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            args.registration, args.cohort, arrivals_path, artifact_path,
            Path(__file__), ROOT / "scripts/audit_workflow_size_selection.py",
            *(ROOT / name for name in policy_sources),
        )},
        "scope": {
            "periodic_resume_only": True,
            "actual_control_selection_reproduced": True,
            "same_observed_eligible_candidates_budget_buffer_and_join_groups": True,
            "protected_ready_join_preserved": True,
            "changed_post_resume_pressure_reconstructed": False,
            "physical_copy_or_allocation_feasibility_proven": False,
            "intervening_service_or_future_state_changes_simulated": False,
            "complete_event_admission_or_pressure_coverage": False,
            "JCT_or_throughput_counterfactual": False,
            "optimal_oracle_bound": False,
        },
        "GPU_action": False, "serving_process_changed": False, "performance_accepted": False,
    }
    write(args.output, result)
    print({"counts": result["counts"], "phase_counts": phase_counts})


if __name__ == "__main__":
    main()
