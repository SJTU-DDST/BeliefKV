#!/usr/bin/env python3
"""Project-disjoint first-trigger screen using only delivered stream content."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
from statistics import median
import sys
from typing import Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_child_hidden_trace import (
    blocked_child_invocations, index_workflow,
)


def collect(
    workflows: Path | Sequence[Path], *, min_snapshot_chars: int = 128,
    exclude_boundary_snapshots: bool = False,
) -> tuple[list[dict], Counter]:
    if min_snapshot_chars not in (32, 64, 128):
        raise ValueError("min_snapshot_chars must be 32, 64, or 128")
    rows: list[dict] = []
    counts: Counter = Counter()
    roots = (workflows,) if isinstance(workflows, Path) else workflows
    paths = sorted(
        path for root in roots
        for path in root.glob("*/child_stream_content.jsonl")
    )
    seen_tasks: set[str] = set()
    for path in paths:
        task = path.parent.name
        if task in seen_tasks:
            raise ValueError(f"workflow task collected more than once: {task}")
        seen_tasks.add(task)
        stats_path = path.parent / "child_stream_content_stats.json"
        if not stats_path.is_file():
            counts["unfinished_workflows"] += 1
            continue
        stats = json.loads(stats_path.read_text())
        if not stats["complete"]:
            counts["incomplete_workflows"] += 1
            continue
        project = task.split("__", 1)[0]
        with (path.parent / "runtime_events.deepagents.jsonl").open() as stream:
            events = [json.loads(line) for line in stream]
        terminal, join_last = index_workflow(
            events, blocked_invocations=blocked_child_invocations(path.parent),
        )
        results_by_rid = {
            (event.get("attributes") or {}).get("request_id"): event
            for event in events if event["kind"] == "llm_result"
        }
        returns_by_child = {
            event["invocation_id"]: float(event["ts_ms"])
            for event in events if event["kind"] == "return"
        }
        tools_by_child: dict[str, list[float]] = defaultdict(list)
        for event in events:
            if event["kind"] == "tool_start":
                tools_by_child[event["invocation_id"]].append(float(event["ts_ms"]))
        with path.open() as stream:
            observations = [json.loads(line) for line in stream]
        by_rid: dict[str, list[dict]] = defaultdict(list)
        for row in observations:
            by_rid[row["request_id"]].append(row)
        for rid, sequence in by_rid.items():
            results = [row for row in sequence if row["event"] == "child_stream_result"]
            if len(results) != 1:
                counts["censored_or_unfinished_rounds"] += 1
                continue
            result = results[0]
            native = results_by_rid.get(rid)
            if (
                native is None
                or native["invocation_id"] != result["invocation_id"]
                or native.get("context_id") != result["context_id"]
                or native.get("context_epoch") != result["context_epoch"]
            ):
                counts["result_identity_mismatch"] += 1
                continue
            child_id = result["invocation_id"]
            end_ts = returns_by_child.get(child_id, float("inf"))
            followed_by_tool = any(
                float(native["ts_ms"]) < ts < end_ts
                for ts in tools_by_child[child_id]
            )
            if result["tool_call_count"] or result["invalid_tool_call_count"] or followed_by_tool:
                label = "tool"
                counts["tool_rounds"] += 1
                counts[f"{project}_tool_rounds"] += 1
                if not result["tool_call_count"] and followed_by_tool:
                    counts["text_then_tool_rounds"] += 1
            elif rid in terminal and result["finish_reason"] == "stop":
                label = "return"
                counts["natural_return_rounds"] += 1
                counts[f"{project}_natural_return_rounds"] += 1
            else:
                counts["ambiguous_final_rounds"] += 1
                continue
            child, return_ts = terminal.get(rid, (None, None))
            snapshots = []
            last_chars = -1
            for row in sequence:
                if row["event"] != "child_stream_content" or row["tool_chunk"]:
                    continue
                if (
                    exclude_boundary_snapshots
                    and row.get("sampling_reason") == "content_boundary"
                ):
                    continue
                if (
                    row["content_chars"] < min_snapshot_chars
                    or row["content_chars"] == last_chars
                ):
                    continue
                if row["finish_reason"]:
                    continue
                if row["ts_ms"] >= result["ts_ms"]:
                    continue
                last_chars = row["content_chars"]
                snapshots.append(row)
                counts[f"snapshot_{row.get('sampling_reason', 'legacy')}"] += 1
            if not snapshots:
                counts[f"{label}_without_eligible_snapshot"] += 1
                counts[f"{project}_{label}_without_eligible_snapshot"] += 1
                continue
            rows.append({
                "task": task, "project": project, "rid": rid, "label": label,
                "join_last": child in join_last if child else False,
                "return_ts": return_ts, "snapshots": snapshots,
            })
    return rows, counts


def features(
    rows: list[dict], *, target_window_ms: float | None = None,
) -> tuple[list[str], np.ndarray, np.ndarray, list[int]]:
    texts, sizes, labels, call_index = [], [], [], []
    for index, row in enumerate(rows):
        for snap in row["snapshots"]:
            texts.append(snap["content_tail"])
            sizes.append([np.log1p(snap["content_chars"])])
            labels.append(int(
                row["label"] == "return" and (
                    target_window_ms is None
                    or 0 <= row["return_ts"] - snap["ts_ms"] <= target_window_ms
                )
            ))
            call_index.append(index)
    return texts, np.asarray(sizes, dtype=float).reshape(-1, 1), np.asarray(labels), call_index


def decode_progress(rows: list[dict]) -> np.ndarray:
    """Causal delivered-character progress; no final length or future time."""
    vectors = []
    for row in rows:
        first = row["snapshots"][0]
        previous = first
        for snap in row["snapshots"]:
            elapsed = max(snap["ts_ms"] - first["ts_ms"], 0)
            delta_ms = max(snap["ts_ms"] - previous["ts_ms"], 1)
            delta_chars = max(snap["content_chars"] - previous["content_chars"], 0)
            vectors.append((
                np.log1p(snap["content_chars"]),
                np.log1p(elapsed / 1000),
                np.log1p(1000 * delta_chars / delta_ms),
            ))
            previous = snap
    return np.asarray(vectors, dtype=float).reshape(-1, 3)


def text_features(
    train: list[str], held: list[str], train_workflows: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    if len(train) != len(train_workflows):
        raise ValueError("one workflow identity is required per training snapshot")

    def ngrams(text: str) -> Counter[str]:
        words = re.findall(r"[a-z_][a-z_0-9]*|[^\s]", text.lower())
        return Counter(
            words + [f"{left} {right}" for left, right in zip(words, words[1:])]
        )

    train_counts = [ngrams(text) for text in train]
    held_counts = [ngrams(text) for text in held]
    seen_by_workflow: dict[str, set[str]] = defaultdict(set)
    for workflow, row in zip(train_workflows, train_counts):
        seen_by_workflow[workflow].update(row)
    document_counts = Counter(
        token for seen in seen_by_workflow.values() for token in seen
    )
    vocab = {
        token: index for index, (token, count) in enumerate(
            sorted(
                ((token, count) for token, count in document_counts.items()
                 if count >= 2),
                key=lambda pair: (-pair[1], pair[0]),
            )[:1024]
        )
    }
    if not vocab:
        raise ValueError("not enough repeated training terms")

    def encode(rows: list[Counter[str]]) -> np.ndarray:
        matrix = np.zeros((len(rows), len(vocab)))
        for i, row in enumerate(rows):
            for token, count in row.items():
                if token in vocab:
                    matrix[i, vocab[token]] = np.log1p(count)
        norms = np.linalg.norm(matrix, axis=1)
        return matrix / np.maximum(norms[:, None], 1e-12)

    return encode(train_counts), encode(held_counts)


def length_conditioned_text(
    train_text: np.ndarray,
    held_text: np.ndarray,
    train_size: np.ndarray,
    held_size: np.ndarray,
    weights: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Remove the lexical variation explained by causal progress on train only."""
    train_basis = np.column_stack((np.ones(len(train_size)), train_size))
    held_basis = np.column_stack((np.ones(len(held_size)), held_size))
    ridge = np.diag([1e-8] + [1e-3] * train_size.shape[1])
    coefficients = np.linalg.solve(
        train_basis.T @ (weights[:, None] * train_basis) + ridge,
        train_basis.T @ (weights[:, None] * train_text),
    )
    return (
        train_text - train_basis @ coefficients,
        held_text - held_basis @ coefficients,
    )


def centroid_scores(
    train_x: np.ndarray,
    held_x: np.ndarray,
    train_y: np.ndarray,
    weights: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    positive = np.average(
        train_x[train_y == 1], axis=0, weights=weights[train_y == 1]
    )
    negative = np.average(
        train_x[train_y == 0], axis=0, weights=weights[train_y == 0]
    )
    direction = positive - negative
    return train_x @ direction, held_x @ direction


def evaluate(
    workflows: Path | Sequence[Path], heldout_project: str, *, min_snapshot_chars: int = 128,
    train_projects: tuple[str, ...] | None = None,
    calibration_projects: tuple[str, ...] = (),
    exclude_boundary_snapshots: bool = False,
) -> dict:
    rows, counts = collect(
        workflows, min_snapshot_chars=min_snapshot_chars,
        exclude_boundary_snapshots=exclude_boundary_snapshots,
    )
    if calibration_projects:
        if not train_projects or (
            set(train_projects) & (set(calibration_projects) | {heldout_project})
            or heldout_project in calibration_projects
        ):
            raise ValueError("train, calibration, and heldout projects must be disjoint")
    elif train_projects:
        raise ValueError("train_projects requires independent calibration_projects")
    train = [
        row for row in rows
        if row["project"] in train_projects
    ] if train_projects else [
        row for row in rows if row["project"] != heldout_project
    ]
    held = [row for row in rows if row["project"] == heldout_project]
    cal = [
        row for row in rows if row["project"] in calibration_projects
    ]
    if (
        {row["label"] for row in train} != {"return", "tool"}
        or not held or (calibration_projects and not cal)
    ):
        return {
            "status": "insufficient_project_disjoint_samples",
            "counts": dict(counts),
            "train_rounds": len(train), "calibration_rounds": len(cal),
            "heldout_rounds": len(held),
        }
    train_text, train_size, _, train_idx = features(train)
    held_text, held_size, _, held_idx = features(held)
    cal_text, cal_size, _, cal_idx = features(cal)
    train_progress = decode_progress(train)
    cal_progress = decode_progress(cal)
    held_progress = decode_progress(held)
    train_text_x, other_text_x = text_features(
        train_text, cal_text + held_text,
        [train[index]["task"] for index in train_idx],
    )
    cal_text_x = other_text_x[:len(cal_text)]
    held_text_x = other_text_x[len(cal_text):]
    size_mean, size_std = train_size.mean(), max(train_size.std(), 1e-9)
    progress_mean = train_progress.mean(axis=0)
    progress_std = np.maximum(train_progress.std(axis=0), 1e-9)
    cal_progress = (cal_progress - progress_mean) / progress_std
    held_progress = (held_progress - progress_mean) / progress_std
    train_progress = (train_progress - progress_mean) / progress_std
    cal_size = (cal_size - size_mean) / size_std
    held_size = (held_size - size_mean) / size_std
    train_size = (train_size - size_mean) / size_std
    task_counts = Counter(row["task"] for row in train)
    weights = np.asarray([
        1 / (
            task_counts[train[idx]["task"]] * len(train[idx]["snapshots"])
        ) for idx in train_idx
    ])
    train_text_residual, other_residual = length_conditioned_text(
        train_text_x, other_text_x, train_size,
        np.concatenate((cal_size, held_size)), weights,
    )
    cal_text_residual = other_residual[:len(cal_text)]
    held_text_residual = other_residual[len(cal_text):]
    train_progress_residual, other_progress_residual = length_conditioned_text(
        train_text_x, other_text_x, train_progress,
        np.concatenate((cal_progress, held_progress)), weights,
    )
    cal_progress_residual = other_progress_residual[:len(cal_text)]
    held_progress_residual = other_progress_residual[len(cal_text):]
    results = {}
    matrices = (
        ("size_only", train_size, cal_size, held_size),
        ("progress_only", train_progress, cal_progress, held_progress),
        ("delivered_tail_only", train_text_x, cal_text_x, held_text_x),
        ("delivered_tail_plus_size",
         np.column_stack((train_text_x, train_size)),
         np.column_stack((cal_text_x, cal_size)),
         np.column_stack((held_text_x, held_size))),
    )
    for mode, window in (("return_vs_tool", None), ("near_return_2000ms", 2000.0)):
        _, _, train_y, _ = features(train, target_window_ms=window)
        if len(set(train_y)) < 2:
            results[mode] = {"status": "insufficient_near_return_snapshots"}
            continue
        for name, x, x_cal, x_held in (
            *matrices,
            ("length_conditioned_content", train_text_residual,
             cal_text_residual, held_text_residual),
            ("progress_conditioned_content", train_progress_residual,
             cal_progress_residual, held_progress_residual),
        ):
            predicted_train, predicted_held = centroid_scores(
                x, x_held, train_y, weights
            )
            predicted_cal = centroid_scores(x, x_cal, train_y, weights)[1]
            if name in ("length_conditioned_content", "progress_conditioned_content"):
                baseline_train = (
                    train_progress if name == "progress_conditioned_content"
                    else train_size
                )
                baseline_cal = (
                    cal_progress if name == "progress_conditioned_content"
                    else cal_size
                )
                baseline_held = (
                    held_progress if name == "progress_conditioned_content"
                    else held_size
                )
                length_train, length_held = centroid_scores(
                    baseline_train, baseline_held, train_y, weights
                )
                length_cal = centroid_scores(
                    baseline_train, baseline_cal, train_y, weights
                )[1]
                # Fixed diagnostic weight; never select it on the held-out project.
                length_scale = max(float(length_train.std()), 1e-9)
                content_scale = max(float(predicted_train.std()), 1e-9)
                predicted_train = (
                    length_train / length_scale
                    + 0.5 * predicted_train / content_scale
                )
                predicted_held = (
                    length_held / length_scale
                    + 0.5 * predicted_held / content_scale
                )
                predicted_cal = (
                    length_cal / length_scale
                    + 0.5 * predicted_cal / content_scale
                )
            # Calibrated threshold. In near mode, early parts of eventual
            # RETURNs also count as false starts, alongside tool rounds.
            threshold_rows = cal if calibration_projects else train
            threshold_scores = predicted_cal if calibration_projects else predicted_train
            threshold_indices = cal_idx if calibration_projects else train_idx
            bad_max = [
                max(
                    (score for score, idx, snap in zip(
                        threshold_scores, threshold_indices,
                        (s for row in threshold_rows for s in row["snapshots"]),
                    ) if idx == j and (
                        row["label"] == "tool"
                        or (window is not None
                            and row["return_ts"] - snap["ts_ms"] > window)
                    )),
                    default=float("-inf"),
                )
                for j, row in enumerate(threshold_rows)
            ]
            bad_max = [value for value in bad_max if np.isfinite(value)]
            if not bad_max:
                raise ValueError("calibration set lacks false-start candidates")
            threshold = float(
                np.nextafter(np.quantile(bad_max, 0.95), 1.0)
            )
            triggered = defaultdict(list)
            for score, idx, snap in zip(
                predicted_held, held_idx,
                (s for row in held for s in row["snapshots"]),
            ):
                if score >= threshold:
                    triggered[idx].append(snap["ts_ms"])
            positives = [i for i, row in enumerate(held) if row["label"] == "return"]
            negatives = [i for i, row in enumerate(held) if row["label"] == "tool"]
            lead = [
                held[i]["return_ts"] - min(triggered[i])
                for i in positives if triggered[i]
            ]
            results[f"{mode}_{name}"] = {
                "threshold_selected_on_calibration" if calibration_projects
                else "threshold_selected_on_train": threshold,
                "train_bad_round_false_starts": int(sum(
                    score >= threshold for score in bad_max
                )),
                "threshold_bad_round_count": len(bad_max),
                "heldout_return_rounds": len(positives),
                "heldout_tool_rounds": len(negatives),
                "heldout_return_rounds_all": counts[
                    f"{heldout_project}_natural_return_rounds"
                ],
                "heldout_tool_rounds_all": counts[f"{heldout_project}_tool_rounds"],
                "heldout_triggered_return_rounds": len(lead),
                "heldout_tool_false_first_triggers": sum(
                    bool(triggered[i]) for i in negatives
                ),
                "heldout_join_last_rounds": sum(
                    held[i]["join_last"] for i in positives
                ),
                "heldout_join_last_triggered": sum(
                    bool(triggered[i]) and held[i]["join_last"] for i in positives
                ),
                "heldout_join_last_in_window": sum(
                    held[i]["join_last"]
                    and bool(triggered[i])
                    and 500 <= held[i]["return_ts"] - min(triggered[i]) <= 2000
                    for i in positives
                ),
                "heldout_window_oracle": sum(
                    any(
                        500 <= held[i]["return_ts"] - snap["ts_ms"] <= 2000
                        for snap in held[i]["snapshots"]
                    ) for i in positives
                ),
                "heldout_join_last_window_oracle": sum(
                    held[i]["join_last"] and any(
                        500 <= held[i]["return_ts"] - snap["ts_ms"] <= 2000
                        for snap in held[i]["snapshots"]
                    ) for i in positives
                ),
                "heldout_window_hits_by_workflow": dict(sorted(Counter(
                    held[i]["task"] for i in positives
                    if triggered[i]
                    and 500 <= held[i]["return_ts"] - min(triggered[i]) <= 2000
                ).items())),
                "lead_ms_median": median(lead) if lead else None,
                "lead_at_least_500ms": sum(t >= 500 for t in lead),
                "lead_between_500_and_2000ms": sum(
                    500 <= t <= 2000 for t in lead
                ),
                "lead_over_2000ms": sum(t > 2000 for t in lead),
                "lead_over_10000ms": sum(t > 10000 for t in lead),
            }
    return {
        "status": (
            "frozen_project_disjoint_calibrated_threshold"
            if calibration_projects
            else "diagnostic_project_disjoint_in_sample_threshold"
        ),
        "heldout_project": heldout_project,
        "min_snapshot_chars": min_snapshot_chars,
        "exclude_boundary_snapshots": exclude_boundary_snapshots,
        "counts": dict(counts),
        "train_projects": sorted({row["project"] for row in train}),
        "calibration_projects": sorted({row["project"] for row in cal}),
        "train_rounds": len(train),
        "calibration_rounds": len(cal),
        "heldout_rounds": len(held),
        "results": results,
        "limitations": (
            ("Threshold selected on disjoint calibration projects. "
             if calibration_projects else
             "Training threshold is selected in sample; no nested validation. ")
            + "Streamed-mode-only observations; no physical prefetch or H2D claim."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True, action="append")
    parser.add_argument("--heldout-project", required=True)
    parser.add_argument(
        "--train-projects", help="Comma-separated projects frozen before evaluation",
    )
    parser.add_argument(
        "--calibration-projects", help="Comma-separated projects for threshold only",
    )
    parser.add_argument(
        "--min-snapshot-chars", type=int, choices=(32, 64, 128), default=128,
    )
    parser.add_argument(
        "--exclude-boundary-snapshots", action="store_true",
        help="Compare against the same run with boundary-only snapshots removed",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(
        args.workflows, args.heldout_project,
        min_snapshot_chars=args.min_snapshot_chars,
        train_projects=(
            tuple(args.train_projects.split(",")) if args.train_projects else None
        ),
        calibration_projects=(
            tuple(args.calibration_projects.split(","))
            if args.calibration_projects else ()
        ),
        exclude_boundary_snapshots=args.exclude_boundary_snapshots,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
