#!/usr/bin/env python3
"""Read-only project-disjoint ETA for a causally sole-pending JOIN."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_cold_tool_project_loo import require_complete_batch
from scripts.evaluate_join_rolling_stage import collect as collect_joins
from scripts.pilot_stream_dynamic_eta import (
    STAGE_DELAYS_MS, _fit, _paired_gain, _predict, _quality, samples,
)


STAGE_CHARS = 1700
DELAY_MS = STAGE_DELAYS_MS[STAGE_CHARS]


def align(
    join_rows: list[dict], stream_rows: list[dict],
) -> tuple[list[dict], dict[str, int]]:
    by_request = defaultdict(list)
    for row in stream_rows:
        key = (
            Path(row["trace_path"]).parent.name, row["child"],
            row["request_id"], row["join_id"],
        )
        by_request[key].append(row)

    matched = []
    counts = Counter(stage_candidates=len(join_rows))
    for join in join_rows:
        if join["group_label"] != "natural" or join["label"] != "true":
            counts[f"non_natural_{join['group_label']}_{join['label']}"] += 1
            continue
        counts["natural_join_candidates"] += 1
        key = (
            join["task_id"], join["invocation_id"],
            join["request_id"], join["join_id"],
        )
        live = [
            row for row in by_request.get(key, ())
            if abs(
                row["trigger_ms"] - DELAY_MS - join["signal_ts_ms"]
            ) <= 1
        ]
        if not live:
            counts["no_live_trigger_after_delay"] += 1
            continue
        if len(live) != 1 or not live[0]["final"]:
            raise ValueError("natural JOIN has no unique final stream request")
        stream = live[0]
        join_ts_ms = join["signal_ts_ms"] + join["join_lead_ms"]
        return_ts_ms = stream["trigger_ms"] + stream["lead_ms"]
        if join_ts_ms + 1 < return_ts_ms:
            raise ValueError("JOIN satisfies before final child RETURN")
        lead_ms = join_ts_ms - stream["trigger_ms"]
        if lead_ms <= 0:
            counts["join_before_delayed_trigger"] += 1
            continue
        counts["matched_natural_joins"] += 1
        matched.append({
            **stream, "lead_ms": lead_ms,
            "return_to_join_ms": join_ts_ms - return_ts_ms,
        })
    return matched, dict(counts)


def _join_quality(rows: list[dict], predicted: list[float], prior: float) -> dict:
    report = _quality(rows, predicted, prior)
    report["natural_joins"] = report.pop("natural_returns")
    report["return_to_join_p50_ms"] = (
        median(row["return_to_join_ms"] for row in rows) if rows else None
    )
    return report


def evaluate(train_root: Path, heldout_root: Path) -> dict:
    train_ids, train_errors = require_complete_batch(train_root)
    heldout_ids, heldout_errors = require_complete_batch(heldout_root)
    train_projects = {item.split("__", 1)[0] for item in train_ids}
    heldout_projects = {item.split("__", 1)[0] for item in heldout_ids}
    if (
        not train_projects or not heldout_projects
        or train_projects & heldout_projects
        or set(train_ids) & set(heldout_ids)
    ):
        raise ValueError("JOIN ETA requires disjoint tasks and projects")

    train_joins, train_join_counts = collect_joins(train_root, STAGE_CHARS)
    heldout_joins, heldout_join_counts = collect_joins(
        heldout_root, STAGE_CHARS,
    )
    train_streams, train_stream_censored = samples(
        train_root, stage_chars=STAGE_CHARS,
    )
    heldout_streams, heldout_stream_censored = samples(
        heldout_root, stage_chars=STAGE_CHARS,
    )
    train, train_alignment = align(train_joins, train_streams)
    heldout, heldout_alignment = align(heldout_joins, heldout_streams)
    if len(train) < 20:
        raise ValueError("insufficient naturally satisfied training JOINs")
    model = _fit(train)
    prior = median(row["lead_ms"] for row in train)
    predicted = _predict(heldout, model)
    by_project = {}
    for project in sorted(heldout_projects):
        selected = [
            (row, estimate) for row, estimate in zip(heldout, predicted)
            if Path(row["trace_path"]).parent.name.startswith(project + "__")
        ]
        rows = [row for row, _ in selected]
        estimates = [estimate for _, estimate in selected]
        by_project[project] = {
            "frozen_tasks": sum(
                item.startswith(project + "__") for item in heldout_ids
            ),
            "quality": _join_quality(rows, estimates, prior),
            "paired_gain": _paired_gain(rows, estimates, prior),
        }
    return {
        "status": "read_only_sole_pending_join_eta_not_action_eligible",
        "stage_chars": STAGE_CHARS,
        "stage_delay_ms": DELAY_MS,
        "train_projects": sorted(train_projects),
        "heldout_projects": sorted(heldout_projects),
        "train_frozen_workflows": len(train_ids),
        "heldout_frozen_workflows": len(heldout_ids),
        "train_runner_errors": train_errors,
        "heldout_runner_errors": heldout_errors,
        "train_join_collector": train_join_counts,
        "heldout_join_collector": heldout_join_counts,
        "train_alignment": train_alignment,
        "heldout_alignment": heldout_alignment,
        "train_stream_censored": train_stream_censored,
        "heldout_stream_censored": heldout_stream_censored,
        "train_prior_ms": prior,
        "heldout": _join_quality(heldout, predicted, prior),
        "heldout_paired_gain": _paired_gain(heldout, predicted, prior),
        "heldout_by_project": by_project,
        "scope": (
            "Only the first authenticated stream stage while the parent waits "
            "and exactly one JOIN member is pending. The same final child "
            "request, JOIN identity, and stage timestamp must match. The label "
            "is JOIN_SATISFIED after the delayed trigger, not child RETURN. "
            "Training and evaluation projects do not overlap, but natural "
            "JOIN and final-stream membership are selected retrospectively; "
            "unmatched, canceled, and nonfinal candidates remain in coverage "
            "counts. No online terminal classifier, physical H2D, or throughput "
            "benefit is established."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", required=True, type=Path)
    parser.add_argument("--heldout-workflows", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = evaluate(args.train_workflows, args.heldout_workflows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
