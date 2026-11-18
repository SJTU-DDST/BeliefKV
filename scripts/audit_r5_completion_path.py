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
from scripts.audit_r5_shortest_contract import candidate
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


def inspect(row, costs, mappings):
    candidates = [candidate(value) for value in row["candidates"]]
    by_id = {value.identity: value for value in candidates}
    selected = set(row["decision"]["selected"])
    required = set(row["decision"].get("required_reference_members", ()))
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
    forced = [by_id[identity] for identity in required]
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
            required=tuple(sorted(required)), strict_remaining_work=True, clock=lambda: 0,
        )
        choices[name] = {
            "selected": list(decision["selected"]), "complete": decision["search_complete"],
            "reason": decision["reason"],
        }
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "model", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("require fresh CPU-only audit output")
    dataset = json.loads(args.dataset.read_bytes())
    model = WorkflowWorkModel.load(args.model, sha256_file(args.model))
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
