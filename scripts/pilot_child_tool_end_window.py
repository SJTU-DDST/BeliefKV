#!/usr/bin/env python3
"""Causal project-held-out screen for TOOL_END preceding a child RETURN.

Diagnostic only. Tool inputs and command bodies never enter model features or
reports. A predicted final response is not an estimate of JOIN completion.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from statistics import median

import lightgbm as lgb
import numpy as np

if __package__:
    from scripts.audit_child_hidden_trace import index_workflow
else:
    from audit_child_hidden_trace import index_workflow


WINDOW_START_MS = 500
WINDOW_END_MS = 20000
THRESHOLDS = tuple(round(value / 100, 2) for value in range(50, 100, 5))


def _events(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as stream:
        return sorted(
            (json.loads(line) for line in stream if line.strip()),
            key=lambda row: (float(row["ts_ms"]), int(row.get("sequence") or 0)),
        )


def samples(workflows: Path) -> list[dict]:
    output = []
    for path in sorted(workflows.glob("*/runtime_events.deepagents.jsonl")):
        events = _events(path)
        terminal, join_last = index_workflow(events)
        children = {
            row["target_invocation_id"]
            for row in events
            if row["kind"] == "spawn" and row.get("target_invocation_id")
        }
        by_child = defaultdict(list)
        for event in events:
            if event.get("invocation_id") in children:
                by_child[event["invocation_id"]].append(event)
        for child, timeline in by_child.items():
            active = {}
            last_model = None
            first_at = float(timeline[0]["ts_ms"])
            ordinal = 0
            for index, event in enumerate(timeline):
                kind = event["kind"]
                attrs = event.get("attributes") or {}
                if kind == "llm_result" and not attrs.get("runtime_internal"):
                    last_model = event
                if kind == "tool_start" and attrs.get("tool_call_id"):
                    active[attrs["tool_call_id"]] = event
                if kind != "tool_end":
                    continue
                ordinal += 1
                started = active.pop(attrs.get("tool_call_id"), None)
                if started is None or active:
                    continue
                following = next((
                    row for row in timeline[index + 1:]
                    if row["kind"] in {
                        "invocation_cancel", "return", "tool_start",
                    }
                    or (row["kind"] == "llm_result"
                        and not (row.get("attributes") or {}).get(
                            "runtime_internal"
                        ))
                ), None)
                if following is None or following["kind"] != "llm_result":
                    continue
                future_rid = (following.get("attributes") or {}).get("request_id")
                true_return = (
                    future_rid in terminal
                    and terminal[future_rid][0] == child
                )
                lead = (
                    float(terminal[future_rid][1]) - float(event["ts_ms"])
                    if true_return else None
                )
                start_attrs = started.get("attributes") or {}
                prior = (last_model or {}).get("attributes") or {}
                output.append({
                    "project": path.parent.name.split("__", 1)[0],
                    "workflow": path.parent.name,
                    "child": child,
                    "tool_name": str(attrs.get("tool_name") or "unknown"),
                    "shape": str(
                        start_attrs.get("observed_command_shape") or "unknown"
                    ),
                    "status": str(attrs.get("status") or "unknown"),
                    "ordinal": ordinal,
                    "child_elapsed_ms": max(
                        0., float(event["ts_ms"]) - first_at,
                    ),
                    "tool_duration_ms": max(
                        0., float(event["ts_ms"]) - float(started["ts_ms"]),
                    ),
                    "output_chars": max(0, int(attrs.get("output_chars") or 0)),
                    "previous_model_chars": max(
                        0, int(prior.get("output_chars") or 0),
                    ),
                    "true_return": true_return,
                    "lead_ms": lead,
                    "join_last": true_return and child in join_last,
                })
    return output


def matrix(rows: list[dict], vocabulary: list[dict[str, int]]) -> np.ndarray:
    values = np.empty((len(rows), 8), dtype=np.float32)
    for index, row in enumerate(rows):
        for column, key in enumerate(("tool_name", "shape", "status")):
            values[index, column] = vocabulary[column].get(row[key], -1)
        values[index, 3:] = [
            math.log1p(row[key]) for key in (
                "ordinal", "child_elapsed_ms", "tool_duration_ms",
                "output_chars", "previous_model_chars",
            )
        ]
    return values


def fit(rows: list[dict]):
    labels = np.asarray([
        row["true_return"]
        and WINDOW_START_MS <= row["lead_ms"] <= WINDOW_END_MS
        for row in rows
    ], dtype=np.float32)
    if labels.sum() < 8 or (1 - labels).sum() < 8:
        raise ValueError("insufficient completed positive/negative tool windows")
    vocabulary = [
        {value: number for number, value in enumerate(sorted({
            row[key] for row in rows
        }))}
        for key in ("tool_name", "shape", "status")
    ]
    weights = np.where(
        labels == 1, len(rows) / (2 * labels.sum()),
        len(rows) / (2 * (len(rows) - labels.sum())),
    )
    model = lgb.train(
        {
            "objective": "binary", "learning_rate": .04, "num_leaves": 7,
            "min_data_in_leaf": 24, "lambda_l2": 8, "max_bin": 127,
            "seed": 47, "num_threads": 4, "verbosity": -1,
        },
        lgb.Dataset(matrix(rows, vocabulary), label=labels, weight=weights,
                    categorical_feature=[0, 1, 2]),
        num_boost_round=80,
    )
    return model, vocabulary


def report(rows: list[dict], scores: np.ndarray, threshold: float) -> dict:
    selected = [row for row, score in zip(rows, scores) if score >= threshold]
    correct = [
        row for row in selected if row["true_return"]
        and WINDOW_START_MS <= row["lead_ms"] <= WINDOW_END_MS
    ]
    positives = [
        row for row in rows if row["true_return"]
        and WINDOW_START_MS <= row["lead_ms"] <= WINDOW_END_MS
    ]
    return {
        "threshold": threshold,
        "eligible_candidates": len(rows),
        "true_window_events": len(positives),
        "selected": len(selected),
        "correct": len(correct),
        "precision": len(correct) / len(selected) if selected else None,
        "recall": len(correct) / len(positives) if positives else None,
        "trigger_projects": len({row["project"] for row in selected}),
        "trigger_workflows": len({row["workflow"] for row in selected}),
        "not_next_return": sum(not row["true_return"] for row in selected),
        "over_20s_early": sum(
            row["true_return"] and row["lead_ms"] > WINDOW_END_MS
            for row in selected
        ),
        "under_500ms": sum(
            row["true_return"] and row["lead_ms"] < WINDOW_START_MS
            for row in selected
        ),
        "confirmed_join_last_correct": sum(
            row["join_last"] for row in correct
        ),
        "confirmed_join_last_in_selected": sum(
            row["join_last"] for row in selected
        ),
        "correct_lead_p50_ms": (
            median(row["lead_ms"] for row in correct) if correct else None
        ),
    }


def select_rule(rows: list[dict], scores: np.ndarray) -> float | None:
    qualified = []
    for threshold in THRESHOLDS:
        result = report(rows, scores, threshold)
        if (result["correct"] >= 8 and result["trigger_projects"] >= 2
                and result["precision"] is not None
                and result["precision"] >= .90):
            qualified.append((result["correct"], threshold))
    return max(qualified)[1] if qualified else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, action="append", required=True)
    parser.add_argument("--heldout-workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    train = []
    for root in args.train_workflows:
        if not root.is_dir():
            parser.error(f"missing training workflow directory: {root}")
        batch = samples(root)
        if not batch:
            parser.error(f"no completed tool-return evidence in {root}")
        train.extend(batch)
    projects = {row["project"] for row in train}
    if len(projects) < 3:
        parser.error("need at least three training projects")
    folds = []
    for project in sorted(projects):
        fit_rows = [row for row in train if row["project"] != project]
        validation = [row for row in train if row["project"] == project]
        model, vocab = fit(fit_rows)
        fold_scores = model.predict(matrix(validation, vocab), num_threads=4)
        folds.extend(zip(validation, fold_scores))
    cv_rows = [row for row, _ in folds]
    cv_scores = np.asarray([value for _, value in folds])
    chosen = select_rule(cv_rows, cv_scores)
    result = {
        "diagnostic_only": True,
        "window_ms": [WINDOW_START_MS, WINDOW_END_MS],
        "train_projects": sorted(projects),
        "train_rows": len(train),
        "project_cv": {
            str(cut): report(cv_rows, cv_scores, cut) for cut in THRESHOLDS
        },
        "threshold_chosen_on_train_project_cv": chosen,
        "heldout_at_frozen_threshold": None,
    }
    if chosen is not None:
        if not args.heldout_workflows.is_dir():
            parser.error("missing held-out workflow directory")
        heldout = samples(args.heldout_workflows)
        if not heldout or {row["project"] for row in heldout} & projects:
            parser.error("held-out must have tool evidence from disjoint projects")
        model, vocab = fit(train)
        heldout_scores = model.predict(matrix(heldout, vocab), num_threads=4)
        result["heldout_at_frozen_threshold"] = report(
            heldout, heldout_scores, chosen,
        )
        result["heldout_projects"] = sorted({
            row["project"] for row in heldout
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "train_rows": result["train_rows"],
        "threshold_chosen_on_train_project_cv": chosen,
        "heldout_at_frozen_threshold": result["heldout_at_frozen_threshold"],
    }, indent=2))


if __name__ == "__main__":
    main()
