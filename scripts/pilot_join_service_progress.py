#!/usr/bin/env python3
"""Training-only JOIN stream ETA using causally prior per-request service."""

from __future__ import annotations

import argparse
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

from scripts.evaluate_cold_tool_project_loo import require_complete_batch
from scripts.evaluate_cold_tool_structure_holdout import _paired_long_gain
from scripts.evaluate_join_stream_eta import STAGE_CHARS, align, collect_joins
from scripts.pilot_stream_dynamic_eta import _fit, _predict, _quality, samples


CLOCK_GUARD_MS = 100.
MAX_CLOCK_BRACKET_MS = 200.
PROGRESS_WINDOW_MS = 2000.


def clock_bracket(
    workflows: Path, server_events: Path,
) -> tuple[float, float, dict]:
    by_kind = {"llm_submit": {}, "llm_result": {}}
    with server_events.open("rb") as stream:
        for line in stream:
            event = orjson.loads(line)
            kind = event.get("kind")
            if kind in by_kind:
                by_kind[kind][event["attributes"]["request_id"]] = float(
                    event["ts_ms"]
                )
    offsets: dict[str, list[float]] = {
        "llm_submit": [], "llm_result": [],
    }
    for path in sorted(workflows.glob("*/runtime_events.deepagents.jsonl")):
        with path.open("rb") as stream:
            for line in stream:
                event = orjson.loads(line)
                kind = event.get("kind")
                if kind not in offsets:
                    continue
                request_id = (event.get("attributes") or {}).get("request_id")
                if request_id in by_kind[kind]:
                    offsets[kind].append(
                        by_kind[kind][request_id] - float(event["ts_ms"])
                    )
    if min(map(len, offsets.values())) < 100:
        raise ValueError("insufficient paired submit/result clock anchors")
    lower = max(offsets["llm_result"])
    upper = min(offsets["llm_submit"])
    if not (0 <= upper - lower <= MAX_CLOCK_BRACKET_MS):
        raise ValueError("client/server clock bracket is too wide or inverted")
    return lower, upper, {
        "submit_pairs": len(offsets["llm_submit"]),
        "result_pairs": len(offsets["llm_result"]),
        "offset_lower_ms": lower,
        "offset_upper_ms": upper,
        "width_ms": upper - lower,
        "guard_ms": CLOCK_GUARD_MS,
    }


def attach_progress(
    rows: list[dict], audit: Path, lower_offset_ms: float,
) -> tuple[list[dict], dict]:
    if not math.isfinite(lower_offset_ms):
        raise ValueError("clock offset must be finite")
    watched = defaultdict(list)
    for row in rows:
        watched[row["request_id"]].append(row)
    cutoff_by_row = {
        id(row): row["trigger_ms"] + lower_offset_ms - CLOCK_GUARD_MS
        for row in rows
    }
    progress: dict[int, list[tuple[float, int]]] = defaultdict(list)
    matched_service_events = 0
    with audit.open("rb") as stream:
        for line in stream:
            event = orjson.loads(line)
            if (
                event.get("event") != "gpu_service_sample"
                or event.get("phase") != "decode"
            ):
                continue
            when = float(event["ts_ms"])
            for sample in event.get("request_samples") or ():
                request_id = sample.get("request_id")
                if request_id not in watched:
                    continue
                if sample.get("token_delta_semantics") != "observed_output_ids_delta":
                    continue
                before = sample.get("output_tokens_before")
                delta = sample.get("token_delta")
                if not all(
                    type(value) is int and value >= 0
                    for value in (before, delta)
                ):
                    continue
                for row in watched[request_id]:
                    cutoff = cutoff_by_row[id(row)]
                    if cutoff - PROGRESS_WINDOW_MS <= when < cutoff:
                        progress[id(row)].append((when, before + delta))
                        matched_service_events += 1
    selected = []
    excluded = Counter()
    for row in rows:
        events = sorted(progress[id(row)])
        if len(events) < 2:
            excluded["insufficient_prior_decode_samples"] += 1
            continue
        first, last = events[0], events[-1]
        duration = last[0] - first[0]
        if duration <= 0 or last[1] < first[1]:
            excluded["invalid_decode_progress"] += 1
            continue
        cutoff = cutoff_by_row[id(row)]
        rate = (last[1] - first[1]) * 1000. / duration
        selected.append({
            **row,
            "service_features": [
                *row["features"],
                math.log1p(last[1]),
                math.log1p(rate),
                math.log1p(cutoff - last[0]),
            ],
            "service_sample_count": len(events),
            "service_age_ms": cutoff - last[0],
        })
    return selected, {
        "candidates": len(rows),
        "matched_service_events": matched_service_events,
        "supported": len(selected),
        "excluded": dict(excluded),
        "service_age_p50_ms": (
            median(row["service_age_ms"] for row in selected)
            if selected else None
        ),
    }


def evaluate_rows(rows: list[dict]) -> dict:
    projects = sorted({
        Path(row["trace_path"]).parent.name.split("__", 1)[0]
        for row in rows
    })
    if len(projects) < 3:
        raise ValueError("service progress requires three training projects")
    folds = {}
    for project in projects:
        train = [
            row for row in rows
            if not Path(row["trace_path"]).parent.name.startswith(project + "__")
        ]
        test = [
            row for row in rows
            if Path(row["trace_path"]).parent.name.startswith(project + "__")
        ]
        if len(train) < 20:
            folds[project] = {
                "status": "insufficient_train_support", "heldout": len(test),
            }
            continue
        prior = median(row["lead_ms"] for row in train)
        baseline = _predict(test, _fit(train))
        augmented = _predict(
            [{**row, "features": row["service_features"]} for row in test],
            _fit([
                {**row, "features": row["service_features"]}
                for row in train
            ]),
        )
        paired = _paired_long_gain(
            [
                {
                    "workflow": row["trace_path"],
                    "duration_ms": row["lead_ms"],
                }
                for row in test
            ],
            np.asarray(baseline), np.asarray(augmented),
        )
        folds[project] = {
            "status": "scored",
            "heldout": len(test),
            "workflows": len({row["trace_path"] for row in test}),
            "stream_only": _quality(test, baseline, prior),
            "stream_plus_service": _quality(test, augmented, prior),
            "paired_vs_stream_only": paired,
        }
    return {
        "status": "training_loo_asof_request_service_not_online",
        "training_projects": projects,
        "folds": folds,
        "scope": (
            "Only the project's pre-trigger per-request decode observations "
            "are features. Client/server submit and result pairs bracket the "
            "clock offset; the service cutoff uses the lower bound minus "
            "100 ms, never a rounded or mean submit offset. The same "
            "retrospectively natural sole-pending JOIN candidates are scored "
            "by both heads. Server progress is not currently exposed to the "
            "online control plane; this is not H2D action eligibility."
        ),
    }


def evaluate(workflows: Path, server: Path) -> dict:
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
        raise ValueError("unexpected workflow trace identity")
    lower, _, clocks = clock_bracket(
        workflows, server / "runtime_events.sglang.jsonl",
    )
    selected, coverage = attach_progress(
        matched, server / "runtime_audit.jsonl", lower,
    )
    result = evaluate_rows(selected)
    result.update({
        "frozen_workflows": len(frozen),
        "runner_errors": errors,
        "join_collector": join_counts,
        "stream_alignment": alignment,
        "stream_censored": censored,
        "clock_bracket": clocks,
        "service_coverage": coverage,
    })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", required=True, type=Path)
    parser.add_argument("--server", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = evaluate(args.workflows, args.server)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
