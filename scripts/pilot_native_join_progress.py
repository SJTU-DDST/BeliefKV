#!/usr/bin/env python3
"""Train-only child-progress timing pilot; validate by complete JOIN episode."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path

import lightgbm as lgb
import numpy as np
import orjson


NUMERIC = (
    "invocation_elapsed_ms", "state_elapsed_ms", "active_tool_elapsed_ms",
    "current_sequence_tokens", "observed_output_tokens", "llm_round",
    "child_count", "unfinished_child_count", "active_tool_count",
)
CATEGORICAL = ("state", "agent_definition_id", "active_tool_family",
               "backend_pressure")


def _events(path: Path):
    with path.open("rb") as stream:
        for line in stream:
            if line.strip():
                yield orjson.loads(line)


def _groups(dataset: Path):
    by_workflow = defaultdict(list)
    for item in _events(dataset / "reentries.jsonl"):
        if (item.get("reentry_kind") != "join"
                or item.get("terminal_status") != "satisfied"
                or item.get("training_eligible") is not True):
            continue
        members = item.get("member_outcomes") or ()
        if not members or any(member.get("return_ts_ms") is None
                              for member in members):
            continue
        returns = {member["invocation_id"]: float(member["return_ts_ms"])
                   for member in members}
        starts = {
            member["invocation_id"]: (
                float(member["start_ts_ms"])
                if member.get("start_ts_ms") is not None else None
            )
            for member in members
        }
        reentry = float(item["reentry_ts_ms"])
        if not math.isclose(max(returns.values()), reentry, abs_tol=1):
            continue
        group = {
            "identity": (item["workflow_id"], item["reentry_id"]),
            "project": item.get("project"),
            "parent": item["invocation_id"],
            "wait_start": float(item["wait_start_ts_ms"]),
            "return": reentry,
            "children": returns,
            "starts": starts,
            "snapshots": [],
            "last_sample_ts": float("-inf"),
        }
        by_workflow[item["workflow_id"]].append(group)
    return by_workflow


def _collect(dataset: Path, *, sample_spacing_ms: float = 10_000):
    groups = _groups(dataset)
    for row in _events(dataset / "frontier_decision_points.jsonl"):
        workflow = row.get("workflow_id")
        candidates = groups.get(workflow)
        if not candidates:
            continue
        timestamp = float(row["timestamp_ms"])
        features = {item["invocation_id"]: item
                    for item in row.get("invocations", ())
                    if item.get("invocation_id")}
        for group in candidates:
            if (timestamp < group["wait_start"] or timestamp >= group["return"]
                    or features.get(group["parent"], {}).get("state")
                    != "wait_join"):
                continue
            pending = {child: features.get(child)
                       for child, terminal in group["children"].items()
                       if terminal > timestamp}
            if not pending or any(item is None for item in pending.values()):
                continue
            if (group["snapshots"]
                    and timestamp - group["last_sample_ts"] < sample_spacing_ms):
                continue
            group["last_sample_ts"] = timestamp
            pending = {child: {
                **item,
                "is_child": True,
                **({
                    "invocation_elapsed_ms": max(
                        0, timestamp - group["starts"][child]
                    ),
                } if group["starts"][child] is not None else {}),
            } for child, item in pending.items()}
            group["snapshots"].append((timestamp, pending))
    return [group for workflow in groups.values() for group in workflow]


def _feature(feature, vocabulary):
    numeric = [math.log1p(max(0, float(feature.get(key) or 0)))
               for key in NUMERIC]
    categorical = [vocabulary[key].get(str(feature.get(key) or "unknown"), -1)
                   for key in CATEGORICAL]
    return numeric + categorical


def _quantile(values, q):
    ordered = sorted(values)
    return ordered[math.ceil(q * len(ordered)) - 1] if ordered else None


def _summarize(model, groups, vocabulary, *, log_target):
    errors = []
    signed = []
    trigger_leads = []
    groups_with_snapshot = 0
    for group in groups:
        if not group["snapshots"]:
            continue
        groups_with_snapshot += 1
        triggered = False
        for index, (timestamp, pending) in enumerate(group["snapshots"]):
            matrix = np.asarray(
                [_feature(feature, vocabulary) for feature in pending.values()],
                dtype=np.float32,
            )
            remaining = model.predict(matrix, num_threads=4)
            if log_target:
                remaining = np.expm1(remaining)
            remaining = np.maximum(0, remaining)
            # The last pending child determines ALL JOIN readiness.
            forecast = max(remaining)
            actual = group["return"] - timestamp
            if index == 0:
                errors.append(abs(forecast - actual))
                signed.append(forecast - actual)
            if not triggered and forecast <= 2_000:
                trigger_leads.append(actual)
                triggered = True
    return {
        "groups": len(groups),
        "groups_with_snapshot": groups_with_snapshot,
        "first_join_median_absolute_error_ms": _quantile(errors, .5),
        "first_join_p90_absolute_error_ms": _quantile(errors, .9),
        "first_join_mean_signed_error_ms": (
            sum(signed) / len(signed) if signed else None
        ),
        "first_join_within_500ms_rate": (
            sum(value <= 500 for value in errors) / len(errors)
            if errors else None
        ),
        "trigger_2s_groups": len(trigger_leads),
        "trigger_2s_actual_lead_p50_ms": _quantile(trigger_leads, .5),
        "trigger_2s_early_count": sum(value > 2_000 for value in trigger_leads),
        "trigger_2s_late_count": sum(value < 500 for value in trigger_leads),
        "trigger_2s_usable_count": sum(
            500 <= value <= 2_000 for value in trigger_leads
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-scale", choices=("linear", "log"),
                        default="log")
    parser.add_argument("--train-project")
    parser.add_argument("--calibration-project")
    args = parser.parse_args()
    if (args.train.resolve() == args.calibration.resolve()
            and (not args.train_project or not args.calibration_project
                 or args.train_project == args.calibration_project)):
        raise ValueError("same-dataset evaluation needs disjoint projects")
    train = _collect(args.train)
    calibration = (
        train if args.train.resolve() == args.calibration.resolve()
        else _collect(args.calibration)
    )
    if args.train_project:
        train = [group for group in train
                 if group["project"] == args.train_project]
    if args.calibration_project:
        calibration = [group for group in calibration
                       if group["project"] == args.calibration_project]
    vocabulary = {
        key: {name: index for index, name in enumerate(sorted({
            str(feature.get(key) or "unknown")
            for group in train for _, pending in group["snapshots"]
            for feature in pending.values()
        }))}
        for key in CATEGORICAL
    }
    values, targets, weights = [], [], []
    for group in train:
        if not group["snapshots"]:
            continue
        samples_by_child = defaultdict(int)
        for _, pending in group["snapshots"]:
            for child in pending:
                samples_by_child[child] += 1
        for timestamp, pending in group["snapshots"]:
            for child, feature in pending.items():
                values.append(_feature(feature, vocabulary))
                remaining = group["children"][child] - timestamp
                targets.append(
                    math.log1p(remaining)
                    if args.target_scale == "log" else remaining
                )
                weights.append(
                    1 / len(group["children"]) / samples_by_child[child]
                )
    if not values:
        raise ValueError("no training JOIN snapshots")
    model = lgb.train(
        {
            "objective": "regression_l1", "learning_rate": .025,
            "num_leaves": 7, "min_data_in_leaf": 50, "lambda_l2": 10,
            "max_bin": 127, "seed": 42, "num_threads": 4,
            "verbosity": -1,
        },
        lgb.Dataset(
            np.asarray(values, dtype=np.float32),
            label=np.asarray(targets),
            weight=np.asarray(weights),
            categorical_feature=list(range(len(NUMERIC), len(NUMERIC) +
                                           len(CATEGORICAL))),
        ),
        num_boost_round=100,
    )
    report = {
        "status": "offline_exploration_not_deployable",
        "train": _summarize(
            model, train, vocabulary, log_target=args.target_scale == "log"
        ),
        "calibration": _summarize(
            model, calibration, vocabulary, log_target=args.target_scale == "log"
        ),
        "fit_rows": len(values),
        "target_scale": args.target_scale,
        "train_project_filter": args.train_project,
        "calibration_project_filter": args.calibration_project,
        "note": "Calibration projects were already used by the original model.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
