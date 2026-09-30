#!/usr/bin/env python3
"""Offline, nested project-held-out child RETURN ETA candidate comparison."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

import numpy as np
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_child_return_content_service import (
    _latest_notice,
    _metrics,
    _notices,
    evaluate_rows,
)
from scripts.pilot_child_stream_content import phase_features
from scripts.pilot_child_stream_service_progress import service_rows


CHECKPOINTS = (0, 128, 512, 1024)
CANDIDATES = (
    "log_speed", "wall_speed", "log_stage", "wall_stage",
    "huber_stage", "wall_rolling", "physical_length", "nearest_stage",
    "log_history", "wall_history",
)


def candidates_at_checkpoint(
    rows: list[dict],
    notices: dict[tuple[str, str], list[tuple[float, int]]],
    threshold: int,
) -> list[dict]:
    samples = []
    for row in rows:
        if row["label"] != "return":
            continue
        snaps = row["snapshots"]
        position = next(
            (i for i, snap in enumerate(snaps)
             if snap["content_chars"] >= threshold),
            None,
        )
        if position is None:
            continue
        snap = snaps[position]
        remaining = float(row["return_ts"]) - float(snap["ts_ms"])
        if remaining <= 0:
            continue
        notice = _latest_notice(
            notices, row["task"], row["invocation_id"], snap["ts_ms"],
        )
        tokens, rate, age, heading = snap["decode_features"]
        tokens, rate, age = (float(np.expm1(v)) for v in (tokens, rate, age))
        hint = notice[1] if notice else 0
        prior = snaps[position - 1] if position else None
        elapsed = snap["ts_ms"] - prior["ts_ms"] if prior else 0.
        progress = snap["content_chars"] - prior["content_chars"] if prior else 0
        base = [
            np.log1p(snap["content_chars"]),
            np.log1p(tokens),
            np.log1p(rate),
            np.log1p(age),
        ]
        stage = base + [
            float(notice is not None),
            np.log1p(hint),
            np.log1p(max(0., hint - tokens)),
            np.log1p(max(0., snap["ts_ms"] - notice[0]))
            if notice else 0.,
            np.log1p(max(0., hint - tokens) / max(rate, 1.)),
        ]
        rolling = stage + [
            np.log1p(max(0., elapsed)),
            np.log1p(max(0, progress)),
            np.log1p(max(0., progress * 1000 / max(elapsed, 1.))),
            *phase_features([{**row, "snapshots": snaps[:position + 1]}])[-1],
            heading,
        ]
        history = stage + list(snap.get("decode_history_features", (0., 0., 0.)))
        samples.append({
            "project": row["project"],
            "task": row["task"],
            "request_id": row["rid"],
            "invocation_id": row["invocation_id"],
            "checkpoint": threshold,
            "snapshot_ts_ms": snap["ts_ms"],
            "actual_remaining_ms": remaining,
            "notice_seen": notice is not None,
            "base": base,
            "stage": stage,
            "rolling": rolling,
            "history": history,
            "physical": stage[0:4] + [stage[-1], stage[4]],
        })
    return samples


def predict(train: list[dict], held: list[dict], name: str) -> np.ndarray:
    if not held:
        return np.asarray([])
    if name.endswith("_history"):
        key = "history"
    elif name == "nearest_stage":
        key = "stage"
    elif name == "physical_length":
        key = "physical"
    elif name == "wall_rolling":
        key = "rolling"
    elif name.endswith("_stage"):
        key = "stage"
    else:
        key = "base"
    x = np.asarray([row[key] for row in train], dtype=float)
    z = np.asarray([row[key] for row in held], dtype=float)
    center = x.mean(axis=0)
    scale = np.maximum(x.std(axis=0), 0.25)
    x, z = (x - center) / scale, (z - center) / scale
    actual = np.asarray([row["actual_remaining_ms"] for row in train])
    task_counts = Counter(row["task"] for row in train)
    weights = np.asarray([1. / task_counts[row["task"]] for row in train])
    weights /= weights.sum()
    if name == "nearest_stage":
        distances = np.sqrt(np.mean((z[:, None, :] - x[None, :, :]) ** 2, axis=2))
        return np.asarray([
            np.average(actual[np.argsort(dist)[:5]], weights=(
                weights[np.argsort(dist)[:5]]
                / (0.2 + dist[np.argsort(dist)[:5]])
            ))
            for dist in distances
        ])
    log_target = name.startswith("log_")
    y = np.log1p(actual) if log_target else actual / 1000.
    prior = float(np.median(y))
    if name == "huber_stage":
        def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
            error = prior + x @ beta - y
            clipped = np.clip(error, -4., 4.)
            loss = np.where(
                abs(error) <= 4., 0.5 * error ** 2,
                4. * (abs(error) - 2.),
            )
            return (
                float(weights @ loss + 0.3 * (beta @ beta)),
                x.T @ (weights * clipped) + 0.6 * beta,
            )
        fitted = minimize(
            objective, np.zeros(x.shape[1]), method="L-BFGS-B", jac=True,
        )
        if not fitted.success:
            raise RuntimeError(f"Huber fit failed: {fitted.message}")
        beta = fitted.x
    else:
        alpha = 0.25 if name.startswith("log_") else 0.45
        if name == "physical_length":
            alpha = 0.1
        beta = np.linalg.solve(
            x.T @ (weights[:, None] * x) + alpha * np.eye(x.shape[1]),
            x.T @ (weights * (y - prior)),
        )
    prediction = prior + z @ beta
    return np.maximum(
        0., np.expm1(prediction) if log_target else prediction * 1000.,
    )


def _nested_choice(train: list[dict], current_train: list[dict]) -> str:
    projects = sorted({row["project"] for row in current_train})
    scores: dict[str, list[float]] = {name: [] for name in CANDIDATES}
    for project in projects:
        inner_train = [row for row in train if row["project"] != project]
        inner_held = [
            row for row in current_train if row["project"] == project
        ]
        if len(inner_train) < 3 or len({r["project"] for r in inner_train}) < 2:
            continue
        actual = np.asarray([row["actual_remaining_ms"] for row in inner_held])
        for name in CANDIDATES:
            scores[name].extend(
                abs(predict(inner_train, inner_held, name) - actual),
            )
    if not any(scores.values()):
        raise ValueError("nested selection needs at least three training projects")
    return min(
        CANDIDATES,
        key=lambda name: (float(np.mean(scores[name])), CANDIDATES.index(name)),
    )


def _paired_workflow_bootstrap(
    rows: list[dict], name: str, *, draws: int = 10_000,
) -> dict | None:
    if not rows:
        return None
    tasks = sorted({row["task"] for row in rows})
    differences, counts = [], []
    for task in tasks:
        group = [row for row in rows if row["task"] == task]
        differences.append(sum(
            abs(row["signed_error_ms"]["original_joint"])
            - abs(row["signed_error_ms"][name])
            for row in group
        ))
        counts.append(len(group))
    rng = np.random.default_rng(42)
    sample = rng.integers(0, len(tasks), size=(draws, len(tasks)))
    differences = np.asarray(differences)
    counts = np.asarray(counts)
    sampled = differences[sample].sum(axis=1) / counts[sample].sum(axis=1)
    return {
        "workflow_count": len(tasks),
        "paired_mae_gain_ms": float(differences.sum() / counts.sum()),
        "workflow_bootstrap_95pct_ci_ms": [
            float(np.quantile(sampled, value)) for value in (.025, .975)
        ],
    }


def evaluate(
    rows: list[dict],
    notices: dict[tuple[str, str], list[tuple[float, int]]],
    extra_training: list[tuple[list[dict], dict]] | None = None,
) -> tuple[dict, list[dict]]:
    report = {"status": "offline_development_not_online_eligible",
              "checkpoints": {}}
    prediction_rows = []
    for threshold in CHECKPOINTS:
        baseline_rows: list[dict] = []
        baseline_report = evaluate_rows(
            rows, min_chars=threshold, notices=notices,
            prediction_rows=baseline_rows,
        )
        baseline = {
            row["request_id"]: row for row in baseline_rows
        }
        samples = candidates_at_checkpoint(rows, notices, threshold)
        extra = [
            row for extra_rows, extra_notices in (extra_training or ())
            for row in candidates_at_checkpoint(
                extra_rows, extra_notices, threshold,
            )
        ]
        if {row["request_id"] for row in samples} != set(baseline):
            raise ValueError("candidates and original baseline have different RETURNs")
        if {row["request_id"] for row in samples} & {
            row["request_id"] for row in extra
        }:
            raise ValueError("training-only requests overlap with evaluation requests")
        by_name: dict[str, list[float]] = {
            name: [] for name in (*CANDIDATES, "original_joint", "nested_choice")
        }
        actual, choices = [], {}
        for project in sorted({row["project"] for row in samples}):
            current_train = [
                row for row in samples if row["project"] != project
            ]
            train = [
                row for row in (*current_train, *extra)
                if row["project"] != project
            ]
            held = [row for row in samples if row["project"] == project]
            if len(train) < 3 or len({r["project"] for r in train}) < 2:
                raise ValueError(f"cannot hold out project {project}")
            selected = _nested_choice(train, current_train)
            choices[project] = selected
            fitted = {
                name: predict(train, held, name) for name in CANDIDATES
            }
            for index, row in enumerate(held):
                values = {
                    name: float(fitted[name][index]) for name in CANDIDATES
                }
                values["original_joint"] = float(
                    baseline[row["request_id"]]["joint_predicted_ms"]
                )
                values["nested_choice"] = values[selected]
                for name, value in values.items():
                    by_name[name].append(value)
                actual.append(row["actual_remaining_ms"])
                prediction_rows.append({
                    "checkpoint_chars": threshold,
                    "project": project,
                    "task": row["task"],
                    "invocation_id": row["invocation_id"],
                    "request_id": row["request_id"],
                    "snapshot_ts_ms": row["snapshot_ts_ms"],
                    "actual_remaining_ms": row["actual_remaining_ms"],
                    "notice_seen": row["notice_seen"],
                    "selected_model": selected,
                    "predicted_ms": values,
                    "signed_error_ms": {
                        name: value - row["actual_remaining_ms"]
                        for name, value in values.items()
                    },
                })
        if len(actual) != baseline_report["natural_returns"]:
            raise ValueError("candidate sample count differs from original")
        report["checkpoints"][str(threshold)] = {
            "count": len(actual),
            "notice_seen": sum(row["notice_seen"] for row in samples),
            "extra_training_samples": len(extra),
            "inner_choices_by_held_project": choices,
            "metrics": {
                name: _metrics(actual, predictions)
                for name, predictions in by_name.items()
            },
            "paired_vs_original_joint": _paired_workflow_bootstrap(
                [row for row in prediction_rows
                 if row["checkpoint_chars"] == threshold],
                "nested_choice",
            ),
        }
    return report, prediction_rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rows-output", type=Path, required=True)
    parser.add_argument("--extra-train-run", type=Path, action="append", default=[])
    args = parser.parse_args()
    clients = list(args.run.glob("client_*/workflows"))
    if len(clients) != 1:
        raise ValueError("expected exactly one client workflow root")
    rows, coverage = service_rows([clients[0]], [args.run])
    extra_training = []
    extra_coverage = {}
    for run in args.extra_train_run:
        roots = [
            root for root in (*run.glob("client_*/workflows"), run / "workloads/workflows")
            if root.is_dir()
        ]
        if len(roots) != 1:
            raise ValueError(f"expected exactly one extra training workflow root: {run}")
        extra_rows, extra_coverage[str(run)] = service_rows([roots[0]], [run])
        extra_training.append((extra_rows, _notices(roots[0])))
    report, prediction_rows = evaluate(
        rows, _notices(clients[0]), extra_training,
    )
    report["coverage"] = coverage
    report["extra_training_coverage"] = extra_coverage
    report["limitations"] = (
        "Only natural RETURN rounds with causally observed decode and delivered "
        "text; terminal identity is an oracle. Wall-clock labels include future "
        "GPU no-service gaps. Old runs are training-only and exclude the held "
        "project; their configurations may differ. Model candidates are "
        "exploratory; nested selection uses only other projects, not a sealed "
        "external test set."
    )
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    with args.rows_output.open("x", encoding="utf-8") as stream:
        for row in prediction_rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
