#!/usr/bin/env python3
"""Post-hoc bound for a live stream rate with a future-known final report length."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import median

try:
    from scripts.evaluate_child_intent_stream_milestones import collect
    from scripts.evaluate_child_return_intent_timing import _metrics
except ModuleNotFoundError:
    from evaluate_child_intent_stream_milestones import collect
    from evaluate_child_return_intent_timing import _metrics


THRESHOLD_CHARS = 1024
FIRST_CONTENT_CHARS = 64


def _task_median(rows: list[dict], value) -> float:
    by_task = defaultdict(list)
    for row in rows:
        by_task[row["task_id"]].append(value(row))
    return median(median(values) for values in by_task.values())


def _live_project_length(
    row: dict, project_rows: list[dict], fallback: float,
) -> tuple[float, bool]:
    history = [
        previous for previous in project_rows
        if previous["task_id"] != row["task_id"]
        and previous["return_ts_ms"] < row["signal_ts_ms"]
    ]
    if len(history) < 4 or len({
        previous["task_id"] for previous in history
    }) < 2:
        return fallback, False
    return _task_median(
        history, lambda previous: previous["final_output_chars_oracle"]
    ), True


def evaluate_rows(rows: list[dict]) -> dict:
    projects = sorted({row["project"] for row in rows})
    if len(projects) < 3:
        raise ValueError("at least three projects with stage events required")
    eligible = [
        {**row, "ms_per_char": (
            row["signal_ts_ms"] - row["observed_first_content_ts_ms"]
        ) / (THRESHOLD_CHARS - FIRST_CONTENT_CHARS)}
        for row in rows
        if row["label"] == "true"
        and type(row["final_output_chars_oracle"]) is int
        and row["final_output_chars_oracle"] >= THRESHOLD_CHARS
        and row["observed_first_content_ts_ms"] is not None
        and row["observed_first_content_ts_ms"] < row["signal_ts_ms"]
    ]
    pooled = {
        name: {"actual": [], "estimate": [], "join_actual": [],
               "join_estimate": []}
        for name in (
            "fixed_stage_prior", "causal_length_prior",
            "causal_project_length_prior", "oracle_length",
        )
    }
    folds = {}
    for project in projects:
        train = [row for row in eligible if row["project"] != project]
        test = [row for row in eligible if row["project"] == project]
        if not train:
            raise ValueError("stage has no other-project length support")
        stage_prior = _task_median(train, lambda row: row["lead_ms"])
        length_prior = _task_median(
            train, lambda row: row["final_output_chars_oracle"]
        )
        tail_prior = max(0., _task_median(
            train,
            lambda row: row["lead_ms"] - (
                row["final_output_chars_oracle"] - THRESHOLD_CHARS
            ) * row["ms_per_char"],
        ))
        actual = [row["lead_ms"] for row in test]
        online_lengths = []
        project_supported = 0
        for row in test:
            length, supported = _live_project_length(row, test, length_prior)
            project_supported += supported
            online_lengths.append(length)
        estimates = {
            "fixed_stage_prior": [stage_prior] * len(test),
            "causal_length_prior": [
                max(0., (length_prior - THRESHOLD_CHARS) * row["ms_per_char"]
                    + tail_prior)
                for row in test
            ],
            "causal_project_length_prior": [
                max(0., (length - THRESHOLD_CHARS) * row["ms_per_char"]
                    + tail_prior)
                for row, length in zip(test, online_lengths)
            ],
            "oracle_length": [
                max(0., (
                    row["final_output_chars_oracle"] - THRESHOLD_CHARS
                ) * row["ms_per_char"] + tail_prior)
                for row in test
            ],
        }
        fold = {
            "stage_candidates": sum(
                row["project"] == project for row in rows
            ),
            "stage_false_or_censored": sum(
                row["project"] == project and row["label"] != "true"
                for row in rows
            ),
            "stage_true": sum(
                row["project"] == project and row["label"] == "true"
                for row in rows
            ),
            "evaluable": len(test),
            "train_workflows": len({row["task_id"] for row in train}),
            "heldout_workflows": len({row["task_id"] for row in test}),
            "join_last_evaluable": sum(row["join_last"] for row in test),
            "real_lead_at_least_500ms": sum(
                row["lead_ms"] >= 500 for row in test
            ),
            "stage_prior_ms": stage_prior,
            "final_length_prior_chars": length_prior,
            "tail_prior_ms": tail_prior,
            "causal_project_length_supported": project_supported,
        }
        for name, estimate in estimates.items():
            current = pooled[name]
            current["actual"].extend(actual)
            current["estimate"].extend(estimate)
            join_actual = [
                value for value, row in zip(actual, test) if row["join_last"]
            ]
            join_estimate = [
                value for value, row in zip(estimate, test) if row["join_last"]
            ]
            current["join_actual"].extend(join_actual)
            current["join_estimate"].extend(join_estimate)
            fold[name] = {
                "child_return": _metrics(actual, estimate),
                "join_last_child": _metrics(join_actual, join_estimate),
            }
        folds[project] = fold
    return {
        "diagnostic_only": True,
        "projects": projects,
        "threshold_chars": THRESHOLD_CHARS,
        "first_content_chars": FIRST_CONTENT_CHARS,
        "stage_candidates": len(rows),
        "evaluable": len(eligible),
        "evaluable_join_last_child": sum(row["join_last"] for row in eligible),
        "stage_outcomes": dict(Counter(row["label"] for row in rows)),
        "folds": folds,
        "pooled": {
            name: {
                "child_return": _metrics(data["actual"], data["estimate"]),
                "join_last_child": _metrics(
                    data["join_actual"], data["join_estimate"]
                ),
            }
            for name, data in pooled.items()
        },
        "scope": (
            "Retrospective naturally completed episodes only. The 64-to-1024 "
            "character rate is live-observable; oracle_length additionally "
            "uses the FINAL response length, which is NOT known at the stage. "
            "Other-project task-balanced priors use only training labels. "
            "Project history includes only other workflows whose RETURN was "
            "observed before the current stage, requiring four completed "
            "examples in two workflows and otherwise reverting to train prior. "
            "Stage false/censored signals are excluded from timing errors. "
            "Historical development projects, no independent sealed test or "
            "physical transfer. An oracle is not an online predictor."
        ),
    }


def evaluate(workflows: list[Path]) -> dict:
    rows = []
    seen_tasks = set()
    for root in workflows:
        paths = sorted(root.glob("*/runtime_events.deepagents.jsonl"))
        if not paths:
            raise FileNotFoundError(f"no workflow events in {root}")
        duplicates = seen_tasks & {path.parent.name for path in paths}
        if duplicates:
            raise ValueError(f"duplicate workflow tasks: {sorted(duplicates)}")
        seen_tasks.update(path.parent.name for path in paths)
        current, _ = collect(root, THRESHOLD_CHARS)
        rows.extend(current)
    return evaluate_rows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(evaluate(args.workflows), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
