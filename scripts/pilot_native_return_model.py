#!/usr/bin/env python3
"""Offline project-held-out child RETURN timing experiment; never deploys actions."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path

import lightgbm as lgb
import numpy as np

from beliefkv.predictor.structured_frontier import (
    FrontierBeliefModel,
    _local_features_from_row,
)


CATEGORIES = ("state", "agent_definition_id", "boundary_last", "tool_family")
DEVELOPMENT_PROJECTS = frozenset(("pydata/xarray", "pytest-dev/pytest"))


def _targets(root: Path) -> tuple[dict[str, tuple[float, float]], list[dict]]:
    children = {}
    groups = []
    with (root / "reentries.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            entry = json.loads(line)
            if (
                entry.get("reentry_kind") != "join"
                or entry.get("terminal_status") != "satisfied"
                or entry.get("training_eligible") is not True
            ):
                continue
            members = entry.get("member_outcomes") or ()
            if not members or any(
                member.get("start_ts_ms") is None
                or member.get("return_ts_ms") is None for member in members
            ):
                continue
            reentry_ts = float(entry["reentry_ts_ms"])
            returns = {
                str(member["invocation_id"]): float(member["return_ts_ms"])
                for member in members
            }
            if not math.isclose(max(returns.values()), reentry_ts, abs_tol=1):
                continue
            for member in members:
                children[str(member["invocation_id"])] = (
                    float(member["return_ts_ms"]), float(member["start_ts_ms"])
                )
            groups.append({
                "workflow": str(entry["workflow_id"]),
                "parent": str(entry["invocation_id"]),
                "start": float(entry["wait_start_ts_ms"]),
                "end": reentry_ts,
                "returns": returns,
            })
    return children, groups


def _features(row: dict, item: dict, start: float) -> tuple[float | str, ...]:
    local = _local_features_from_row(
        row, {
            **item,
            "invocation_elapsed_ms": max(0.0, float(row["timestamp_ms"]) - start),
            "is_child": True,
        }
    )
    return (
        local.state, local.agent_definition_id,
        local.boundary_history[-1] if local.boundary_history else "none",
        local.tool_family,
        math.log1p(local.invocation_elapsed_ms),
        math.log1p(local.state_elapsed_ms),
        math.log1p(local.elapsed_wait_ms),
        math.log1p(local.current_sequence_tokens),
        math.log1p(local.generated_tokens),
        math.log1p(local.llm_round),
        math.log1p(local.active_tool_count),
    )


def _samples(root: Path, children: dict, *, development: bool | None):
    rows = []
    eval_rows = []
    with (root / "frontier_decision_points.jsonl").open(
        encoding="utf-8"
    ) as stream:
        for line in stream:
            row = json.loads(line)
            is_dev = str(row.get("project") or "") in DEVELOPMENT_PROJECTS
            if development is not None and development != is_dev:
                continue
            timestamp = float(row.get("timestamp_ms") or 0)
            workflow = str(row.get("workflow_id") or "")
            candidates = []
            for item in row.get("invocations", ()):
                child_id = str(item.get("invocation_id") or "")
                target = children.get(child_id)
                if target is not None and target[1] <= timestamp < target[0]:
                    candidates.append((
                        child_id, _features(row, item, target[1]),
                        target[0] - timestamp, workflow,
                    ))
            if development is False:
                rows.extend(candidates)
            else:
                eval_rows.append((
                    workflow, timestamp, str(row.get("decision_id") or ""),
                    {str(item.get("invocation_id") or ""): item
                     for item in row.get("invocations", ())},
                    candidates,
                ))
    return rows if development is False else eval_rows


def _matrix(rows, vocabulary):
    result = np.empty((len(rows), 11), dtype=np.float32)
    for index, raw in enumerate(rows):
        for column in range(4):
            result[index, column] = vocabulary[column].get(raw[column], -1)
        result[index, 4:] = raw[4:]
    return result


def _forecast(models, matrix):
    predictions = np.stack(
        [np.expm1(model.predict(matrix, num_threads=8)) for model in models],
        axis=1,
    )
    return np.maximum.accumulate(np.maximum(predictions, 0), axis=1)


def _metric(values: list[float]):
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "median_ms": ordered[(len(ordered) - 1) // 2] if ordered else None,
        "p90_ms": ordered[math.ceil(0.9 * len(ordered)) - 1] if ordered else None,
    }


def _evaluate(groups, eval_rows, vocabulary, models):
    entries = [candidate[1] for _, _, _, _, candidates in eval_rows
               for candidate in candidates]
    predictions = _forecast(models, _matrix(entries, vocabulary)) if entries else []
    indexed = defaultdict(list)
    position = 0
    for workflow, timestamp, decision_id, states, candidates in eval_rows:
        forecast = {}
        for child, _, _, _ in candidates:
            forecast[child] = predictions[position]
            position += 1
        indexed[workflow].append((timestamp, decision_id, states, forecast))
    first_errors, near_errors = [], []
    triggered, useful_500_2000, early, late = 0, 0, 0, 0
    for group in groups:
        snapshots = []
        for timestamp, _, states, forecasts in indexed[group["workflow"]]:
            if (
                timestamp < group["start"] or timestamp >= group["end"]
                or states.get(group["parent"], {}).get("state") != "wait_join"
            ):
                continue
            pending = [child for child, ts in group["returns"].items()
                       if ts > timestamp]
            if pending and all(child in forecasts for child in pending):
                snapshots.append((
                    timestamp,
                    max(forecasts[child][0] for child in pending),
                    max(forecasts[child][1] for child in pending),
                ))
        snapshots.sort()
        if not snapshots:
            continue
        first_errors.append(abs(snapshots[0][2] - (group["end"] - snapshots[0][0])))
        near = next(
            (snapshot for snapshot in snapshots
             if group["end"] - snapshot[0] <= 2_000), None,
        )
        if near is not None:
            near_errors.append(abs(near[2] - (group["end"] - near[0])))
        trigger = next((timestamp for timestamp, p10, _ in snapshots
                        if p10 <= 2_000), None)
        if trigger is None:
            continue
        triggered += 1
        lead = group["end"] - trigger
        useful_500_2000 += 500 <= lead <= 2_000
        early += lead > 2_000
        late += lead < 500
    return {
        "join_groups": len(groups),
        "first_absolute_error": _metric(first_errors),
        "within_2s_absolute_error": _metric(near_errors),
        "p10_2s_first_trigger": {
            "triggered": triggered, "lead_500_to_2000ms": useful_500_2000,
            "lead_over_2000ms": early, "lead_under_500ms": late,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    child_targets, training_groups = _targets(args.train)
    training_rows = _samples(args.train, child_targets, development=False)
    vocabulary = [
        {category: index for index, category in enumerate(sorted({
            item[1][column] for item in training_rows
        }))}
        for column in range(4)
    ]
    matrix = _matrix(
        [features for _, features, _, _ in training_rows], vocabulary
    )
    target = np.log1p(np.asarray(
        [remaining for _, _, remaining, _ in training_rows], dtype=np.float64
    ))
    child_counts = Counter((workflow, child) for child, _, _, workflow in training_rows)
    workflow_children = Counter(workflow for workflow, _ in child_counts)
    weights = np.asarray([
        1 / child_counts[(workflow, child)] / workflow_children[workflow]
        for child, _, _, workflow in training_rows
    ], dtype=np.float32)
    weights *= len(weights) / weights.sum()
    models = []
    for alpha in (0.1, 0.5, 0.9):
        dataset = lgb.Dataset(
            matrix, label=target, weight=weights,
            categorical_feature=list(range(4)), free_raw_data=True,
        )
        models.append(lgb.train(
            {
                "objective": "quantile", "alpha": alpha,
                "learning_rate": 0.06, "num_leaves": 15,
                "min_data_in_leaf": 80, "max_bin": 127,
                "lambda_l2": 2, "verbosity": -1, "num_threads": 8,
                "seed": 42,
            },
            dataset, num_boost_round=140,
        ))
    dev_children, _ = _targets(args.train)
    development_rows = _samples(args.train, dev_children, development=True)
    dev_workflows = {row[0] for row in development_rows}
    dev_groups = [
        group for group in training_groups
        if group["workflow"] in dev_workflows
    ]
    cal_children, cal_groups = _targets(args.calibration)
    calibration_rows = _samples(args.calibration, cal_children, development=None)
    result = {
        "training_project_holdout": sorted(DEVELOPMENT_PROJECTS),
        "training_samples": len(training_rows),
        "development": _evaluate(dev_groups, development_rows, vocabulary, models),
        "calibration": _evaluate(cal_groups, calibration_rows, vocabulary, models),
        "status": "offline_pilot_only_not_action_eligible",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
