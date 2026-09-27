#!/usr/bin/env python3
"""Training-only project-LOO JOIN stream ETA with as-of scheduler load."""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter
import json
import math
from pathlib import Path
from statistics import median
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_cold_tool_project_loo import require_complete_batch
from scripts.evaluate_cold_tool_structure_holdout import _paired_long_gain
from scripts.evaluate_join_stream_eta import STAGE_CHARS, align, collect_joins
from scripts.pilot_stream_dynamic_eta import _fit, _predict, _quality, samples


MAX_METRIC_AGE_MS = 2000.


def attach_asof(
    rows: list[dict], metrics: list[dict],
) -> tuple[list[dict], dict[str, int]]:
    times = [float(item["monotonic_ts_ms"]) for item in metrics]
    if times != sorted(times) or not all(math.isfinite(t) for t in times):
        raise ValueError("metrics timestamps must be finite and ordered")
    selected = []
    excluded = Counter()
    for row in rows:
        when = float(row["trigger_ms"])
        index = bisect_left(times, when) - 1
        if index < 0 or when - times[index] > MAX_METRIC_AGE_MS:
            excluded["no_recent_prior_snapshot"] += 1
            continue
        snapshot = metrics[index]
        running = snapshot.get("num_running_reqs")
        queued = snapshot.get("num_queue_reqs")
        if any(
            type(value) not in (int, float) or not math.isfinite(value)
            or value < 0 for value in (running, queued)
        ):
            excluded["invalid_snapshot"] += 1
            continue
        selected.append({
            **row,
            "load_features": [
                *row["features"], math.log1p(running), math.log1p(queued),
            ],
            "metric_age_ms": when - times[index],
        })
    return selected, dict(excluded)


def evaluate_rows(rows: list[dict]) -> dict:
    projects = sorted({
        Path(row["trace_path"]).parent.name.split("__", 1)[0]
        for row in rows
    })
    if len(projects) < 3:
        raise ValueError("JOIN load comparison requires three training projects")
    folds = {}
    for project in projects:
        train = [
            row for row in rows
            if not Path(row["trace_path"]).parent.name.startswith(project + "__")
        ]
        heldout = [
            row for row in rows
            if Path(row["trace_path"]).parent.name.startswith(project + "__")
        ]
        if len(train) < 20 or not heldout:
            folds[project] = {
                "status": "insufficient_independent_join_support",
                "train": len(train), "heldout": len(heldout),
            }
            continue
        prior = median(row["lead_ms"] for row in train)
        base = _predict(heldout, _fit(train))
        load_train = [{**row, "features": row["load_features"]} for row in train]
        load_test = [{**row, "features": row["load_features"]} for row in heldout]
        conditioned = _predict(load_test, _fit(load_train))
        gain = _paired_long_gain(
            [
                {
                    "workflow": row["trace_path"],
                    "duration_ms": row["lead_ms"],
                }
                for row in heldout
            ],
            np.asarray(base), np.asarray(conditioned),
        )
        folds[project] = {
            "status": "scored",
            "train": len(train), "heldout": len(heldout),
            "workflows": len({row["trace_path"] for row in heldout}),
            "metric_age_p50_ms": median(
                row["metric_age_ms"] for row in heldout
            ),
            "stream_only": _quality(heldout, base, prior),
            "stream_plus_load": _quality(heldout, conditioned, prior),
            "paired_vs_stream_only": gain,
        }
    return {
        "status": "training_project_loo_stream_asof_load_not_action_eligible",
        "stage_chars": STAGE_CHARS,
        "max_metric_age_ms": MAX_METRIC_AGE_MS,
        "training_projects": projects,
        "folds": folds,
        "scope": (
            "Snapshot timestamp is strictly earlier than the delayed stream "
            "trigger; no zero-filled, stale, or future metrics are used. "
            "Both heads score the same retrospectively natural, sole-pending "
            "JOINs, with each project's labels excluded from model fitting. "
            "Only the five existing stream features and current running/queue "
            "counts are used. This does not establish online finality, "
            "physical H2D readiness, or sealed project generalization."
        ),
    }


def evaluate(workflows: Path) -> dict:
    frozen, errors = require_complete_batch(workflows)
    joins, join_counts = collect_joins(workflows, STAGE_CHARS)
    streams, censored = samples(workflows, stage_chars=STAGE_CHARS)
    matched, alignment = align(joins, streams)
    observed = {
        path.parent.name for path in workflows.glob(
            "*/runtime_events.deepagents.jsonl"
        )
    }
    if not observed <= set(frozen):
        raise ValueError("JOIN trace identities differ from frozen manifest")
    metrics_path = workflows.parent / "sglang_metrics.jsonl"
    with metrics_path.open(encoding="utf-8") as stream:
        metrics = [json.loads(line) for line in stream if line.strip()]
    joined, excluded = attach_asof(matched, metrics)
    report = evaluate_rows(joined)
    report.update({
        "frozen_workflows": len(frozen),
        "runner_errors": errors,
        "join_collector": join_counts,
        "stream_alignment": alignment,
        "stream_censored": censored,
        "asof_matched": len(joined),
        "asof_excluded": excluded,
    })
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = evaluate(args.workflows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
