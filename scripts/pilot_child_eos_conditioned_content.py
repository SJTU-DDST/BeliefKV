#!/usr/bin/env python3
"""Development-only project holdout: lexical/progress value at first EOS cue."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pilot_child_stream_content import (
    centroid_scores, decode_progress, length_conditioned_text, text_features,
)
from scripts.pilot_child_stream_eos_joint import (
    THRESHOLDS, load_rows, score,
)

MODES = ("eos_only", "progress_only", "progress_plus_content")


def candidates(rows: list[dict], threshold: str, sampled: bool) -> list[dict]:
    result = []
    for index, row in enumerate(rows):
        if sampled:
            floor = (
                float("-inf") if threshold == "top20"
                else math.log(float(threshold))
            )
            for position, snap in enumerate(row["snapshots"]):
                value = snap["eos_shadow_max_logprob_since_previous_snapshot"]
                if (
                    type(value) in (int, float) and math.isfinite(value)
                    and value >= floor
                ):
                    result.append({
                        **row, "snapshots": row["snapshots"][:position + 1],
                        "signal_ts": snap["ts_ms"], "source_index": index,
                    })
            continue
        ts = row["eos"].get(threshold)
        if ts is None:
            continue
        delivered = [snap for snap in row["snapshots"] if snap["ts_ms"] <= ts]
        if not delivered:
            continue
        result.append({
            **row, "snapshots": delivered, "signal_ts": ts,
            "source_index": index,
        })
    return result


def last_progress(rows: list[dict]) -> np.ndarray:
    if not rows:
        return np.empty((0, 3))
    ends = np.cumsum([len(row["snapshots"]) for row in rows]) - 1
    return decode_progress(rows)[ends]


def trained_scores(
    train: list[dict], held: list[dict],
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    labels = np.asarray([
        int(
            row["label"] == "return"
            and 500 <= row["return_ts"] - row["signal_ts"] <= 2000
        )
        for row in train
    ])
    if len(set(labels)) < 2:
        return {}
    train_progress = last_progress(train)
    held_progress = last_progress(held)
    mean = train_progress.mean(axis=0)
    std = np.maximum(train_progress.std(axis=0), 1e-9)
    train_progress = (train_progress - mean) / std
    held_progress = (held_progress - mean) / std
    request_counts = Counter((row["task"], row["rid"]) for row in train)
    task_counts = Counter(task for task, _ in request_counts)
    weights = np.asarray([
        1 / (task_counts[row["task"]] * request_counts[row["task"], row["rid"]])
        for row in train
    ])
    progress_train, progress_held = centroid_scores(
        train_progress, held_progress, labels, weights
    )
    lexical_train, lexical_held = text_features(
        [row["snapshots"][-1]["content_tail"] for row in train],
        [row["snapshots"][-1]["content_tail"] for row in held],
        [row["task"] for row in train],
    )
    residual_train, residual_held = length_conditioned_text(
        lexical_train, lexical_held,
        train_progress, held_progress, weights,
    )
    lexical_train_score, lexical_held_score = centroid_scores(
        residual_train, residual_held, labels, weights
    )
    progress_scale = max(float(progress_train.std()), 1e-9)
    lexical_scale = max(float(lexical_train_score.std()), 1e-9)
    return {
        "progress_only": (progress_train, progress_held),
        "progress_plus_content": (
            progress_train / progress_scale
            + 0.5 * lexical_train_score / lexical_scale,
            progress_held / progress_scale
            + 0.5 * lexical_held_score / lexical_scale,
        ),
    }


def gated_metrics(
    rows: list[dict], matched: list[dict], signals: np.ndarray,
    threshold: str, cutoff: float,
) -> dict:
    triggered = {}
    for row, value in zip(matched, signals):
        if value >= cutoff:
            triggered.setdefault(row["source_index"], row["signal_ts"])
    return score([
        {
            **row, "eos": (
                {threshold: triggered[index]} if index in triggered else {}
            ),
        }
        for index, row in enumerate(rows)
    ], threshold)


def evaluate(workflows: list[Path], heldout_project: str) -> dict:
    rows, counts, manifests, sampled = load_rows(workflows)
    train = [row for row in rows if row["project"] != heldout_project]
    held = [row for row in rows if row["project"] == heldout_project]
    if len({row["project"] for row in train}) < 2 or not held:
        raise ValueError("at least two training projects and one heldout project required")
    results = {}
    for threshold in THRESHOLDS:
        train_candidates = candidates(train, threshold, sampled)
        held_candidates = candidates(held, threshold, sampled)
        if not train_candidates:
            continue
        results[f"{threshold}|eos_only"] = {
            "train": gated_metrics(
                train, train_candidates, np.ones(len(train_candidates)),
                threshold, 0.5,
            ),
            "heldout": gated_metrics(
                held, held_candidates, np.ones(len(held_candidates)),
                threshold, 0.5,
            ),
            "train_cutoff": None,
        }
        trained = trained_scores(train_candidates, held_candidates)
        for mode, (train_scores, held_scores) in trained.items():
            bad_by_request = {}
            for row, value in zip(train_candidates, train_scores):
                if (
                    row["label"] == "tool"
                    or row["return_ts"] - row["signal_ts"] > 2000
                ):
                    index = row["source_index"]
                    bad_by_request[index] = max(
                        bad_by_request.get(index, float("-inf")), float(value),
                    )
            bad = list(bad_by_request.values())
            if len(bad) < 10:
                continue
            cutoff = float(np.nextafter(np.quantile(bad, 0.95), np.inf))
            results[f"{threshold}|{mode}"] = {
                "train": gated_metrics(
                    train, train_candidates, train_scores, threshold, cutoff,
                ),
                "heldout": gated_metrics(
                    held, held_candidates, held_scores, threshold, cutoff,
                ),
                "train_cutoff": cutoff,
            }

    selected = {}
    for mode in MODES:
        viable = [
            (key, value)
            for key, value in results.items()
            if key.endswith(f"|{mode}")
            and value["train"]["return_rounds"] >= 10
            and value["train"]["tool_rounds_with_content"] >= 20
            and value["train"]["first_triggered_tool"]
            / value["train"]["tool_rounds_with_content"] <= 0.05
            and value["train"]["return_trigger_early_over_2000ms"]
            / value["train"]["return_rounds"] <= 0.1
        ]
        if viable:
            key, _ = max(viable, key=lambda item: (
                item[1]["train"]["return_trigger_500_to_2000ms"],
                -item[1]["train"]["first_triggered_tool"],
                -item[1]["train"]["return_trigger_early_over_2000ms"],
                THRESHOLDS.index(item[0].split("|")[0]),
            ))
            selected[mode] = {"policy": key, **results[key]}
        else:
            selected[mode] = None
    return {
        "scope": (
            "development only; thresholds and lexical direction selected on "
            "training projects in-sample; no independent calibration or H2D"
        ),
        "heldout_project": heldout_project,
        "train_projects": sorted({row["project"] for row in train}),
        "eos_evidence": (
            "each delivered snapshot interval with an EOS candidate; "
            "only the first accepted snapshot per request counts" if sampled
            else "first threshold crossing only"
        ),
        "risk_cutoff_scope": (
            "request-level maximum score on tool rounds and early RETURN "
            "snapshots; no heldout threshold fitting"
        ),
        "manifests": manifests,
        "counts": dict(counts),
        "selected": selected,
        "train_policy_scores": {
            key: value["train"] for key, value in results.items()
        },
        "heldout_policy_scores": {
            key: value["heldout"] for key, value in results.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, action="append", required=True)
    parser.add_argument("--heldout-project", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args.workflows, args.heldout_project)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "heldout_project": report["heldout_project"],
        "selected": report["selected"],
    }, indent=2))


if __name__ == "__main__":
    main()
