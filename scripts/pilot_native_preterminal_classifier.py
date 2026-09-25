#!/usr/bin/env python3
"""Offline TOOL_END -> next-child-response terminal classifier experiment."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path

import lightgbm as lgb
import numpy as np


DEV_PROJECTS = frozenset(("pydata/xarray", "pytest-dev/pytest"))


def _rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def _samples(dataset: Path, workflows: Path):
    children = {}
    for row in _rows(dataset / "reentries.jsonl"):
        if (
            row.get("reentry_kind") == "join"
            and row.get("terminal_status") == "satisfied"
            and row.get("training_eligible") is True
        ):
            for member in row.get("member_outcomes") or ():
                children[(row["workflow_id"], member["invocation_id"])] = (
                    row.get("project") or "unknown"
                )
    by_child = defaultdict(list)
    for path in workflows.glob("*/runtime_events.deepagents.jsonl"):
        for event in _rows(path):
            identity = (
                event.get("workflow_id"), event.get("invocation_id")
            )
            if identity in children:
                by_child[identity].append(event)
    output = []
    for child, events in by_child.items():
        events.sort(key=lambda event: float(event.get("ts_ms") or 0))
        started = float(events[0].get("ts_ms") or 0)
        last_tool = None
        last_model = None
        ordinal = 0
        for index, event in enumerate(events):
            kind = event.get("kind")
            if kind == "invocation_create":
                started = float(event["ts_ms"])
            if kind == "llm_result":
                last_model = event
            if kind != "tool_end":
                continue
            ordinal += 1
            timestamp = float(event["ts_ms"])
            next_result = next((
                later for later in events[index + 1:]
                if later.get("kind") in {
                    "llm_result", "tool_start", "return", "invocation_cancel"
                }
            ), None)
            terminal = False
            lead = None
            if next_result is not None and next_result.get("kind") == "llm_result":
                attrs = next_result.get("attributes") or {}
                if (
                    attrs.get("runtime_internal") is not True
                    and attrs.get("tool_call_count") == 0
                    and attrs.get("invalid_tool_call_count", 0) == 0
                    and attrs.get("finish_reason") in (None, "stop")
                    and isinstance(attrs.get("output_chars"), int)
                    and attrs["output_chars"] > 0
                ):
                    successor = next((
                        later for later in events[index + 1:]
                        if float(later.get("ts_ms") or 0)
                        > float(next_result["ts_ms"])
                        and later.get("kind") in {
                            "llm_submit", "tool_start", "return",
                            "invocation_cancel",
                        }
                    ), None)
                    if successor is not None and successor.get("kind") == "return":
                        terminal = True
                        lead = float(successor["ts_ms"]) - timestamp
            attrs = event.get("attributes") or {}
            previous_attrs = (last_model or {}).get("attributes") or {}
            features = (
                str(attrs.get("tool_name") or "unknown"),
                str(attrs.get("status") or "unknown"),
                str(previous_attrs.get("structured_action_kinds") or "unknown"),
                math.log1p(max(0, ordinal)),
                math.log1p(max(0, timestamp - started)),
                math.log1p(max(0, timestamp - last_tool))
                if last_tool is not None else 0,
                math.log1p(max(0, float(attrs.get("duration_ms") or 0))),
                math.log1p(max(0, float(attrs.get("output_chars") or 0))),
                math.log1p(max(0, float(previous_attrs.get("output_chars") or 0))),
            )
            output.append((
                children[child], child[0], features, int(terminal), lead
            ))
            last_tool = timestamp
    return output


def _matrix(samples, vocabulary):
    values = np.empty((len(samples), 9), dtype=np.float32)
    for index, sample in enumerate(samples):
        features = sample[2]
        for column in range(3):
            values[index, column] = vocabulary[column].get(
                features[column], -1
            )
        values[index, 3:] = features[3:]
    return values


def _metrics(samples, scores, *, threshold: float):
    selected = [sample for sample, score in zip(samples, scores)
                if score >= threshold]
    positives = sum(sample[3] for sample in samples)
    true = sum(sample[3] for sample in selected)
    return {
        "samples": len(samples), "positives": positives,
        "base_rate": positives / len(samples) if samples else None,
        "selected": len(selected), "true_positives": true,
        "precision": true / len(selected) if selected else None,
        "recall": true / positives if positives else None,
        "true_positive_lead_p50_ms": sorted(
            sample[4] for sample in selected if sample[3] and sample[4] is not None
        )[max(0, (true - 1) // 2)] if true else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--train-workflows", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--calibration-workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = _samples(args.train, args.train_workflows)
    fit = [row for row in rows if row[0] not in DEV_PROJECTS]
    development = [row for row in rows if row[0] in DEV_PROJECTS]
    calibration = _samples(
        args.calibration, args.calibration_workflows
    )
    vocabulary = [
        {name: index for index, name in enumerate(sorted({
            row[2][column] for row in fit
        }))}
        for column in range(3)
    ]
    model = lgb.train(
        {
            "objective": "binary", "learning_rate": .04, "num_leaves": 7,
            "min_data_in_leaf": 60, "lambda_l2": 8, "max_bin": 127,
            "seed": 42, "num_threads": 8, "verbosity": -1,
        },
        lgb.Dataset(
            _matrix(fit, vocabulary),
            label=np.asarray([row[3] for row in fit]),
            categorical_feature=[0, 1, 2],
        ),
        num_boost_round=120,
    )
    dev_scores = model.predict(_matrix(development, vocabulary), num_threads=8)
    cal_scores = model.predict(_matrix(calibration, vocabulary), num_threads=8)
    ranked = sorted(set(float(value) for value in dev_scores), reverse=True)
    threshold = next((
        cut for cut in ranked
        if (score := _metrics(development, dev_scores, threshold=cut))["selected"] >= 5
        and score["precision"] is not None and score["precision"] >= .5
    ), None)
    result = {
        "development": {
            "base": _metrics(development, dev_scores, threshold=0),
            "top_1_percent": _metrics(
                development, dev_scores, threshold=float(
                    np.quantile(dev_scores, .99)
                )
            ),
        },
        "calibration": {
            "base": _metrics(calibration, cal_scores, threshold=0),
            "top_1_percent": _metrics(
                calibration, cal_scores, threshold=float(
                    np.quantile(cal_scores, .99)
                )
            ),
        },
        "dev_precision_50_threshold": threshold,
        "dev_at_threshold": _metrics(
            development, dev_scores, threshold=threshold
        ) if threshold is not None else None,
        "calibration_at_dev_threshold": _metrics(
            calibration, cal_scores, threshold=threshold
        ) if threshold is not None else None,
        "status": "offline_exploration_not_deployable",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
