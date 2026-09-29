#!/usr/bin/env python3
"""Offline project-held-out RETURN ETA from delivered child text and GPU service."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

import numpy as np
import orjson

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_child_return_intent_timing import _metrics
from scripts.pilot_child_stream_content import phase_features
from scripts.pilot_child_stream_service_progress import service_rows


def _notices(workflows: Path) -> dict[tuple[str, str], list[tuple[float, int]]]:
    by_child: dict[tuple[str, str], list[tuple[float, int]]] = {}
    for path in workflows.glob("*/runtime_events.deepagents.jsonl"):
        with path.open("rb") as stream:
            for line in stream:
                event = orjson.loads(line)
                attrs = event.get("attributes") or {}
                estimate = attrs.get("estimated_final_report_tokens")
                if (
                    event.get("kind") != "structured_action"
                    or attrs.get("child_completion_signal_kind") != "stage"
                    or attrs.get("beliefkv_child_completion_intent") is not True
                    or type(estimate) is not int or estimate <= 0
                ):
                    continue
                key = (path.parent.name, event["invocation_id"])
                by_child.setdefault(key, []).append(
                    (float(event["ts_ms"]), estimate)
                )
    for values in by_child.values():
        values.sort()
    return by_child


def _latest_notice(
    notices: dict[tuple[str, str], list[tuple[float, int]]],
    task: str, child: str | None, when: float,
) -> tuple[float, int] | None:
    return next(
        (notice for notice in reversed(notices.get((task, child), ()))
         if notice[0] <= when),
        None,
    )


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


def evaluate_rows(
    rows: list[dict], *, min_chars: int = 0,
    notices: dict[tuple[str, str], list[tuple[float, int]]] | None = None,
) -> dict:
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
        notice = _latest_notice(
            notices or {}, row["task"], row.get("invocation_id"), snap["ts_ms"],
        )
        progress = list(snap["decode_features"])
        size = [np.log1p(snap["content_chars"])]
        notice_features = [
            int(notice is not None),
            notice[1] if notice else 0,
            max(0., snap["ts_ms"] - notice[0]) if notice else 0.,
        ]
        samples.append({
            "project": row["project"], "task": row["task"],
            "label": row["label"], "remaining_ms": remaining,
            "notice_seen": notice is not None,
            "size": size,
            "service": size + progress[:3],
            "notice": size + progress[:3] + notice_features,
            "semantic": size + progress + list(phase),
            "joint": size + progress + notice_features + list(phase),
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
        for key in ("size", "service", "notice", "semantic", "joint"):
            predictions[key] = (
                _predict(returns, held_returns, key) if held_returns else []
            )
        results[project] = {
            "train_return_requests": len(returns),
            "held_return_requests": len(held_returns),
            "held_tool_requests": sum(row["label"] == "tool" for row in held),
            "return_lead_at_least_500ms": sum(value >= 500 for value in actual),
            "return_lead_p50_ms": (
                float(np.median(actual)) if actual else None
            ),
            "return_lead_p90_ms": (
                float(np.quantile(actual, .9)) if actual else None
            ),
            "terminal_screen": {
                key: _terminal_screen(train, held, key)
                for key in ("size", "service", "notice", "semantic", "joint")
            },
            "eta": {
                key: _metrics(actual, value)
                for key, value in predictions.items()
            },
        }
    leads = [
        row["remaining_ms"] for row in samples if row["label"] == "return"
    ]
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
        "notice_seen": sum(row["notice_seen"] for row in samples),
        "notice_seen_on_returns": sum(
            row["notice_seen"] and row["label"] == "return"
            for row in samples
        ),
        "return_lead_p50_ms": float(np.median(leads)) if leads else None,
        "return_lead_p90_ms": float(np.quantile(leads, .9)) if leads else None,
        "return_lead_at_least_1000ms": sum(lead >= 1000 for lead in leads),
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
    notices = _notices(clients[0])
    report = evaluate_rows(rows, notices=notices)
    report["rolling_checkpoints"] = {
        str(threshold): evaluate_rows(
            rows, min_chars=threshold, notices=notices,
        )
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
