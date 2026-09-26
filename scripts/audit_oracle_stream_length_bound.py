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


def _eligible(rows: list[dict]) -> list[dict]:
    return [
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


def evaluate_rows(
    rows: list[dict], *, train_rows: list[dict] | None = None,
) -> dict:
    projects = sorted({row["project"] for row in rows})
    if train_rows is None and len(projects) < 3:
        raise ValueError("at least three projects with stage events required")
    if train_rows is not None:
        if not projects or not train_rows or {
            row["project"] for row in train_rows
        } & set(projects):
            raise ValueError("fixed training and held-out projects must be disjoint")
    eligible = _eligible(rows)
    fit = _eligible(train_rows) if train_rows is not None else eligible
    pooled = {
        name: {"actual": [], "estimate": [], "join_actual": [],
               "join_estimate": []}
        for name in (
            "fixed_stage_prior", "causal_length_prior",
            "causal_project_length_prior", "reported_length_hint",
            "bias_corrected_reported_length_hint",
            "oracle_length",
        )
    }
    folds = {}
    for project in projects:
        train = [row for row in fit if row["project"] != project]
        test = [row for row in eligible if row["project"] == project]
        if not train:
            raise ValueError("stage has no other-project length support")
        stage_prior = _task_median(train, lambda row: row["lead_ms"])
        length_prior = _task_median(
            train, lambda row: row["final_output_chars_oracle"]
        )
        train_hints = [
            row for row in train
            if type(row.get("planned_final_report_chars_at_notice")) is int
            and 256 <= row["planned_final_report_chars_at_notice"] <= 12_000
        ]
        length_hint_bias = (
            _task_median(
                train_hints,
                lambda row: row["final_output_chars_oracle"]
                - row["planned_final_report_chars_at_notice"],
            ) if train_hints else 0.
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
        hints = [
            row.get("planned_final_report_chars_at_notice")
            for row in test
        ]
        hint_valid = [
            type(value) is int and 256 <= value <= 12_000
            for value in hints
        ]
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
            "reported_length_hint": [
                max(0., (max(THRESHOLD_CHARS, hint) - THRESHOLD_CHARS)
                    * row["ms_per_char"] + tail_prior)
                if valid else max(
                    0., (length_prior - THRESHOLD_CHARS)
                    * row["ms_per_char"] + tail_prior,
                )
                for row, hint, valid in zip(test, hints, hint_valid)
            ],
            "bias_corrected_reported_length_hint": [
                max(0., (max(THRESHOLD_CHARS, hint + length_hint_bias)
                    - THRESHOLD_CHARS) * row["ms_per_char"] + tail_prior)
                if valid and train_hints else max(
                    0., (length_prior - THRESHOLD_CHARS)
                    * row["ms_per_char"] + tail_prior,
                )
                for row, hint, valid in zip(test, hints, hint_valid)
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
            "reported_length_hint_count": sum(hint_valid),
            "train_length_hint_count": len(train_hints),
            "train_length_hint_bias_chars": (
                length_hint_bias if train_hints else None
            ),
            "reported_length_hint_char_mae": (
                sum(
                    abs(hint - row["final_output_chars_oracle"])
                    for row, hint, valid in zip(test, hints, hint_valid)
                    if valid
                ) / sum(hint_valid)
                if any(hint_valid) else None
            ),
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
        "protocol": (
            "fixed_project_disjoint_train_heldout" if train_rows is not None
            else "development_leave_one_project_out"
        ),
        "train_projects": (
            sorted({row["project"] for row in train_rows})
            if train_rows is not None else None
        ),
        "train_evaluable": len(fit) if train_rows is not None else None,
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
            "A notice-time character hint is evaluated on the same full "
            "support, falling back to the train-only length median if absent. "
            "The bias correction is the task-balanced median of final minus "
            "announced characters from other training projects only; it "
            "falls back to the training length median without training hints. "
            "Stage false/censored signals are excluded from timing errors. "
            "Historical development projects, no independent sealed test or "
            "physical transfer. An oracle is not an online predictor."
        ),
    }


def _load_roots(workflows: list[Path], seen_tasks: set[str]) -> list[dict]:
    rows = []
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
    return rows


def evaluate(
    workflows: list[Path], *, train_workflows: list[Path] | None = None,
) -> dict:
    seen_tasks: set[str] = set()
    train_rows = (
        _load_roots(train_workflows, seen_tasks)
        if train_workflows is not None else None
    )
    rows = _load_roots(workflows, seen_tasks)
    return evaluate_rows(rows, train_rows=train_rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, action="append")
    parser.add_argument("--train-workflows", type=Path, action="append")
    parser.add_argument("--heldout-workflows", type=Path, action="append")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.workflows and (args.train_workflows or args.heldout_workflows):
        parser.error("choose --workflows or fixed --train/--heldout-workflows")
    if not args.workflows and not (args.train_workflows and args.heldout_workflows):
        parser.error("both --train-workflows and --heldout-workflows required")
    args.output.write_text(
        json.dumps(
            evaluate(
                args.workflows or args.heldout_workflows,
                train_workflows=args.train_workflows,
            ), indent=2, sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
