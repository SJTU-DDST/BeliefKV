#!/usr/bin/env python3
"""Training-only project-LOO service ETA from causal final-request submit state."""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
from statistics import median
import sys

import numpy as np
import orjson

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_repeated_tool_timing import _quantile
from scripts.evaluate_child_return_intent_timing import (
    _metrics, _ridge_predict, load_episodes,
)
from scripts.evaluate_cold_tool_project_loo import require_complete_batch
from scripts.evaluate_cold_tool_structure_holdout import _paired_long_gain


MAX_METRIC_AGE_MS = 2000
METHODS = (
    "train_median", "notice_ridge", "load_ridge", "load_shape_ridge",
    "online_notice_ridge",
)
ONLINE_WINDOW = 64
ONLINE_MIN_SUPPORT = 4
ONLINE_MIN_WORKFLOWS = 3
ONLINE_RESIDUAL_WEIGHT = .5


def asof_rows(
    episodes: list[dict], submits: dict[str, dict],
    metrics: list[dict],
) -> tuple[list[dict], dict]:
    times = [float(item["monotonic_ts_ms"]) for item in metrics]
    if times != sorted(times):
        raise ValueError("metrics timestamps are not ordered")
    rows = []
    excluded = Counter()
    for episode in episodes:
        post = episode.get("post_notice") or {}
        rid = post.get("final_request_id")
        if not rid or rid not in submits:
            excluded["missing_final_submit"] += 1
            continue
        event = submits[rid]
        submitted_at = float(event["ts_ms"])
        if submitted_at <= episode["notice_ms"]:
            excluded["submit_not_after_notice"] += 1
            continue
        index = bisect_left(times, submitted_at) - 1
        if index < 0 or submitted_at - times[index] > MAX_METRIC_AGE_MS:
            excluded["missing_recent_load"] += 1
            continue
        attrs = event["attributes"]
        load = metrics[index]
        values = [
            attrs.get("prompt_chars"), attrs.get("message_count"),
            load.get("num_running_reqs"), load.get("num_queue_reqs"),
        ]
        if any(
            type(value) not in (int, float) or not math.isfinite(value)
            or value < 0 for value in values
        ):
            excluded["invalid_submit_or_load_fields"] += 1
            continue
        target = post.get("llm_submit_to_result_ms")
        if type(target) not in (int, float) or not math.isfinite(target) or target <= 0:
            excluded["invalid_service_label"] += 1
            continue
        prompt_chars, messages, running, queued = values
        rows.append({
            "project": episode["project"],
            "workflow": episode["task_id"],
            "duration_ms": float(target),
            "submit_ts_ms": submitted_at,
            "completion_ts_ms": submitted_at + float(target),
            "join_last": episode["join_last"],
            "metric_age_ms": submitted_at - times[index],
            "features": list(episode["features"]),
            "load_features": [
                *episode["features"], running, queued,
            ],
            "load_shape_features": [
                *episode["features"], running, queued,
                prompt_chars, messages,
            ],
        })
    return rows, dict(sorted(excluded.items()))


def load_rows(workloads: Path) -> tuple[list[dict], dict]:
    frozen, runner_errors = require_complete_batch(workloads / "workflows")
    episodes, counts = load_episodes(workloads)
    wanted = {
        row["post_notice"]["final_request_id"]
        for row in episodes if row.get("post_notice")
        and row["post_notice"].get("final_request_id")
    }
    submits = {}
    for path in sorted(workloads.glob("workflows/*/runtime_events.deepagents.jsonl")):
        with path.open("rb") as stream:
            for line in stream:
                if not line.strip():
                    continue
                event = orjson.loads(line)
                attrs = event.get("attributes") or {}
                rid = attrs.get("request_id")
                if event.get("kind") != "llm_submit" or rid not in wanted:
                    continue
                if rid in submits:
                    raise ValueError(f"duplicate final submit: {rid}")
                submits[rid] = event
    metrics_path = workloads / "sglang_metrics.jsonl"
    with metrics_path.open("rb") as stream:
        metrics = [orjson.loads(line) for line in stream if line.strip()]
    rows, excluded = asof_rows(episodes, submits, metrics)
    return rows, {
        "frozen_workflows": len(frozen),
        "runner_errors": runner_errors,
        "episode_counts": counts,
        "identified_final_requests": len(wanted),
        "asof_scored": len(rows),
        "excluded": excluded,
        "metric_age_p90_ms": _quantile([
            row["metric_age_ms"] for row in rows
        ], .9),
    }


def online_residual_predictions(
    rows: list[dict], prior: list[float], *,
    minimum_support: int = ONLINE_MIN_SUPPORT,
    minimum_workflows: int = ONLINE_MIN_WORKFLOWS,
) -> tuple[list[float], list[int]]:
    if len(rows) != len(prior):
        raise ValueError("online predictions require one prior per request")
    if minimum_support < 2 or minimum_workflows < 2:
        raise ValueError("online support must include independent prior workflows")
    pending = sorted(range(len(rows)), key=lambda i: (
        rows[i]["completion_ts_ms"], i,
    ))
    ordered = sorted(range(len(rows)), key=lambda i: (rows[i]["submit_ts_ms"], i))
    history = []
    updated = list(prior)
    supported = []
    completed = 0
    for index in ordered:
        now = rows[index]["submit_ts_ms"]
        while completed < len(pending) and (
            rows[pending[completed]]["completion_ts_ms"] < now
        ):
            history.append(pending[completed])
            history = history[-ONLINE_WINDOW:]
            completed += 1
        if (
            len(history) >= minimum_support
            and len({rows[old]["workflow"] for old in history})
            >= minimum_workflows
        ):
            correction = median(
                rows[old]["duration_ms"] - prior[old] for old in history
            )
            updated[index] = max(0., prior[index] + ONLINE_RESIDUAL_WEIGHT * correction)
            supported.append(index)
    return updated, supported


def evaluate(rows: list[dict]) -> dict:
    projects = sorted({row["project"] for row in rows})
    if len(projects) < 3:
        raise ValueError("at least three training projects are required")
    pooled = {method: ([], []) for method in METHODS}
    folds = {}
    for project in projects:
        train = [row for row in rows if row["project"] != project]
        test = [row for row in rows if row["project"] == project]
        predictions = {
            "train_median": [float(np.median([
                row["duration_ms"] for row in train
            ]))] * len(test),
        }
        for method, feature in (
            ("notice_ridge", "features"),
            ("load_ridge", "load_features"),
            ("load_shape_ridge", "load_shape_features"),
        ):
            predictions[method] = _ridge_predict(
                [{**row, "lead_ms": row["duration_ms"],
                  "features": row[feature]} for row in train],
                [{**row, "lead_ms": row["duration_ms"],
                  "features": row[feature]} for row in test],
            )
        predictions["online_notice_ridge"], supported = (
            online_residual_predictions(test, predictions["notice_ridge"])
        )
        actual = [row["duration_ms"] for row in test]
        folds[project] = {
            "requests": len(test),
            "workflows": len({row["workflow"] for row in test}),
            "join_last_requests": sum(row["join_last"] for row in test),
            "online_supported": len(supported),
            "online_supported_workflows": len({
                test[index]["workflow"] for index in supported
            }),
            "methods": {},
            "paired_vs_notice": {},
        }
        for method in METHODS:
            estimate = predictions[method]
            pooled[method][0].extend(actual)
            pooled[method][1].extend(estimate)
            folds[project]["methods"][method] = {
                "all": _metrics(actual, estimate),
                "join_last": _metrics(
                    [value for value, row in zip(actual, test) if row["join_last"]],
                    [value for value, row in zip(estimate, test) if row["join_last"]],
                ),
            }
        for method in ("load_ridge", "load_shape_ridge", "online_notice_ridge"):
            paired = _paired_long_gain(
                test, np.asarray(predictions["notice_ridge"]),
                np.asarray(predictions[method]),
            )
            paired["requests"] = paired.pop("long_calls")
            folds[project]["paired_vs_notice"][method] = paired
        selected = [test[index] for index in supported]
        paired = _paired_long_gain(
            selected,
            np.asarray([predictions["notice_ridge"][i] for i in supported]),
            np.asarray([predictions["online_notice_ridge"][i] for i in supported]),
        )
        paired["requests"] = paired.pop("long_calls")
        folds[project]["online_supported_paired_gain"] = paired
    return {
        "status": "training_only_project_loo_submit_service_not_action_eligible",
        "projects": projects,
        "max_metric_age_ms": MAX_METRIC_AGE_MS,
        "online_history": {
            "window": ONLINE_WINDOW,
            "minimum_support": ONLINE_MIN_SUPPORT,
            "minimum_workflows": ONLINE_MIN_WORKFLOWS,
            "residual_weight": ONLINE_RESIDUAL_WEIGHT,
        },
        "features": {
            "notice_ridge": [
                "child_age_ms", "prior_llm_results", "prior_tool_ends",
            ],
            "load_ridge_extra": ["num_running_reqs", "num_queue_reqs"],
            "load_shape_ridge_extra": ["prompt_chars", "message_count"],
            "online_notice_ridge": (
                "Median residual of completed same-project requests strictly "
                "before this submit; unsupported requests use frozen notice ridge."
            ),
        },
        "folds": folds,
        "pooled": {
            method: _metrics(*pooled[method]) for method in METHODS
        },
        "limitation": (
            "Project-LOO over training projects only. ETA starts at the "
            "observed final child LLM_SUBMIT, not the earlier notice. Metrics "
            "must predate the submit by at most two seconds. Completion time "
            "only labels the prediction; no post-submit output length, future "
            "load, online delivery, physical H2D, or independent test claim. "
            "Project-local residual adaptation is causal test-time learning "
            "from completed workflows, not zero-shot project generalization."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workloads", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows, counts = load_rows(args.workloads)
    report = evaluate(rows)
    report["counts"] = counts
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )


if __name__ == "__main__":
    main()
