"""Compare a logged periodic candidate window with its exact ready-paused population."""

import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact
from scripts import run_causal_comparison as causal
from scripts.audit_free_dispatch_service import identity
from scripts.audit_learned_combination_prediction import planning_costs, verify_selection_policy
from scripts.audit_live_work_prediction import audit, observation_timeline
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_combination_choices import EVENT, replay_choice
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution

PERIODIC = "agent_periodic_admission_decision"
RESUME = "workflow_size_resume_selection"


def pair_periodic(records):
    """Associate synchronous records, rejecting missing or foreign control boundaries."""
    pending, control, paired = None, None, []
    counts = Counter()
    for sequence, row in enumerate(records):
        event = row.get("event")
        if event == EVENT and row.get("source") == "periodic_decay_resume":
            if pending is not None:
                raise ValueError("another periodic selection arrived before its control and resume records")
            pending, control = (sequence, row), None
            counts["periodic_combination_entries"] += 1
        elif event == PERIODIC and pending is not None:
            if control is not None:
                raise ValueError("periodic selection has multiple control records")
            control = sequence, row
        elif event == RESUME and pending is not None:
            if control is None:
                raise ValueError("periodic selection lacks its actual control record")
            choice_sequence, choice = pending
            control_sequence, observed = control
            capacity = choice["logical_reference_capacity_tokens"]
            selections = (
                choice["decision"]["selected"], observed["resumed_program_ids"],
                row["selected_in_priority_order"],
            )
            if (
                not choice_sequence < control_sequence < sequence
                or type(capacity) is not int
                or any(len(value) != len(set(value)) for value in selections)
                or observed["monotonic_s"] != row["monotonic_s"]
                or capacity != observed["resume_capacity_tokens"]
                or capacity != row["capacity_tokens"]
                or set(choice["decision"]["selected"]) != set(observed["resumed_program_ids"])
                or set(choice["decision"]["selected"]) != set(row["selected_in_priority_order"])
            ):
                raise ValueError("periodic window belongs to another capacity or selection boundary")
            programs = {}
            for item in observed["programs"]:
                owner = item["invocation_id"]
                if owner in programs:
                    raise ValueError("periodic control repeats a program")
                programs[owner] = item
            keys = {}
            for item in row["candidates"]:
                key = item["key"]
                owner = identity(key, grant=True)[2]
                if owner in keys:
                    raise ValueError("resume metadata repeats a program")
                keys[owner] = key
            readiness = row.get("logical_ready_paused_before_resume")
            if not isinstance(readiness, dict):
                counts["missing_exact_ready_map"] += 1
                pending, control = None, None
                continue
            ready, seen = [], set()
            for item in row["ordered"]:
                owner, _category, _rank, tokens, _queue_sequence, _group = item
                if owner in seen or owner not in readiness or type(readiness[owner]) is not bool:
                    raise ValueError("resume population has duplicate or unknown ready state")
                seen.add(owner)
                if not readiness[owner]:
                    continue
                if owner not in keys or owner not in programs:
                    raise ValueError("ready resume member lacks its exact control and request")
                before = programs[owner]
                if identity(keys[owner], grant=True) != identity(before["key"], grant=True):
                    raise ValueError("periodic member changed request, attempt or session")
                if before["complete_budget_tokens"] != tokens + 100:
                    raise ValueError("periodic member changed its undiscounted budget")
                if any(type(before.get(name)) is not bool for name in (
                    "active_before", "pending_before", "marked_before", "application_ready",
                )):
                    raise ValueError("periodic member has unknown before-control state")
                if (
                    before["active_before"] or not before["pending_before"]
                    or before["marked_before"] or not before["application_ready"]
                ):
                    counts["ready_map_members_without_ready_paused_before_state"] += 1
                    continue
                ready.append({"key": keys[owner], "logical_tokens": tokens + 100})
            for candidate in choice.get("candidates", ()):
                owner = candidate["identity"]
                if owner not in keys or identity(choice["keys"][owner], grant=True) != identity(
                    keys[owner], grant=True,
                ):
                    raise ValueError("native window metadata changed the selected request identity")
                if owner not in programs or candidate["logical_tokens"] != programs[owner][
                    "complete_budget_tokens"
                ]:
                    raise ValueError("native window changed its undiscounted periodic budget")
            paired.append({
                "combination": choice, "ready": ready, "monotonic_s": choice["monotonic_s"],
                "combination_sequence": choice_sequence, "control_sequence": control_sequence,
                "resume_sequence": sequence,
            })
            counts["exact_periodic_windows_paired"] += 1
            pending, control = None, None
    if pending is not None:
        raise ValueError("sealed history ends before the periodic window's actual control boundary")
    return paired, dict(counts)


def inspect(pairs, observations, costs, cost_identity, *, measured=()):
    if len(pairs) != len(observations):
        raise ValueError("each periodic window requires its exact observed progress")
    counts, cases = Counter(), []
    measured = set(measured)
    for pair, observation in zip(pairs, observations):
        row = pair["combination"]
        replay = replay_choice(
            row, costs, cost_identity, allow_feasible_resource_alternatives=True,
            plan_device_capacity=True,
        )
        counts["periodic_windows"] += 1
        if not replay["search_replayed"]:
            counts["no_replayed_native_window"] += 1
            continue
        if not row["decision"]["search_complete"]:
            counts["incomplete_searches_excluded"] += 1
            continue
        if row.get("unavailable_summaries"):
            counts["incomplete_native_window_summaries_excluded"] += 1
            continue
        if observation["monotonic_s"] != pair["monotonic_s"]:
            raise ValueError("periodic work labels belong to another decision")
        progress = {}
        for item in observation["candidates"]:
            owner = identity(item["key"], grant=True)[2]
            if owner in progress:
                raise ValueError("progress repeats a periodic candidate")
            value = item.get("observed_remaining_model_work")
            if value is None:
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or value < -1e-6:
                raise ValueError("periodic work label is not finite nonnegative remaining work")
            progress[owner] = item
        ready = {item["key"]["invocation_id"]: item for item in pair["ready"]}
        if any(owner not in progress for owner in ready):
            counts["missing_observed_progress"] += 1
            continue
        for owner, item in ready.items():
            if identity(item["key"], grant=True) != identity(progress[owner]["key"], grant=True):
                raise ValueError("progress changed a periodic request identity")
        window = {item["identity"] for item in row["candidates"]}
        if not window <= ready.keys():
            counts["native_window_not_fully_ready_paused_before_control"] += 1
            continue
        required = set(row["decision"]["required_reference_members"])
        if not required <= window:
            raise ValueError("protected JOIN members are absent from the native candidate window")
        required_tokens = sum(ready[owner]["logical_tokens"] for owner in required)
        protected_workflows = {ready[owner]["key"]["root_workflow_id"] for owner in required}
        capacity = row["logical_reference_capacity_tokens"] - required_tokens
        fitting = [
            item for owner, item in ready.items()
            if owner not in required and item["logical_tokens"] <= capacity
            and item["key"]["root_workflow_id"] not in protected_workflows
        ]
        inside = [item for item in fitting if item["key"]["invocation_id"] in window]
        outside = [item for item in fitting if item["key"]["invocation_id"] not in window]
        counts["complete_periodic_searches_with_all_native_summaries"] += 1
        counts["searches_with_individually_logical_fitting_outside_candidates"] += bool(outside)
        if not inside or not outside:
            continue
        inside_workflows = {item["key"]["root_workflow_id"] for item in inside}
        outside = [item for item in outside if item["key"]["root_workflow_id"] not in inside_workflows]
        if not outside:
            continue

        def remaining(item):
            return max(0., progress[item["key"]["invocation_id"]]["observed_remaining_model_work"])

        best_inside, best_outside = min(inside, key=remaining), min(outside, key=remaining)
        a, b = remaining(best_inside), remaining(best_outside)
        if b + 1e-9 >= a:
            continue
        counts["outside_other_workflow_shorter_than_every_fitting_window_workflow"] += 1
        counts["outside_shorter_by_at_least_two"] += a >= 2 * max(b, 1e-9)
        counts["outside_shorter_workflow_is_measured"] += (
            best_outside["key"]["root_workflow_id"] in measured
        )
        cases.append({
            "monotonic_s": pair["monotonic_s"], "journal_sequence": pair["combination_sequence"],
            "inside_minimum": {"key": best_inside["key"], "remaining_model_work": a},
            "outside_minimum": {
                "key": best_outside["key"], "remaining_model_work": b,
                "logical_tokens": best_outside["logical_tokens"],
            },
            "window_agent_count": len(window), "ready_agent_count": len(ready),
            "required_JOIN_members": sorted(required),
            "ordinary_capacity_after_protected_JOIN_tokens": capacity,
        })
    return {"counts": dict(counts), "outside_shorter_cases": cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "cohort", "labels", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--selection-core-parity", type=Path)
    args = parser.parse_args()
    if args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19 or os.sched_getaffinity(0) != {0}
    ):
        raise ValueError("preserve evidence and use CPU0, nice19, hidden CUDA")
    registration, cohort, labels = (causal.load(path) for path in (
        args.registration, args.cohort, args.labels,
    ))
    causal.comparison.verify_sources(registration)
    verify_execution(args.registration, args.cohort)
    artifact = WorkflowWorkOracleArtifact(labels)
    if (
        set(artifact.workflows) != set(registration["workflow_ids"])
        or Path(labels["source_binding"]["source_arm"]).resolve()
        != Path(registration["source_arm"]).resolve()
        or labels["source_binding"]["token_sha256"] != registration["token_source_sha256"]
    ):
        raise ValueError("periodic labels differ from the exact completed traffic")
    policy = ROOT / "beliefkv/policy/workflow_combination.py"
    verify_selection_policy(registration, policy, args.selection_core_parity)
    config_path = Path(registration["experiment_configuration"])
    model_path = Path(causal.load(config_path)["common_environment"]["WORKFLOW_WORK_MODEL"])
    profile = causal.load(model_path)["admission_cost_profile"]
    paths = [
        args.registration, args.cohort, args.labels, config_path, model_path, policy,
        Path(__file__), ROOT / "scripts/audit_live_work_prediction.py",
        ROOT / "scripts/audit_workflow_combination_choices.py",
    ]
    if args.selection_core_parity is not None:
        paths.extend((
            args.selection_core_parity,
            Path(registration["execution_checkout"]) / "beliefkv/policy/workflow_combination.py",
        ))
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    reader, arm = SealedRecords(), Path(registration["run_root"]) / "full"
    pairs, pairing = pair_periodic(reader(arm / "opportunities/admission_opportunities.jsonl"))
    observations = [{
        "monotonic_s": pair["monotonic_s"],
        "candidates": [{"key": item["key"]} for item in pair["ready"]],
    } for pair in pairs]
    timeline = observation_timeline(
        list(reader(arm / "replay/runtime_events.replay.jsonl")),
        (row for row in reader(arm / "server/runtime_audit.jsonl") if row.get("event") == "gpu_service_sample"),
        artifact.requests,
    )
    audit(
        observations, timeline, artifact.requests,
        {key: value["total_work_units"] / 100. for key, value in artifact.workflows.items()}, {},
    )
    result = inspect(
        pairs, observations, planning_costs(profile), profile["identity"],
        measured=cohort["measured_workflow_ids"],
    )
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("an immutable periodic diagnostic input changed")
    result.update(
        pairing_counts=pairing, input_jsonl_prefixes=reader.prefixes, inputs_sha256=pins,
        GPU_action=False,
        scope={
            "completed_fixed_cohort_and_original_request_work_verified": True,
            "exact_combination_control_resume_and_request_session_association": True,
            "only_complete_searches_with_all_native_window_summaries": True,
            "outside_native_prefix_and_physical_fit_unknown": True,
            "protected_JOIN_rights_and_their_logical_budget_preserved": True,
            "logical_individual_fit_is_not_admission_permission": True,
            "future_remaining_model_work_is_offline_label_only": True,
            "whole_run_JCT_counterfactual_proven": False,
            "existing_GPU_work_or_arrivals_changed": False,
        },
    )
    write(args.output, result)
    print(json.dumps({"pairing": pairing, "counts": result["counts"], "GPU_action": False}), flush=True)


if __name__ == "__main__":
    main()
