"""Check bounded native service order within actual multi-workflow admissions."""

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
from beliefkv.policy.workflow_combination import CombinationCosts
from scripts import run_causal_comparison as causal
from scripts.audit_free_dispatch_service import identity
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_combination_choices import inspect as inspect_choices
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def summarize(audit, choices, measured):
    observed = {row["journal_sequence"]: row for row in choices["decisions"]}
    if len(observed) != len(choices["decisions"]):
        raise ValueError("duplicate actual choice sequence")
    counts, rows, waits = Counter(), [], defaultdict(list)
    for labelled in audit["decisions"]:
        sequence = labelled["raw_journal_sequence"]
        choice = observed[sequence]
        selected = labelled["selections"]["cached"]["ratio_selected"]
        if (
            choice["monotonic_s"] != labelled["monotonic_s"]
            or list(choice["selected"]) != list(selected)
            or not choice["search_replayed"] or not choice["search_complete"]
        ):
            raise ValueError("labelled choice differs from the executed complete search")
        counts["complete_labelled_choices"] += 1
        candidates = {row["key"]["invocation_id"]: row for row in labelled["candidates"]}
        source = choice["source"]
        services = {
            row["invocation_id"]: row for row in choice[
                "all_periodic_service" if source == "periodic_decay_resume" else "all_free_service"
            ]
        }
        protected = set(labelled["required_JOIN_members"])
        protected_workflows = {candidates[owner]["key"]["root_workflow_id"] for owner in protected}
        groups = defaultdict(list)
        for owner in selected:
            candidate = candidates[owner]
            key, service = identity(candidate["key"], grant=True), services.get(owner)
            work = candidate["remaining_model_work"]
            if type(work) not in (float, int) or not math.isfinite(work) or work < 0:
                raise ValueError("remaining model work must be a finite complete-trace label")
            episode = service.get("episode") if service else None
            direct = bool(service) and (
                choice.get("following_periodic_grant_verified", False)
                if source == "periodic_decay_resume" else
                service["actual_grant_verified"] and not service["separate_tool_replacement"]
            )
            if service is not None and tuple(service["key"]) != key:
                raise ValueError("selected service belongs to another exact request attempt")
            retained = bool(episode) and episode.get(
                "selection_retained", episode.get("active_after_pressure"),
            ) is True
            bounded = direct and retained and episode["new_service_before_next_control_or_intervention"] is True
            wait = (
                episode["selection_ms"] + episode["first_new_useful_service_ms"]
                - choice["monotonic_s"] * 1000. if bounded else None
            )
            if wait is not None and (not math.isfinite(wait) or wait < 0):
                raise ValueError("new service precedes its actual selection")
            kind = "new_to_reference" if owner not in labelled["reference"] else "retained_reference"
            counts[kind + "_selected_members"] += 1
            counts[kind + "_bounded_served_members"] += bounded
            if wait is not None:
                waits[kind].append(wait)
            workflow = candidate["key"]["root_workflow_id"]
            if workflow not in protected_workflows:
                groups[workflow].append({
                    "key": key, "remaining_model_work": work,
                    "direct_grant": direct, "retained": retained, "bounded_wait_ms": wait,
                })
        if len(groups) < 2:
            continue
        counts["multi_workflow_unprotected_selected_windows"] += 1
        workflows = []
        for workflow, members in groups.items():
            values = [member["remaining_model_work"] for member in members]
            if max(values) - min(values) > 1e-6:
                raise ValueError("one workflow has conflicting remaining-work labels")
            served = [member["bounded_wait_ms"] for member in members if member["bounded_wait_ms"] is not None]
            workflows.append({
                "workflow_id": workflow, "remaining_model_work": min(values),
                "selected_agents": len(members), "bounded_served_agents": len(served),
                "first_bounded_wait_ms": min(served) if served else None,
                "measured": workflow in measured, "members": members,
            })
        complete = all(row["first_bounded_wait_ms"] is not None for row in workflows)
        counts["multi_workflow_windows_with_complete_bounded_service"] += complete
        counts["multi_workflow_windows_with_unknown_bounded_service"] += not complete
        delay, spread = None, None
        if complete:
            first = min(row["first_bounded_wait_ms"] for row in workflows)
            shortest = min(row["remaining_model_work"] for row in workflows)
            delay = min(
                row["first_bounded_wait_ms"] for row in workflows
                if row["remaining_model_work"] <= shortest + 1e-6
            ) - first
            spread = max(row["first_bounded_wait_ms"] for row in workflows) - first
            counts["shortest_selected_workflow_in_first_wave"] += delay == 0.
            counts["shortest_selected_workflow_served_later"] += delay > 0.
        rows.append({
            "raw_journal_sequence": sequence, "source": source,
            "touches_measured": any(row["measured"] for row in workflows),
            "all_workflows_bounded_served": complete, "workflows": workflows,
            "shortest_delay_after_first_ms": delay, "first_service_spread_ms": spread,
        })
    return {
        "counts": dict(counts), "decisions": rows,
        "selected_member_wait_ms": {name: distribution(values) for name, values in waits.items()},
        "shortest_selected_workflow_delay_ms": distribution(
            row["shortest_delay_after_first_ms"] for row in rows
            if row["shortest_delay_after_first_ms"] is not None
        ),
        "scope": {
            "whole_complete_run_diagnostic_not_common_window_JCT": True,
            "only_actual_selected_members_compared": True,
            "protected_JOIN_workflows_excluded_from_size_comparison": True,
            "one_first_service_per_workflow_per_decision": True,
            "exact_grant_attempt_session_and_positive_bounded_progress_required": True,
            "separate_tool_replacement_not_counted_as_combination_service": True,
            "missing_or_out_of_window_service_is_unknown": True,
            "remaining_model_work_is_offline_label_not_online_input": True,
            "worker_service_clock_not_kernel_start_or_streaming_TTFT": True,
            "observed_wait_is_not_avoidable_JCT_or_counterfactual_gain": True,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "analysis", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("use hidden CUDA, nice19 and a fresh read-only evidence output")
    plan, audit = causal.load(args.registration), causal.load(args.analysis)
    causal.comparison.verify_sources(plan)
    policy = ROOT / "beliefkv/policy/workflow_combination.py"
    if sha256_file(policy) != plan["source_sha256"]["beliefkv/policy/workflow_combination.py"]:
        raise ValueError("run this reader with the executed ratio policy, not a newer selector")
    execution = verify_execution(args.registration, args.cohort)
    if (
        audit.get("complete_measured_execution_verified") is not True
        or audit["measured_requests"] != execution["measured_requests"]
        or audit["measured_output_tokens"] != execution["measured_output_tokens"]
    ):
        raise ValueError("require the original complete measured population")
    configuration = Path(plan["experiment_configuration"])
    spec = causal.load(configuration)
    artifact = Path(spec["common_environment"]["WORKFLOW_WORK_MODEL"])
    if sha256_file(artifact) != spec["common_environment"]["WORKFLOW_WORK_MODEL_SHA256"]:
        raise ValueError("the actual prediction artifact changed")
    profile = causal.load(artifact)["admission_cost_profile"]
    reader, arm = SealedRecords(), Path(plan["run_root"]) / "full"
    records = list(reader(arm / "opportunities/admission_opportunities.jsonl"))
    choices = inspect_choices(
        records, reader(arm / "server/runtime_audit.jsonl"),
        CombinationCosts(**profile["costs"]), profile["identity"],
        allow_feasible_resource_alternatives=True, plan_device_capacity=True,
        include_all_selected_service=True,
    )
    for path, prefix in reader.prefixes.items():
        if prefix != audit["input_jsonl_prefixes"].get(path):
            raise ValueError("service evidence differs from the sealed work-label analysis")
    result = summarize(audit, choices, set(causal.load(args.cohort)["measured_workflow_ids"]))
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        registration=str(args.registration), actual_choice_audit_counts=choices["counts"],
        complete_measured_execution_verified=True, input_jsonl_prefixes=reader.prefixes,
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in (
            args.registration, args.analysis, args.cohort, configuration, artifact, policy, Path(__file__),
            ROOT / "scripts/audit_workflow_combination_choices.py",
            ROOT / "scripts/audit_periodic_admission_decisions.py",
            ROOT / "scripts/audit_free_dispatch_service.py",
        )},
        GPU_action=False, performance_accepted=False,
    )
    write(args.output, result)
    print(json.dumps({"counts": result["counts"], "shortest_delay": result["shortest_selected_workflow_delay_ms"]}))


if __name__ == "__main__":
    main()
