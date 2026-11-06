"""Compare oracle rank definitions at the same recorded admission boundaries."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.paper_baselines import ThunderActingDecayWorkingSet, ThunderProgram
from beliefkv.policy.workflow_size_resume import WorkflowSizeWorkingSet
from scripts.audit_periodic_admission_decisions import observation_phases
from scripts.audit_ranked_oracle_service import matched_reference
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_size_selection import EVENT, finite
from scripts.train_workflow_work import write


OBJECTIVES = {
    "model_tool_path": "remaining_path_s",
    "model_only_path": "model_only_path_s",
    "total_model_work": "remaining_work",
}


def select(row, scores):
    groups = {item[0]: item[5][2] for item in row["ordered"] if item[5] is not None}
    policy = WorkflowSizeWorkingSet(
        max(1, row["capacity_tokens"]), rank=lambda owner, now: scores[owner],
        sibling_group=groups.get, rank_before_category=True, observe_ready=lambda item: True,
    )
    policy.programs = {
        owner: ThunderProgram(
            owner, tokens, steps=1 if category == 1 else 2,
            acting=category == 2, queue_sequence=sequence,
        ) for owner, category, _rank, tokens, sequence, _group in row["ordered"]
    }
    if row["capacity_tokens"] <= policy.buffer_tokens:
        return [], []
    policy._resume_now = row["monotonic_s"]
    policy._resume()
    return (
        list(policy.last_selection["selected_in_priority_order"]),
        [item[0] for item in policy.last_selection["ordered"]],
    )


def compare(row, measured):
    reference = matched_reference(row)
    metadata = {item["key"]["invocation_id"]: item for item in row.get("candidates", ())}
    workflows, protected, scores, observed = {}, set(), {}, {}
    candidates = []
    buffer = ThunderActingDecayWorkingSet(1).buffer_tokens
    for owner, category, rank, tokens, sequence, group in row["ordered"]:
        item = metadata[owner]
        workflow = item["key"]["root_workflow_id"]
        prediction = item["prediction"]
        values = {name: prediction.get(field) for name, field in OBJECTIVES.items()}
        if any(not finite(value) or value < 0 for value in values.values()):
            raise ValueError("rank comparison requires all nonnegative oracle work definitions")
        if workflow in observed and observed[workflow] != values:
            raise ValueError("one boundary has inconsistent workflow work definitions")
        observed[workflow] = values
        workflows[owner] = workflow
        if rank == 0.:
            protected.add(owner)
        scores[owner] = values
        candidates.append({
            "key": item["key"], "category": category, "complete_budget_tokens": tokens + buffer,
            "sequence": sequence, "protected_join": rank == 0., "sibling_group": group,
            "scores": values,
        })
    actual = row["selected_in_priority_order"]
    fitting = [
        item for item in candidates if item["complete_budget_tokens"] <= row["capacity_tokens"]
    ]
    competition = (
        len({item["key"]["root_workflow_id"] for item in fitting}) > 1
        and sum(item["complete_budget_tokens"] for item in fitting) > row["capacity_tokens"]
    )
    candidate_class = (
        "no_recorded_candidates" if not candidates else
        "none_individually_fit" if not fitting else
        "one_fitting_workflow" if len({item["key"]["root_workflow_id"] for item in fitting}) == 1 else
        "multiple_fitting_workflows_compete" if competition else "multiple_fitting_workflows_all_fit"
    )
    choices = {}
    for objective in OBJECTIVES:
        selected, ordered = select(row, {
            owner: 0. if owner in protected else values[objective] + 1.
            for owner, values in scores.items()
        })
        if objective == "model_tool_path" and (
            selected != actual or ordered != [item[0] for item in row["ordered"]]
        ):
            raise ValueError("actual oracle choice does not reproduce its recorded objective")
        difference = set(selected) ^ set(actual)
        choices[objective] = {
            "selected": selected,
            "order_changed_from_actual": ordered != [item[0] for item in row["ordered"]],
            "set_changed_from_actual": bool(difference),
            "set_changed_from_reference": set(selected) != set(reference["matched_control_selected"]),
            "added": [owner for owner in selected if owner not in actual],
            "removed": [owner for owner in actual if owner not in selected],
            "affected_measured_workflows": sorted({
                workflows[owner] for owner in difference if workflows[owner] in measured
            }),
            "selected_tokens": sum(
                item["complete_budget_tokens"]
                for item in candidates if item["key"]["invocation_id"] in selected
            ),
        }
    return {
        "monotonic_s": row["monotonic_s"], "capacity_tokens": row["capacity_tokens"],
        "actual_selected": actual, "reference_selected": reference["matched_control_selected"],
        "logical_cross_workflow_competition": competition, "candidate_class": candidate_class,
        "protected_join_present": bool(protected),
        "candidates": candidates, "objectives": choices,
    }


def totals(decisions):
    counts = Counter({
        "controls": len(decisions),
        "nonempty": sum(bool(row["candidates"]) for row in decisions),
        "logical_competitions": sum(row["logical_cross_workflow_competition"] for row in decisions),
    })
    classes = Counter(row["candidate_class"] for row in decisions)
    objectives = {}
    for name in OBJECTIVES:
        values = Counter({label: 0 for label in (
            "order_changes", "set_changes", "set_changes_from_reference",
            "smaller_sets", "larger_sets", "same_count_changed_sets",
            "added_agent_episodes", "removed_agent_episodes", "measurement_affecting_changes",
            "unprotected_competitive_set_changes",
        )})
        measured = set()
        for row in decisions:
            choice = row["objectives"][name]
            changed = choice["set_changed_from_actual"]
            values["order_changes"] += choice["order_changed_from_actual"]
            values["set_changes"] += changed
            values["set_changes_from_reference"] += choice["set_changed_from_reference"]
            values["smaller_sets"] += changed and len(choice["selected"]) < len(row["actual_selected"])
            values["larger_sets"] += changed and len(choice["selected"]) > len(row["actual_selected"])
            values["same_count_changed_sets"] += changed and len(choice["selected"]) == len(row["actual_selected"])
            values["added_agent_episodes"] += len(choice["added"])
            values["removed_agent_episodes"] += len(choice["removed"])
            values["measurement_affecting_changes"] += bool(choice["affected_measured_workflows"])
            values["unprotected_competitive_set_changes"] += (
                changed and row["logical_cross_workflow_competition"] and not row["protected_join_present"]
            )
            measured.update(choice["affected_measured_workflows"])
        objectives[name] = {**values, "distinct_affected_measured_workflows": len(measured)}
    return {"counts": dict(counts), "candidate_classes": dict(classes), "objectives": objectives}


def bound_original_inputs(report, execution_checkout):
    resolved = {}
    for name, digest in report["inputs_sha256"].items():
        original = Path(name)
        paths = [original]
        if original.is_relative_to(ROOT) and original.relative_to(ROOT).parts[0] in ("beliefkv", "scripts"):
            paths.append(execution_checkout / original.relative_to(ROOT))
        match = next((path for path in paths if path.is_file() and sha256_file(path) == digest), None)
        if match is None:
            raise ValueError(f"original evidence input is unavailable or changed: {name}")
        resolved[name] = {"verified_path": str(match), "sha256": digest}
    return resolved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve previous evidence and use nice19 CPU-only execution")
    report = json.loads(args.service_analysis.read_bytes())
    if report.get("complete_measured_execution_verified") is not True:
        raise ValueError("require the completed original actual-service audit")
    registration = Path(report["registration"])
    plan = json.loads(registration.read_bytes())
    resolved = bound_original_inputs(report, Path(plan["execution_checkout"]))
    for name in ("beliefkv/policy/workflow_size_resume.py", "beliefkv/policy/paper_baselines.py"):
        if sha256_file(ROOT / name) != plan["source_sha256"].get(name):
            raise ValueError("local selection core differs from the original serving policy")
    cohort_path = next(Path(name) for name in resolved if Path(name).name == "analysis_cohort.json")
    cohort = json.loads(cohort_path.read_bytes())
    measured = set(cohort["measured_workflow_ids"])
    reader = SealedRecords()
    for name, prefix in report["input_jsonl_prefixes"].items():
        if name.endswith(("/opportunities/admission_opportunities.jsonl", "/replay/runtime_events.replay.jsonl")):
            reader.prefixes[name] = prefix
    opportunities = next(Path(name) for name in reader.prefixes if "/opportunities/" in name)
    events = next(Path(name) for name in reader.prefixes if "/replay/" in name)
    decisions = [
        compare(row, measured) for row in reader(opportunities) if row.get("event") == EVENT
    ]
    if len(decisions) != report["counts"]["actual_oracle_selections_replayed"]:
        raise ValueError("rank comparison missed an original periodic selection")
    if len(decisions) != len(report["decisions"]) or any(
        row["monotonic_s"] != previous["monotonic_s"]
        or row["actual_selected"] != previous["oracle_selected"]
        or row["reference_selected"] != previous["matched_control_selected"]
        or row["capacity_tokens"] != previous["capacity_tokens"]
        for row, previous in zip(decisions, report["decisions"])
    ):
        raise ValueError("recorded boundary differs from the original exact service analysis")
    if sum(row["objectives"]["model_tool_path"]["set_changed_from_reference"] for row in decisions) != (
        report["counts"]["selected_set_changes"]
    ):
        raise ValueError("same-state nonranking reference differs from the original service audit")
    phases = observation_phases(
        report, json.loads(Path(plan["workflow_arrivals_path"]).read_bytes()), cohort, reader(events),
    )
    origin = phases["origin_monotonic_ms"] / 1000.
    phase_counts = {
        name: totals([
            row for row in decisions if origin + phase["start_s"] <= row["monotonic_s"]
            and (phase["end_s"] is None or row["monotonic_s"] < origin + phase["end_s"])
        ]) for name, phase in phases["phases"].items()
    }
    if any(
        phase_counts[name]["counts"]["controls"] != prior["actual_oracle_selections_replayed"]
        for name, prior in report["phase_counts"].items()
    ):
        raise ValueError("fixed observation phases differ from the original service audit")
    result = {
        **totals(decisions), "decisions": decisions,
        "phase_counts": phase_counts,
        "phase_boundaries": phases, "original_inputs_verified": resolved,
        "original_service_analysis": str(args.service_analysis.resolve()),
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            args.service_analysis, Path(__file__), ROOT / "beliefkv/policy/workflow_size_resume.py",
            ROOT / "beliefkv/policy/paper_baselines.py", ROOT / "scripts/audit_ranked_oracle_service.py",
            ROOT / "scripts/audit_workflow_size_selection.py", ROOT / "scripts/audit_periodic_admission_decisions.py",
        )},
        "scope": {
            "same_observed_candidates_budgets_groups_and_protected_JOIN_rank": True,
            "empty_recorded_candidate_rows_can_include_budget_fast_return": True,
            "counterfactual_is_prepressure_logical_selection_only": True,
            "other_admission_and_future_execution_not_simulated": True,
            "model_only_and_external_only_max_paths_must_not_be_added": True,
            "model_work_and_seconds_differ_in_units_and_aggregation": True,
            "measurement_membership_private_analysis_only": True,
            "alternative_physical_fit_and_service_not_proven": True,
            "no_counterfactual_JCT_or_throughput_estimate": True,
            "historical_native_service_uses_original_verified_report_not_new_GPU_evidence": True,
        },
        "GPU_action": False, "performance_accepted": False,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    write(args.output, result)
    print(json.dumps({key: result[key] for key in (
        "counts", "candidate_classes", "objectives", "phase_counts",
    )}))


if __name__ == "__main__":
    main()
