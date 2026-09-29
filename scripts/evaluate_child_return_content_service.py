#!/usr/bin/env python3
"""Offline project-held-out RETURN ETA from delivered child text and GPU service."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_child_return_intent_timing import _metrics
from scripts.pilot_child_stream_content import phase_features
from scripts.pilot_child_stream_service_progress import service_rows


def _first_snapshots(rows: list[dict], min_chars: int = 0) -> list[dict]:
    # Each fixed threshold chooses its first crossing, never the best in hindsight.
    return [
        {**row, "snapshots": [snapshot]}
        for row in rows
        if (
            snapshot := next(
                (snap for snap in row["snapshots"]
                 if snap["content_chars"] >= min_chars),
                None,
            )
        ) is not None
    ]


def _predict(train: list[dict], held: list[dict], key: str) -> list[float]:
    x = np.asarray([row[key] for row in train], dtype=float)
    z = np.asarray([row[key] for row in held], dtype=float)
    x = np.sign(x) * np.log1p(np.abs(x))
    z = np.sign(z) * np.log1p(np.abs(z))
    center = x.mean(axis=0)
    scale = np.maximum(x.std(axis=0), 1.)
    x, z = (x - center) / scale, (z - center) / scale
    y = np.log1p([row["remaining_ms"] for row in train])
    prior = float(np.median(y))
    task_counts = Counter(row["task"] for row in train)
    weights = np.asarray([1. / task_counts[row["task"]] for row in train])
    coefficients = np.linalg.solve(
        x.T @ (weights[:, None] * x) + 8. * np.eye(x.shape[1]),
        x.T @ (weights * (y - prior)),
    )
    return list(np.maximum(0., np.expm1(prior + z @ coefficients)))


def _terminal_screen(train: list[dict], held: list[dict], key: str) -> dict | None:
    positives = [row for row in train if row["label"] == "return"]
    negatives = [row for row in train if row["label"] == "tool"]
    if not positives or not negatives:
        return None
    x = np.asarray([row[key] for row in train], dtype=float)
    z = np.asarray([row[key] for row in held], dtype=float)
    center = x.mean(axis=0)
    scale = np.maximum(x.std(axis=0), 1.)
    x, z = (x - center) / scale, (z - center) / scale
    weights = np.asarray([
        1. / sum(other["task"] == row["task"] for other in train)
        for row in train
    ])
    mask = np.asarray([row["label"] == "return" for row in train])
    direction = (
        np.average(x[mask], axis=0, weights=weights[mask])
        - np.average(x[~mask], axis=0, weights=weights[~mask])
    )
    train_scores = x @ direction
    threshold = float(np.nextafter(
        np.quantile(train_scores[~mask], .95), float("inf")
    ))
    flagged = z @ direction >= threshold
    return {
        "train_tool_requests": len(negatives),
        "train_tool_false_positives": int(sum(train_scores[~mask] >= threshold)),
        "held_flagged": int(sum(flagged)),
        "held_return_hits": sum(
            bool(hit) and row["label"] == "return"
            for hit, row in zip(flagged, held)
        ),
        "held_tool_false_positives": sum(
            bool(hit) and row["label"] == "tool"
            for hit, row in zip(flagged, held)
        ),
        "held_return_hits_with_500ms_lead": sum(
            bool(hit) and row["label"] == "return"
            and row["remaining_ms"] >= 500
            for hit, row in zip(flagged, held)
        ),
    }


def evaluate_rows(rows: list[dict], *, min_chars: int = 0) -> dict:
    selected = _first_snapshots(rows, min_chars)
    phases = phase_features(selected)
    samples = []
    for row, phase in zip(selected, phases):
        snap = row["snapshots"][0]
        remaining = (
            float(row["return_ts"]) - float(snap["ts_ms"])
            if row["label"] == "return" else None
        )
        if remaining is not None and remaining <= 0:
            continue
        progress = list(snap["decode_features"])
        size = [np.log1p(snap["content_chars"])]
        samples.append({
            "project": row["project"], "task": row["task"],
            "label": row["label"], "remaining_ms": remaining,
            "size": size,
            "service": size + progress[:3],
            "semantic": size + progress + list(phase),
        })
    results = {}
    for project in sorted({row["project"] for row in samples}):
        train = [row for row in samples if row["project"] != project]
        held = [row for row in samples if row["project"] == project]
        returns = [row for row in train if row["label"] == "return"]
        held_returns = [row for row in held if row["label"] == "return"]
        if len(returns) < 3 or len({row["project"] for row in returns}) < 2:
            continue
        actual = [row["remaining_ms"] for row in held_returns]
        predictions = {
            "train_median": [float(np.median([
                row["remaining_ms"] for row in returns
            ]))] * len(held_returns)
        }
        for key in ("size", "service", "semantic"):
            predictions[key] = (
                _predict(returns, held_returns, key) if held_returns else []
            )
        results[project] = {
            "train_return_requests": len(returns),
            "held_return_requests": len(held_returns),
            "held_tool_requests": sum(row["label"] == "tool" for row in held),
            "return_lead_at_least_500ms": sum(value >= 500 for value in actual),
            "terminal_screen": {
                key: _terminal_screen(train, held, key)
                for key in ("size", "service", "semantic")
            },
            "eta": {
                key: _metrics(actual, value)
                for key, value in predictions.items()
            },
        }
    return {
        "status": "offline_development_not_online_eligible",
        "min_delivered_chars": min_chars,
        "scope": (
            "One first service-eligible, delivered-content snapshot per child "
            "request. Projects are disjoint between train and evaluation. "
            "ETA is measured on the client monotonic clock only for natural "
            "RETURN rounds (oracle terminal identity); tool rounds are counted "
            "as the nonterminal denominator, not silently treated as returns. "
            "The terminal screen is a training-only 95th-percentile tool-score "
            "threshold at the first snapshot, not a calibrated probability; "
            "tool rounds without an eligible text snapshot are excluded and "
            "reported in collection_coverage. Physical H2D benefit remains "
            "unmeasured. Semantic features are lightweight text cues, not "
            "an embedding or hidden-state model."
        ),
        "eligible_requests": len(samples),
        "natural_returns": sum(row["label"] == "return" for row in samples),
        "tool_rounds": sum(row["label"] == "tool" for row in samples),
        "project_holdouts": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    clients = list(args.run.glob("client_*/workflows"))
    if len(clients) != 1:
        raise ValueError("expected exactly one client workflow root")
    rows, coverage = service_rows([clients[0]], [args.run])
    report = evaluate_rows(rows)
    report["rolling_checkpoints"] = {
        str(threshold): evaluate_rows(rows, min_chars=threshold)
        for threshold in (128, 512, 1024)
    }
    report["collection_coverage"] = coverage
    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        if args.output.exists():
            raise FileExistsError(args.output)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
