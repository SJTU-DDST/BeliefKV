"""Compare completion-path ordering on sealed R5 candidates without simulating JCT."""

import argparse
from collections import Counter, defaultdict
from dataclasses import replace
from itertools import combinations
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import CombinationCosts, CombinationResources, _evaluate
from beliefkv.policy.workflow_shortest_frontier import choose_shortest_work_first
from beliefkv.predictor.workflow_continuous_work import log_work_weight, utility_reference_work
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from beliefkv.predictor.workflow_work_distribution import support_status
from scripts.audit_free_dispatch_service import identity
from scripts.audit_r5_shortest_contract import candidate, inspect_choice
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


def inspect(row, costs, mappings):
    candidates = [candidate(value) for value in row["candidates"]]
    by_id = {value.identity: value for value in candidates}
    selected = set(row["decision"]["selected"])
    required_order = row["decision"].get("required_reference_members", ())
    required = set(required_order)
    result = {
        "monotonic_s": row["monotonic_s"], "source": row["source"],
        "actual": sorted(selected), "reason": row["decision"]["reason"],
        "workflows": sorted({value.workflow for value in candidates}),
    }
    if (
        row["resources"] is None or not selected <= by_id.keys()
        or not required <= selected
    ):
        return {**result, "metadata_complete": False}
    resources = CombinationResources(**row["resources"])
    forced = [by_id[identity] for identity in required_order]
    feasible = [
        value for value in candidates
        if value.identity not in required and _evaluate([*forced, value], resources, costs) is not None
    ]
    workflows = {value.workflow for value in feasible}
    result.update(
        metadata_complete=True,
        feasible_workflows=sorted(workflows),
        all_fit=_evaluate(candidates, resources, costs) is not None,
        capacity_competition=(
            len(workflows) > 1 and _evaluate([*forced, *feasible], resources, costs) is None
        ),
    )
    choices = {}
    for name, weights in mappings.items():
        decision = choose_shortest_work_first(
            [replace(value, weight=weights[value.workflow]) for value in candidates],
            row["decision"].get("reference", ()), resources, costs,
            required=required_order, strict_remaining_work=True, clock=lambda: 0,
            preserve_reference_service_capacity=row.get("preserve_reference_service_capacity", False),
        )
        choices[name] = {
            "selected": list(decision["selected"]), "complete": decision["search_complete"],
            "reason": decision["reason"],
        }
        if "ranked_selected" in decision:
            choices[name]["ranked_selected"] = list(decision["ranked_selected"])
    result["choices"] = choices
    truth = mappings["true_model_tool_path"]
    inversions = []
    for short in feasible:
        if short.identity in selected:
            continue
        anchors = [
            value for value in candidates if value.identity in selected and (
                value.identity in required or truth[value.workflow] >= truth[short.workflow]
            )
        ]
        if _evaluate([*anchors, short], resources, costs) is None:
            continue
        for long in candidates:
            if (
                long.identity in selected - required and long.workflow != short.workflow
                and truth[short.workflow] > truth[long.workflow]
            ):
                inversions.append([short.identity, long.identity])
    result["feasible_true_path_priority_inversions"] = inversions
    representatives = {}
    for value in feasible:
        representatives.setdefault(value.workflow, value)
    pairs = []
    for left, right in combinations(sorted(workflows), 2):
        correct = truth[left] - truth[right]
        if correct == 0:
            continue
        pairs.append({
            "workflows": [left, right],
            "cannot_coexist": _evaluate(
                [*forced, representatives[left], representatives[right]], resources, costs,
            ) is None,
            "order": {
                name: (
                    "tie" if weights[left] == weights[right] else
                    "correct" if (weights[left] - weights[right]) * correct > 0 else "wrong"
                ) for name, weights in mappings.items()
            },
        })
    result["pairs"] = pairs
    return result


def totals(rows, measured):
    counts, pairs = Counter({"boundaries": len(rows)}), defaultdict(Counter)
    for row in rows:
        counts["metadata_complete"] += row["metadata_complete"]
        if not row["metadata_complete"]:
            continue
        competition = row["capacity_competition"]
        counts["all_candidates_fit"] += row["all_fit"]
        counts["capacity_competition"] += competition
        counts["measured_in_capacity_competition"] += (
            competition and bool(set(row["feasible_workflows"]) & measured)
        )
        counts["feasible_true_path_priority_inversion_boundaries"] += bool(
            row["feasible_true_path_priority_inversions"]
        )
        counts["feasible_true_path_priority_inversion_pairs"] += len(
            row["feasible_true_path_priority_inversions"]
        )
        for name, choice in row["choices"].items():
            changed = set(choice["selected"]) != set(row["actual"])
            counts[name + "_set_changes"] += changed
            counts[name + "_competitive_set_changes"] += competition and changed
        truth = set(row["choices"]["true_model_tool_path"]["selected"])
        for name in (
            "current_weight", "point_model_work", "reforecast_point_model_work",
            "true_model_path", "predicted_model_tool_path",
        ):
            differs = set(row["choices"][name]["selected"]) != truth
            counts[name + "_vs_true_path_set_differences"] += differs
            counts[name + "_vs_true_path_competitive_set_differences"] += competition and differs
        for pair in row["pairs"] if competition else ():
            involves_measured = bool(set(pair["workflows"]) & measured)
            for name, order in pair["order"].items():
                pairs[name][order] += 1
                if involves_measured:
                    pairs[name]["measured_" + order] += 1
                if pair["cannot_coexist"]:
                    pairs[name]["cannot_coexist_" + order] += 1
    return {"counts": dict(counts), "competitive_distinct_workflow_pairs": dict(pairs)}


def clocks(dataset):
    reader = SealedRecords()
    source = next(
        path for path in dataset["input_jsonl_prefixes"]
        if path.endswith("/replay/runtime_events.replay.jsonl")
    )
    reader.prefixes[source] = dataset["input_jsonl_prefixes"][source]
    started, ended = {}, {}
    for event in reader(Path(source)):
        destination = (
            started if event["kind"] == "workflow_start" else
            ended if event["kind"] == "workflow_end" else None
        )
        if destination is not None:
            destination[event["workflow_id"]] = event["ts_ms"] / 1000.
    measured = dataset["cohort"]["measured_workflow_ids"]
    origin = min(started.values())
    common = [origin + offset for offset in dataset["cohort"]["throughput_window_offset_seconds"]]
    lifetime = [min(started[wf] for wf in measured), max(ended[wf] for wf in measured)]
    return common, lifetime, reader.prefixes


def logical_unpriced_candidates(row):
    window = row.get("logical_reference_window")
    if window is None:
        window = row.get("periodic_ready_window")
    if window is None:
        return None
    budgets = {
        record["key"]["invocation_id"]: record["logical_tokens"] for record in window
    }
    required = set(row["decision"].get("required_reference_members", ()))
    if not required <= budgets.keys():
        return None
    priced = {record["identity"] for record in row["candidates"]}
    attempted = {record["key"]["invocation_id"] for record in row.get("unavailable_summaries", ())}
    available = row["logical_reference_capacity_tokens"] - sum(budgets[key] for key in required)
    return sorted(
        key for key, tokens in budgets.items()
        if key not in priced | attempted | required and tokens <= available
    )


def audit_recorded_choice(row, costs, reference_s):
    decision = row["decision"]
    logged = {value["workflow"]: value["weight"] for value in row["candidates"]}
    result = {
        "monotonic_s": row["monotonic_s"], "source": row["source"],
        "reason": decision["reason"], "workflows": sorted(logged),
        "logical_unpriced_candidates": logical_unpriced_candidates(row),
        "prefix_summary_failures": len(row.get("unavailable_summaries", ())),
    }
    if "search_complete" not in decision:
        return {**result, "status": "selector_not_reached"}
    if decision["search_complete"] is not True:
        return {**result, "status": "search_incomplete"}
    if any(value["weight"] != logged[value["workflow"]] for value in row["candidates"]):
        return {**result, "status": "incomplete_metadata"}
    seconds = {name: state["path"][0] for name, state in row["states"].items()}
    truth = {name: 1. / (1. + value / reference_s) for name, value in seconds.items()}
    comparison = inspect(row, costs, {"logged": logged, "true_model_tool_path": truth})
    if not comparison["metadata_complete"]:
        return {**result, "status": "incomplete_metadata"}
    reproduced = comparison["choices"]["logged"]
    oracle_choice = comparison["choices"]["true_model_tool_path"]
    actual = set(comparison["actual"])
    if (
        not reproduced["complete"] or not oracle_choice["complete"]
        or list(decision["selected"]) != reproduced["selected"]
        or decision["reason"] != reproduced["reason"]
        or decision.get("ranked_selected") != reproduced.get("ranked_selected")
    ):
        return {**result, "status": "unverified_replay"}

    oracle = set(oracle_choice["selected"])
    groups, workflows = defaultdict(set), {}
    for value in row["candidates"]:
        owner, workflow = value["identity"], value["workflow"]
        workflows[owner] = workflow
        if value["unfinished_members"] > 1:
            groups[workflow, value["group"]].add(owner)
    actual_workflows = {workflows[owner] for owner in actual}
    oracle_workflows = {workflows[owner] for owner in oracle}
    return {
        **result, "status": "scored", "capacity_competition": comparison["capacity_competition"],
        "actual": sorted(actual), "oracle": sorted(oracle),
        "oracle_reason": oracle_choice["reason"],
        "actual_guard_kept_reference": "ranked_selected" in reproduced,
        "oracle_guard_kept_reference": "ranked_selected" in oracle_choice,
        "different_sets_before_guard": (
            set(reproduced.get("ranked_selected", reproduced["selected"]))
            != set(oracle_choice.get("ranked_selected", oracle_choice["selected"]))
        ),
        "changed_workflows": sorted(actual_workflows ^ oracle_workflows),
        "changed_members_workflows": sorted({workflows[owner] for owner in actual ^ oracle}),
        "shortest_candidate_inversions": comparison["feasible_true_path_priority_inversions"],
        "changed_visible_JOIN_groups": sum(peers & actual != peers & oracle for peers in groups.values()),
        "missed_full_ready_JOIN_groups": sum(
            len(peers) > 1 and peers <= oracle and not peers <= actual for peers in groups.values()
        ),
        "feasible_partial_JOIN_groups": inspect_choice(row, costs)["feasible_incomplete_sibling_groups"],
    }


def summarize_recorded_choices(rows, measured):
    counts, reasons = Counter(), Counter()
    for row in rows:
        counts["status_" + row["status"]] += 1
        reasons[row["reason"]] += 1
        omitted = row["logical_unpriced_candidates"]
        counts["windows_with_logical_evidence"] += omitted is not None
        counts["unpriced_logical_candidates"] += len(omitted or ())
        if row["source"] == "periodic_decay_resume" and omitted is not None:
            counts["periodic_windows_with_logical_evidence"] += 1
            counts["periodic_unpriced_logical_candidates"] += len(omitted)
        counts["prefix_summary_failures"] += row["prefix_summary_failures"]
        if row["status"] != "scored":
            continue
        counts["capacity_competitions"] += row["capacity_competition"]
        counts["different_sets"] += row["actual"] != row["oracle"]
        counts["different_sets_before_guard"] += row["different_sets_before_guard"]
        counts["actual_guard_kept_reference"] += row["actual_guard_kept_reference"]
        counts["oracle_guard_kept_reference"] += row["oracle_guard_kept_reference"]
        counts["guard_reconciled_set_difference"] += (
            row["different_sets_before_guard"] and row["actual"] == row["oracle"]
        )
        counts["different_workflow_sets"] += bool(row["changed_workflows"])
        counts["different_measured_members"] += bool(set(row["changed_members_workflows"]) & measured)
        for member in row.get("actual_disagreement_service", ()):
            name = "disagreement_service_" + member["outcome"]
            counts[name] += 1
            if member["key"] is not None and member["key"][1] in measured:
                counts["measured_" + name] += 1
        for name in ("changed_visible_JOIN_groups", "missed_full_ready_JOIN_groups"):
            counts[name] += row[name]
        counts["feasible_partial_JOIN_groups"] += len(row["feasible_partial_JOIN_groups"])
    return {**counts, "actual_selection_reasons": dict(reasons)}


def actual_disagreement_service(row, scored, service):
    """Follow actual choices that the same-state Oracle would not select."""
    if service is not None and (
        service["selected"] != list(row["decision"]["selected"])
        or service["reason"] != row["decision"]["reason"]
    ):
        raise ValueError("service readout disagrees with the recorded selection")
    periodic = row["source"] == "periodic_decay_resume"
    field = "periodic_service" if periodic else "free_service"
    evidence = {} if service is None else {
        item["invocation_id"]: item for item in service.get("all_" + field, service[field])
    }
    displaced = None if service is None else (service.get("periodic_ready_service_fit") or {}).get("displaced")
    result = []
    for owner in sorted(set(scored["actual"]) - set(scored["oracle"])):
        raw_key = row.get("keys", {}).get(owner)
        key = identity(raw_key, grant=True) if raw_key is not None else None
        grant = evidence.get(owner)
        episode = None if grant is None else grant["episode"]
        if grant is not None and (
            key != tuple(grant["key"])
            or (episode is not None and key != tuple(episode["key"]))
        ):
            raise ValueError("service readout changed the selected request attempt or session")
        if key is not None and displaced is not None and key == tuple(displaced):
            outcome = "displaced_by_ready_service_fit"
        elif grant is None:
            outcome = "without_service_association"
        elif not periodic and grant["separate_tool_replacement"]:
            outcome = "separate_tool_replacement"
        elif not periodic and not grant["actual_grant_verified"]:
            outcome = "without_verified_grant"
        elif episode is None or episode["new_service_before_next_control_or_intervention"] is None:
            outcome = "without_bounded_service_evidence"
        elif episode["new_service_before_next_control_or_intervention"] is True:
            outcome = "observed_before_next_control_or_intervention"
        else:
            outcome = "no_service_before_next_control_or_intervention"
        result.append({"key": key, "invocation_id": owner, "outcome": outcome})
    return result


def recorded_service_index(dataset, report):
    if (
        Path(report["registration"]).resolve() != Path(dataset["registration"]).resolve()
        or report["policy_kind"] != "learned"
        or report["complete_measured_execution_verified"] is not True
    ):
        raise ValueError("require the completed service audit of the same R5 arm")
    observed = report["input_jsonl_prefixes"]
    for suffix in ("/opportunities/admission_opportunities.jsonl", "/server/runtime_audit.jsonl"):
        path, = (name for name in dataset["input_jsonl_prefixes"] if name.endswith(suffix))
        if observed.get(path) != dataset["input_jsonl_prefixes"][path]:
            raise ValueError("choice and service audit used different native journal prefixes")
    rows = report["decisions"]
    index = {(row["monotonic_s"], row["source"]): row for row in rows}
    if len(index) != len(rows):
        raise ValueError("multiple choice-service records for the same selection")
    return index


def recorded_run_report(dataset, model, model_path, service_report=None):
    registration_path = Path(dataset["registration"]).resolve()
    if (
        sha256_file(registration_path) != dataset["inputs_sha256"][str(registration_path)]
        or sha256_file(model_path) != dataset["base_sha256"]
    ):
        raise ValueError("recorded audit inputs differ from the completed-run collection")
    registration = json.loads(registration_path.read_bytes())
    policy_files = (
        "beliefkv/policy/workflow_combination.py", "beliefkv/policy/workflow_shortest_frontier.py",
    )
    if any(sha256_file(ROOT / name) != registration["source_sha256"].get(name) for name in policy_files):
        raise ValueError("recorded audit must run with the exact serving selection sources")
    environment = registration["reference_environment"]
    if environment["WORKFLOW_WORK_MODEL_SHA256"] != dataset["base_sha256"] or (
        environment["WORKFLOW_WORK_SOURCE"] != "learned"
        or model.raw["strict_remaining_work_admission"] is not True or model.completion_path is None
    ):
        raise ValueError("recorded audit requires the registered R5 completion-path scorer")
    common, lifetime, _ = clocks(dataset)
    measured = set(dataset["cohort"]["measured_workflow_ids"])
    costs = CombinationCosts(**model.raw["admission_cost_profile"]["costs"])
    rows = [audit_recorded_choice(row, costs, model.completion_path.reference_s) for row in dataset["decisions"]]
    if service_report is not None:
        services = recorded_service_index(dataset, service_report)
        for row, scored in zip(dataset["decisions"], rows, strict=True):
            if scored["status"] == "scored" and scored["actual"] != scored["oracle"]:
                service = services.get((row["monotonic_s"], row["source"]))
                scored["actual_disagreement_service"] = actual_disagreement_service(row, scored, service)
    scopes = {
        "whole_completed_run": rows,
        "common_throughput_window": [row for row in rows if common[0] <= row["monotonic_s"] < common[1]],
        "measured_candidate_present": [row for row in rows if set(row["workflows"]) & measured],
        "competitive": [row for row in rows if row.get("capacity_competition")],
    }
    return {
        "scopes": {name: summarize_recorded_choices(values, measured) for name, values in scopes.items()},
        "decisions": rows, "windows": {"common_throughput": common, "measured_lifetime": lifetime},
        "scope": {
            "recorded_weights_and_same_priced_candidates": True,
            "logical_candidates_do_not_establish_native_fit": True,
            "unpriced_logical_candidates_can_include_policy_exclusions": True,
            "priority_inversions_and_partial_JOIN_checks_are_pre_guard_diagnostics": True,
            "oracle_scores_use_offline_native_progress_and_tool_DAG": True,
            "actual_disagreement_service_audit_attached": service_report is not None,
            "future_service_or_JCT_replayed": False,
        },
        "GPU_action": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "model", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--recorded", action="store_true", help="score recorded decisions from a completed R5 run")
    parser.add_argument("--choice-service", type=Path, help="attach the completed recorded-arm choice-service audit")
    args = parser.parse_args()
    if args.choice_service is not None and not args.recorded:
        parser.error("--choice-service requires --recorded")
    if args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("require fresh CPU-only audit output")
    dataset = json.loads(args.dataset.read_bytes())
    model = WorkflowWorkModel.load(args.model, sha256_file(args.model))
    if args.recorded:
        paths = [args.dataset, args.model, Path(__file__)]
        service_report = None
        if args.choice_service is not None:
            paths.append(args.choice_service)
            service_report = json.loads(args.choice_service.read_bytes())
        result = recorded_run_report(dataset, model, args.model, service_report)
        result["inputs_sha256"] = {str(path.resolve()): sha256_file(path) for path in paths}
        write(args.output, result)
        print(json.dumps(result["scopes"]))
        return
    profile = model.completion_path.raw
    if profile["training_source"]["sha256"] != sha256_file(args.dataset):
        raise ValueError("completion path belongs to another frozen dataset")
    costs = CombinationCosts(**model.raw["admission_cost_profile"]["costs"])
    work_reference = utility_reference_work(model.continuous_readout.contract)
    neutral_work = log_work_weight(
        math.log1p(model.continuous_readout.contract["reference_work"]), work_reference,
    )
    projections = {wf: model.task_encoding(text) for wf, text in dataset["task_text"].items()}
    measured = set(dataset["cohort"]["measured_workflow_ids"])
    common, lifetime, prefixes = clocks(dataset)
    rows, forecast_support = [], Counter()
    for row in dataset["decisions"]:
        forecasts, predicted_weights, reforecast_weights = {}, {}, {}
        current = {value["workflow"]: value["weight"] for value in row["candidates"]}
        point = {
            wf: log_work_weight(value["log1p_remaining_work"], work_reference)
            if value is not None and value.get("evidence") == "model"
            and type(value.get("log1p_remaining_work")) in (float, int) else current[wf]
            for wf, value in row["predictions"].items()
        }
        for wf, state in row["states"].items():
            projection, coverage = projections[wf]
            support = support_status(model, state["features"], coverage)
            _, forecast = model.predict(
                projection, state["features"], with_work=True, path_timing=state["timing"],
            )
            status = forecast["completion_path_support"] if support == "calibrated_distribution" else support
            forecast_support[status] += 1
            if wf in measured:
                forecast_support["measured_" + status] += 1
            predicted_weights[wf] = (
                1. / (1. + forecast["remaining_path_s"] / profile["reference_path_s"])
                if support == "calibrated_distribution" and "remaining_path_s" in forecast else
                log_work_weight(
                    forecast["log1p_remaining_work"],
                    profile["reference_path_s"] / profile["model_seconds_per_work_token"],
                ) if support == "calibrated_distribution" else .5
            )
            reforecast_weights[wf] = (
                log_work_weight(forecast["log1p_remaining_work"], work_reference)
                if support == "calibrated_distribution" else neutral_work
            )
            forecasts[wf] = {
                "truth_path_s": state["path"][0], "truth_model_path_s": state["path"][1],
                "support": status,
                **{name: forecast[name] for name in (
                    "remaining_path_s", "log1p_remaining_work",
                ) if name in forecast},
            }
        mappings = {
            "current_weight": current, "point_model_work": {wf: point.get(wf, current[wf]) for wf in current},
            "reforecast_point_model_work": reforecast_weights,
            "true_model_path": {wf: 1. / (1. + state["path"][1]) for wf, state in row["states"].items()},
            "true_model_tool_path": {wf: 1. / (1. + state["path"][0]) for wf, state in row["states"].items()},
            "predicted_model_tool_path": predicted_weights,
        }
        result = inspect(row, costs, mappings)
        result["forecasts"] = forecasts
        rows.append(result)
    scopes = {
        "whole_completed_run": rows,
        "common_throughput_window": [row for row in rows if common[0] <= row["monotonic_s"] <= common[1]],
        "measured_lifetime_window": [row for row in rows if lifetime[0] <= row["monotonic_s"] <= lifetime[1]],
        "measured_candidate_present": [row for row in rows if set(row["workflows"]) & measured],
    }
    report = {
        "scopes": {name: totals(values, measured) for name, values in scopes.items()},
        "forecast_support": dict(forecast_support), "decisions": rows,
        "windows": {"common_throughput": common, "measured_lifetime": lifetime},
        "input_jsonl_prefixes": prefixes,
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path)
            for path in (args.dataset, args.model, Path(__file__))
        },
        "scope": {
            "same_recorded_candidate_windows_and_planning_resources": True,
            "counts_are_decision_boundaries_not_independent_workflows": True,
            "required_JOIN_rights_preserved": True,
            "only_new_seconds_head_has_fixed18_label_holdout": True,
            "existing_base_representation_is_reused_development_not_unseen_project_generalization": True,
            "reforecast_has_current_observations_not_exact_async_worker_cache_lag": True,
            "no_future_queue_transfer_service_or_JCT_simulation": True,
            "no_GPU_performance_gain_proven": True,
        },
        "GPU_action": False,
    }
    write(args.output, report)
    print(json.dumps({key: report[key] for key in ("scopes", "forecast_support", "windows")}), flush=True)


if __name__ == "__main__":
    main()
