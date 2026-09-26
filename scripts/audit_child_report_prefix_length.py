#!/usr/bin/env python3
"""Project-disjoint audit of whether visible report prefixes predict remaining length."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
from statistics import median

import numpy as np

try:
    from scripts.audit_oracle_stream_length_bound import (
        THRESHOLD_CHARS, _eligible, _task_median,
    )
    from scripts.evaluate_child_intent_stream_milestones import collect
    from scripts.evaluate_child_return_intent_timing import _metrics
except ModuleNotFoundError:
    from audit_oracle_stream_length_bound import (
        THRESHOLD_CHARS, _eligible, _task_median,
    )
    from evaluate_child_intent_stream_milestones import collect
    from evaluate_child_return_intent_timing import _metrics


def prefix_features(prefix: str) -> list[float]:
    if len(prefix) != THRESHOLD_CHARS:
        raise ValueError("only the already streamed prefix is admissible")
    lines = prefix.splitlines()
    lower = prefix.lower()
    return [
        sum(line.lstrip().startswith("#") for line in lines),
        sum(line.lstrip().startswith(("- ", "* ", "1. ")) for line in lines),
        prefix.count("```"),
        prefix.count("\n"),
        prefix.count("**"),
        len(re.findall(r"\b(test|verify|validation)\b", lower)),
        len(re.findall(r"\b(summary|conclusion|recommendation)\b", lower)),
        len(re.findall(r"\b(implementation|change|fix)\b", lower)),
        len(lines[-1]) if lines else 0,
    ]


def load(workflows: Path) -> tuple[list[dict], dict]:
    rows, counts = collect(workflows, THRESHOLD_CHARS)
    eligible = _eligible(rows)
    texts = {}
    for task in {row["task_id"] for row in eligible}:
        path = workflows / task / "child_reports.json"
        if not path.is_file():
            continue
        for item in json.loads(path.read_text(encoding="utf-8")):
            completion = item.get("semantic_completion") or {}
            if completion.get("status") == "complete":
                texts[(task, item["invocation_id"])] = completion.get(
                    "summary", ""
                )
    matched = []
    missing = Counter()
    for row in eligible:
        text = texts.get((row["task_id"], row["invocation_id"]))
        if not isinstance(text, str) or not text:
            missing["no_matching_natural_report"] += 1
            continue
        if abs(len(text) - row["final_output_chars_oracle"]) > 5:
            missing["final_text_stream_length_mismatch"] += 1
            continue
        if len(text) < THRESHOLD_CHARS:
            missing["shorter_than_stream_milestone"] += 1
            continue
        matched.append({**row, "features": prefix_features(
            text[:THRESHOLD_CHARS]
        )})
    return matched, {
        "stage_outcomes": dict(Counter(row["label"] for row in rows)),
        "eligible": len(eligible),
        "matched": len(matched),
        "excluded": dict(missing),
        "collector": counts,
    }


def fit(train: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    matrix = np.array([row["features"] for row in train], dtype=float)
    target = np.log1p(np.array([
        row["final_output_chars_oracle"] - THRESHOLD_CHARS
        for row in train
    ], dtype=float))
    mean = matrix.mean(axis=0)
    scale = np.maximum(matrix.std(axis=0), 1.)
    normalized = np.column_stack((np.ones(len(train)), (matrix - mean) / scale))
    penalty = np.diag([0.] + [20.] * matrix.shape[1])
    weights = np.linalg.solve(
        normalized.T @ normalized + penalty, normalized.T @ target,
    )
    return mean, scale, weights


def predict_length(row: dict, model: tuple) -> float:
    mean, scale, weights = model
    x = np.r_[1., (np.array(row["features"]) - mean) / scale]
    return THRESHOLD_CHARS + min(20_000., max(0., np.expm1(x @ weights)))


def score(train: list[dict], test: list[dict]) -> dict:
    model = fit(train)
    length_prior = _task_median(
        train, lambda row: row["final_output_chars_oracle"]
    )
    stage_prior = _task_median(train, lambda row: row["lead_ms"])
    tail = max(0., _task_median(
        train, lambda row: row["lead_ms"] - (
            row["final_output_chars_oracle"] - THRESHOLD_CHARS
        ) * row["ms_per_char"],
    ))
    forecast_chars = [predict_length(row, model) for row in test]
    actual_chars = [row["final_output_chars_oracle"] for row in test]
    actual_ms = [row["lead_ms"] for row in test]
    def timing(lengths: list[float]) -> dict:
        estimate = [
            max(0., (length - THRESHOLD_CHARS) * row["ms_per_char"] + tail)
            for row, length in zip(test, lengths)
        ]
        join = [i for i, row in enumerate(test) if row["join_last"]]
        return {
            "return": _metrics(actual_ms, estimate),
            "join_last_child": _metrics(
                [actual_ms[i] for i in join], [estimate[i] for i in join]
            ),
        }
    return {
        "count": len(test),
        "workflows": len({row["task_id"] for row in test}),
        "join_last_child": sum(row["join_last"] for row in test),
        "lead_at_least_500ms": sum(row["lead_ms"] >= 500 for row in test),
        "train_count": len(train),
        "train_workflows": len({row["task_id"] for row in train}),
        "final_length_prior_chars": length_prior,
        "length_absolute_error_median_chars": median(
            abs(actual - estimate)
            for actual, estimate in zip(actual_chars, forecast_chars)
        ) if test else None,
        "prefix_structure": timing(forecast_chars),
        "training_length_prior": timing([length_prior] * len(test)),
        "training_stage_prior": {
            "return": _metrics(actual_ms, [stage_prior] * len(test)),
            "join_last_child": _metrics(
                [row["lead_ms"] for row in test if row["join_last"]],
                [stage_prior for row in test if row["join_last"]],
            ),
        },
        "future_length_oracle": timing(actual_chars),
    }


def evaluate(train_roots: list[Path], heldout_root: Path) -> dict:
    seen_tasks: set[str] = set()
    train = []
    train_roots_info = {}
    for root in train_roots:
        rows, info = load(root)
        tasks = {row["task_id"] for row in rows}
        if tasks & seen_tasks:
            raise ValueError("training workflows overlap")
        seen_tasks.update(tasks)
        train.extend(rows)
        train_roots_info[str(root)] = info
    heldout, heldout_info = load(heldout_root)
    projects = {row["project"] for row in train}
    eval_projects = {row["project"] for row in heldout}
    if len(projects) < 3 or not eval_projects or projects & eval_projects:
        raise ValueError("need at least three train projects disjoint from evaluation")
    if seen_tasks & {row["task_id"] for row in heldout}:
        raise ValueError("training and evaluation task IDs overlap")
    if len(train) < 20:
        raise ValueError("insufficient prefix training rows")
    by_project = defaultdict(list)
    for row in heldout:
        by_project[row["project"]].append(row)
    return {
        "diagnostic_only": True,
        "train_projects": sorted(projects),
        "heldout_projects": sorted(eval_projects),
        "train_roots": train_roots_info,
        "heldout": heldout_info,
        "train_rows": len(train),
        "evaluation": score(train, heldout),
        "by_project": {
            project: score(train, rows)
            for project, rows in sorted(by_project.items())
        },
        "scope": (
            "Only retrospective natural RETURNs with a completed report are "
            "scored. The first 1024 characters of the future-saved report are "
            "used as a proxy for the stream prefix; online prefix-feature "
            "delivery has not been verified. Length and RETURN labels for "
            "held-out projects are excluded from fitting. Not a predictor "
            "of stage eligibility, and not a physical transfer event."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, action="append", required=True)
    parser.add_argument("--heldout-workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(evaluate(args.train_workflows, args.heldout_workflows),
                   indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
