#!/usr/bin/env python3
"""Freeze unseen-project JOIN service validation and separate load manifests."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path


PROJECTS = ("matplotlib", "scikit-learn")
TEST_PER_PROJECT = 8
BACKGROUND_TASKS = 32
DATASET = "princeton-nlp/SWE-bench_Verified"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project(row: dict) -> str:
    return row["instance_id"].split("__", 1)[0]


def _manifest(rows: list[dict], *, dataset_revision: str, arm: str) -> dict:
    return {
        "schema_version": 1,
        "dataset": DATASET,
        "dataset_revision": dataset_revision,
        "split": arm,
        "workloads": rows,
    }


def freeze(test_sources: list[Path], training_source: Path) -> dict:
    if not test_sources or len(set(test_sources)) != len(test_sources):
        raise ValueError("distinct test source manifests are required")
    provenance = {}
    test = {}
    revisions = set()
    for path in test_sources:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("split") != "test_id" or raw.get("dataset") != DATASET:
            raise ValueError(f"wrong frozen test split or dataset: {path}")
        revisions.add(raw["dataset_revision"])
        provenance[str(path)] = _sha(path)
        for row in raw["workloads"]:
            if _project(row) not in PROJECTS:
                raise ValueError(f"unexpected project in test split: {path}")
            if not row.get("docker_image") or not row.get("source_repo"):
                raise ValueError(f"missing test image/source for {row['instance_id']}")
            prior = test.setdefault(row["instance_id"], row)
            if (
                {key: value for key, value in prior.items() if key != "rollout_index"}
                != {key: value for key, value in row.items() if key != "rollout_index"}
            ):
                raise ValueError(f"conflicting test workload: {row['instance_id']}")
    if len(revisions) != 1:
        raise ValueError("test manifests have different dataset revisions")
    by_project = defaultdict(list)
    for row in test.values():
        by_project[_project(row)].append(row)
    if any(len(by_project[project]) != TEST_PER_PROJECT for project in PROJECTS):
        raise ValueError("expected eight distinct test tasks per project")
    ordered_test = {
        project: sorted(by_project[project], key=lambda row: row["instance_id"])
        for project in PROJECTS
    }
    frozen_test = [
        ordered_test[project][index]
        for index in range(TEST_PER_PROJECT)
        for project in PROJECTS
    ]

    train_raw = json.loads(training_source.read_text(encoding="utf-8"))
    if (
        train_raw.get("dataset") != DATASET
        or train_raw.get("split") != "train"
        or train_raw.get("dataset_revision") not in revisions
    ):
        raise ValueError("wrong training dataset, split, or revision")
    train_by_project = defaultdict(list)
    for row in train_raw["workloads"]:
        if row["instance_id"] in test or _project(row) in PROJECTS:
            raise ValueError("validation task/project occurs in background load")
        if not row.get("docker_image") or not row.get("source_repo"):
            raise ValueError(f"missing background image/source: {row['instance_id']}")
        train_by_project[_project(row)].append(row)
    ordered = [
        sorted(rows, key=lambda row: row["instance_id"])
        for _, rows in sorted(train_by_project.items())
        if len(rows) >= 4
    ]
    if not ordered:
        raise ValueError("no supported training projects for background load")
    background = []
    for index in range(BACKGROUND_TASKS):
        group = ordered[index % len(ordered)]
        rank = index // len(ordered)
        if rank >= len(group):
            raise ValueError("insufficient distinct background tasks")
        background.append(group[rank])
    if len({row["instance_id"] for row in background}) != BACKGROUND_TASKS:
        raise ValueError("duplicate background task")
    return {
        "provenance": {
            "test_source_sha256": provenance,
            "training_source_sha256": _sha(training_source),
            "test_projects": list(PROJECTS),
            "test_workflows": len(frozen_test),
            "background_workflows": len(background),
            "test_ids": [row["instance_id"] for row in frozen_test],
            "background_ids": [row["instance_id"] for row in background],
            "validation_policy": (
                "Test projects and outcomes never fit the JOIN or tool clock; "
                "background projects create pressure and are never scored."
            ),
        },
        "test": _manifest(
            frozen_test, dataset_revision=next(iter(revisions)), arm="test_id",
        ),
        "background": _manifest(
            background, dataset_revision=train_raw["dataset_revision"],
            arm="train_background_only",
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-source-dir", type=Path, required=True)
    parser.add_argument("--training-source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    test_sources = sorted(args.test_source_dir.glob("*-test_id*.json"))
    result = freeze(test_sources, args.training_source)
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    args.output_dir.mkdir(parents=True)
    for name in ("test", "background", "provenance"):
        (args.output_dir / f"{name}.json").write_text(
            json.dumps(
                result[name], indent=2, sort_keys=True, allow_nan=False,
            ) + "\n", encoding="utf-8",
        )


if __name__ == "__main__":
    main()
