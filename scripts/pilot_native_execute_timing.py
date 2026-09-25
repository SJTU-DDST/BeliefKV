#!/usr/bin/env python3
"""Read-only test of coarse execute command classes against tool duration."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import shlex

import orjson

from beliefkv.predictor.command_class import execute_command_class


def _diagnostic_command_class(command: str, *, detailed: bool) -> str:
    coarse = execute_command_class({"command": command})
    if not detailed or coarse != "test_suite":
        return coarse
    try:
        words = shlex.split(command)
    except ValueError:
        return coarse
    if any("::" in item or (
        item.startswith("tests.") and item.count(".") >= 4
    ) for item in words):
        return "test_case"
    if any(item.startswith("tests.") or (
        item.startswith("tests/") and item.endswith(".py")
    ) for item in words if not item.endswith("runtests.py")):
        return "test_module"
    return coarse


def _rows(path):
    with path.open("rb") as stream:
        for line in stream:
            if line.strip():
                yield orjson.loads(line)


def _collect(root: Path, *, detailed: bool):
    dataset = root / "dataset" / "external_waits.jsonl"
    workflows = root / "workloads" / "workflows"
    waits = defaultdict(list)
    for item in _rows(dataset):
        if (item.get("tool_name") != "execute"
                or item.get("training_eligible_survival") is not True
                or item.get("censored") is True):
            continue
        waits[item["instance_id"]].append(item)
    matched = []
    for instance, rows in waits.items():
        path = workflows / instance / "trajectory.json"
        if not path.exists():
            continue
        calls = {}
        for message in orjson.loads(path.read_bytes()):
            for call in message.get("tool_calls") or ():
                if call.get("name") == "execute":
                    arguments = call.get("args") or {}
                    command = arguments.get("command")
                    if isinstance(command, str) and call.get("id"):
                        calls[call["id"]] = command
        for item in rows:
            command = calls.get(item["tool_call_id"])
            if command is None:
                continue
            matched.append({
                "project": item["project"],
                "workflow": item["workflow_id"],
                "tool_call_id": item["tool_call_id"],
                "invocation": item["invocation_id"],
                "start_ts_ms": float(item["start_ts_ms"]),
                "class": _diagnostic_command_class(command, detailed=detailed),
                "duration_ms": float(item["observed_duration_ms"]),
            })
    return matched, sum(map(len, waits.values()))


def _quantile(values, q):
    ordered = sorted(values)
    return ordered[math.ceil(q * len(ordered)) - 1] if ordered else None


def _evaluate(rows, class_median, global_median):
    errors_global = []
    errors_class = []
    long_global = []
    long_class = []
    by_workflow = defaultdict(lambda: ([], []))
    by_project = defaultdict(lambda: ([], []))
    for item in rows:
        actual = item["duration_ms"]
        original = abs(actual - global_median)
        conditioned = abs(actual - class_median.get(item["class"], global_median))
        errors_global.append(original)
        errors_class.append(conditioned)
        by_workflow[item["workflow"]][0].append(original)
        by_workflow[item["workflow"]][1].append(conditioned)
        by_project[item["project"]][0].append(original)
        by_project[item["project"]][1].append(conditioned)
        if actual >= 2_000:
            long_global.append(original)
            long_class.append(conditioned)
    return {
        "matched_execute_calls": len(rows),
        "projects": sorted({row["project"] for row in rows}),
        "long_calls_at_least_2s": len(long_global),
        "global_p50_absolute_error_ms": _quantile(errors_global, .5),
        "class_p50_absolute_error_ms": _quantile(errors_class, .5),
        "long_global_p50_absolute_error_ms": _quantile(long_global, .5),
        "long_class_p50_absolute_error_ms": _quantile(long_class, .5),
        "class_within_500ms": (
            sum(value <= 500 for value in errors_class) / len(errors_class)
            if errors_class else None
        ),
        "workflow_count": len(by_workflow),
        "workflow_weighted_global_p50_absolute_error_ms": _quantile([
            _quantile(values[0], .5) for values in by_workflow.values()
        ], .5),
        "workflow_weighted_class_p50_absolute_error_ms": _quantile([
            _quantile(values[1], .5) for values in by_workflow.values()
        ], .5),
        "by_project": {
            project: {
                "calls": len(values[0]),
                "global_p50_absolute_error_ms": _quantile(values[0], .5),
                "class_p50_absolute_error_ms": _quantile(values[1], .5),
            }
            for project, values in sorted(by_project.items())
        },
    }


def _compare_frontier_model(dataset: Path, matched, model_path: Path,
                            class_median):
    from beliefkv.predictor.structured_frontier import (
        FrontierBeliefModel, WaitBeliefKind, _local_features_from_row,
    )

    model = FrontierBeliefModel.from_dict(
        json.loads(model_path.read_text(encoding="utf-8"))
    )
    by_identity = {
        (item["workflow"], item["tool_call_id"]): item
        for item in matched
    }
    found = set()
    base_errors, classified_errors = [], []
    long_base, long_classified = [], []
    by_workflow = defaultdict(lambda: ([], []))
    for row in _rows(dataset / "frontier_decision_points.jsonl"):
        attrs = row.get("trigger_attributes") or {}
        if row.get("trigger_kind") != "tool_start":
            continue
        key = row.get("workflow_id"), attrs.get("tool_call_id")
        sample = by_identity.get(key)
        if sample is None or key in found:
            continue
        features = next((
            item for item in row.get("invocations", ())
            if item.get("invocation_id") == sample["invocation"]
            and item.get("state") == "wait_tool"
        ), None)
        if features is None:
            continue
        prediction = model.predict(_local_features_from_row(
            row, features, tool_feature_contract=model.tool_feature_contract,
        ))
        wait = prediction.wait_belief
        if wait.kind is not WaitBeliefKind.TOOL or not wait.residual_duration.values:
            continue
        elapsed = max(0, float(row["timestamp_ms"]) - sample["start_ts_ms"])
        actual = max(0, sample["duration_ms"] - elapsed)
        predicted = wait.residual_duration.quantile(.5)
        class_prediction = max(
            0, class_median.get(sample["class"], class_median["execute"]) - elapsed
        )
        base = abs(actual - predicted)
        classified = abs(actual - class_prediction)
        base_errors.append(base)
        classified_errors.append(classified)
        if sample["duration_ms"] >= 2_000:
            long_base.append(base)
            long_classified.append(classified)
        by_workflow[sample["workflow"]][0].append(base)
        by_workflow[sample["workflow"]][1].append(classified)
        found.add(key)
    return {
        "matched_first_decisions": len(found),
        "frontier_p50_absolute_error_ms": _quantile(base_errors, .5),
        "class_p50_absolute_error_ms": _quantile(classified_errors, .5),
        "long_calls_at_least_2s": len(long_base),
        "long_frontier_p50_absolute_error_ms": _quantile(long_base, .5),
        "long_class_p50_absolute_error_ms": _quantile(long_classified, .5),
        "workflow_count": len(by_workflow),
        "workflow_weighted_frontier_p50_absolute_error_ms": _quantile([
            _quantile(values[0], .5) for values in by_workflow.values()
        ], .5),
        "workflow_weighted_class_p50_absolute_error_ms": _quantile([
            _quantile(values[1], .5) for values in by_workflow.values()
        ], .5),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-root", type=Path, required=True)
    parser.add_argument("--calibration-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frontier-model", type=Path)
    parser.add_argument("--detailed-test-classes", action="store_true")
    args = parser.parse_args()
    training, training_total = _collect(
        args.train_root, detailed=args.detailed_test_classes
    )
    calibration, calibration_total = _collect(
        args.calibration_root, detailed=args.detailed_test_classes
    )
    overlap = {row["project"] for row in training} & {
        row["project"] for row in calibration
    }
    if overlap or not training or not calibration:
        raise ValueError("test requires nonempty project-disjoint datasets")
    durations = defaultdict(list)
    for item in training:
        durations[item["class"]].append(item["duration_ms"])
    global_median = _quantile([row["duration_ms"] for row in training], .5)
    class_median = {
        category: _quantile(values, .5)
        for category, values in durations.items() if len(values) >= 20
    }
    class_median["execute"] = global_median
    report = {
        "status": "offline_exploration_not_deployable",
        "detailed_test_classes": args.detailed_test_classes,
        "train_total_execute_calls": training_total,
        "calibration_total_execute_calls": calibration_total,
        "class_support": {key: len(values)
                          for key, values in sorted(durations.items())},
        "global_median_ms": global_median,
        "class_median_ms": class_median,
        "train": _evaluate(training, class_median, global_median),
        "calibration": _evaluate(calibration, class_median, global_median),
        "note": "Root trajectory only: child execute commands are not recorded.",
    }
    if args.frontier_model is not None:
        report["frontier_comparison"] = _compare_frontier_model(
            args.calibration_root / "dataset", calibration,
            args.frontier_model, class_median,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
