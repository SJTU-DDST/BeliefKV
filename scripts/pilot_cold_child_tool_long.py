#!/usr/bin/env python3
"""Project-held-out cold child execute long-duration classifier diagnostic."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import sys

import lightgbm as lgb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.audit_repeated_tool_timing import _read_workflow


def _cold_child_calls(workflows: Path, *, min_projects: int = 3) -> list[dict]:
    calls = [
        row
        for path in sorted(workflows.glob("*/runtime_events.deepagents.jsonl"))
        for row in _read_workflow(path)
        if row["is_child"] is True
        and (row["previous"] is None or row["previous"][2] != "success")
        and isinstance(row["input_chars"], int)
    ]
    if len({row["project"] for row in calls}) < min_projects:
        raise ValueError(f"cold child calls need at least {min_projects} projects")
    return calls


def _matrix(rows: list[dict], vocabulary: dict[str, int]) -> np.ndarray:
    matrix = np.zeros((len(rows), 2), dtype=np.float32)
    for index, row in enumerate(rows):
        matrix[index, 0] = vocabulary.get(row["class"], -1)
        matrix[index, 1] = math.log1p(max(0, row["input_chars"]))
    return matrix


def transfer_pilot(train: list[dict], heldout: list[dict]) -> dict:
    train_projects = {row["project"] for row in train}
    heldout_projects = {row["project"] for row in heldout}
    if not train or not heldout or train_projects & heldout_projects:
        raise ValueError("nonempty project-disjoint train and heldout calls are required")
    labels = [row["duration_ms"] >= 2_000 for row in train]
    if sum(labels) < 20 or len(labels) - sum(labels) < 20:
        raise ValueError("insufficient long/short train calls")
    vocabulary = {
        name: i for i, name in enumerate(sorted({row["class"] for row in train}))
    }
    model = lgb.train(
        {
            "objective": "binary", "learning_rate": .04, "num_leaves": 7,
            "min_data_in_leaf": 60, "lambda_l2": 8, "max_bin": 127,
            "seed": 42, "num_threads": 4, "verbosity": -1,
        },
        lgb.Dataset(
            _matrix(train, vocabulary),
            label=np.asarray(labels, dtype=np.int32),
            categorical_feature=[0],
        ),
        num_boost_round=100,
    )
    scored = list(zip(
        heldout, model.predict(_matrix(heldout, vocabulary), num_threads=4)
    ))
    by_project = {}
    for project in sorted(heldout_projects):
        rows = [(row, score) for row, score in scored if row["project"] == project]
        long_count = sum(row["duration_ms"] >= 2_000 for row, _ in rows)
        thresholds = {}
        for threshold in (0.5, 0.7, 0.9):
            selected = [
                row for row, score in rows if score >= threshold
            ]
            true = sum(row["duration_ms"] >= 2_000 for row in selected)
            thresholds[str(threshold)] = {
                "selected": len(selected),
                "true_long": true,
                "false_short": len(selected) - true,
                "precision": true / len(selected) if selected else None,
                "recall": true / long_count if long_count else None,
            }
        by_project[project] = {
            "calls": len(rows),
            "long_calls": long_count,
            "unseen_command_class": sum(
                row["class"] not in vocabulary for row, _ in rows
            ),
            "fixed_thresholds": thresholds,
        }
    return {
        "status": "read_only_project_disjoint_cold_tool_diagnostic_not_deployable",
        "features": ["observed_command_class", "log_input_chars"],
        "train_projects": sorted(train_projects),
        "train_calls": len(train),
        "train_long_calls": sum(labels),
        "heldout_projects": sorted(heldout_projects),
        "heldout_by_project": by_project,
        "limitation": (
            "Only duration >=2s is classified; no conditional RETURN ETA, "
            "latest-start, or physical H2D benefit is established."
        ),
    }


def pilot(workflows: Path) -> dict:
    rows = _cold_child_calls(workflows)
    by_project = defaultdict(list)
    for row in rows:
        by_project[row["project"]].append(row)
    evaluated = []
    for project in sorted(by_project):
        train = [row for other, group in by_project.items()
                 if other != project for row in group]
        evaluation = by_project[project]
        labels = [row["duration_ms"] >= 2_000 for row in train]
        if sum(labels) < 20 or len(labels) - sum(labels) < 20:
            raise ValueError("insufficient positive/negative train labels")
        vocabulary = {
            name: i for i, name in enumerate(sorted({row["class"] for row in train}))
        }
        model = lgb.train(
            {
                "objective": "binary", "learning_rate": .04, "num_leaves": 7,
                "min_data_in_leaf": 60, "lambda_l2": 8, "max_bin": 127,
                "seed": 42, "num_threads": 4, "verbosity": -1,
            },
            lgb.Dataset(
                _matrix(train, vocabulary),
                label=np.asarray(labels, dtype=np.int32),
                categorical_feature=[0],
            ),
            num_boost_round=100,
        )
        scores = model.predict(_matrix(evaluation, vocabulary), num_threads=4)
        evaluated.extend(
            {**row, "long": row["duration_ms"] >= 2_000, "score": float(score)}
            for row, score in zip(evaluation, scores)
        )
    result = {
        "status": "offline_cold_child_long_classifier_not_deployable",
        "features": ["observed_command_class", "log_input_chars"],
        "sample_count": len(evaluated),
        "long_count": sum(row["long"] for row in evaluated),
        "project_count": len(by_project),
        "by_project": {},
    }
    for project, group in sorted(by_project.items()):
        predictions = [row for row in evaluated if row["project"] == project]
        baseline = sum(row["long"] for row in predictions) / len(predictions)
        metrics = {"calls": len(predictions), "long": sum(row["long"] for row in predictions),
                   "base_rate": baseline}
        for fraction in (.01, .05, .1):
            selected = sorted(
                predictions, key=lambda row: row["score"], reverse=True
            )[: max(1, math.ceil(len(predictions) * fraction))]
            true = sum(row["long"] for row in selected)
            metrics[f"top_{int(fraction * 100)}pct"] = {
                "selected": len(selected), "true": true,
                "precision": true / len(selected),
                "recall": true / metrics["long"] if metrics["long"] else None,
            }
        result["by_project"][project] = metrics
    for fraction in (.01, .05, .1):
        key = f"top_{int(fraction * 100)}pct"
        selected = sum(result["by_project"][p][key]["selected"] for p in by_project)
        true = sum(result["by_project"][p][key]["true"] for p in by_project)
        result[key] = {
            "selected": selected, "true": true,
            "precision": true / selected,
            "recall": true / result["long_count"] if result["long_count"] else None,
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--evaluation-workflows", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = (
        transfer_pilot(
            _cold_child_calls(args.workflows),
            _cold_child_calls(args.evaluation_workflows, min_projects=1),
        )
        if args.evaluation_workflows else pilot(args.workflows)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
