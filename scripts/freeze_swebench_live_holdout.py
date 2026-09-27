#!/usr/bin/env python3
"""Freeze disjoint SWE-bench-Live workloads without reading solution columns."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

import pyarrow.parquet as pq


DATASET = "SWE-bench-Live/SWE-bench-Live"
REVISION = "b51a86422e10cfd403beb4773e5a2947953e36ec"
LITE_SHA256 = "7ee0a75c41bfc954fd441b67ce738fc5c1cbae00721c4e30e7db4d893057c9ab"
VISIBLE_FIELDS = ("repo", "instance_id", "base_commit", "problem_statement")
TEST_PROJECTS = (
    "aws-cloudformation/cfn-lint",
    "deepset-ai/haystack",
    "reflex-dev/reflex",
)
PILOT_PROJECT = "pvlib/pvlib-python"
PER_TEST_PROJECT = 8


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def image_name(instance_id: str) -> str:
    if not re.fullmatch(r"[a-z0-9-]+__[a-z0-9-]+-[0-9]+", instance_id):
        raise ValueError(f"invalid SWE-bench instance ID: {instance_id!r}")
    return f"starryzhang/sweb.eval.x86_64.{instance_id.replace('__', '_1776_')}:latest"


def freeze(
    parquet: Path, verified_split: Path, source_root: Path, *,
    expected_sha256: str = LITE_SHA256,
) -> tuple[dict, dict, dict]:
    digest = _digest(parquet)
    if digest != expected_sha256:
        raise ValueError("SWE-bench-Live lite parquet digest changed")
    # Column projection is deliberate: no patch, tests, hints or difficulty
    # enters selection, validation, provenance or the workload manifest.
    table = pq.read_table(parquet, columns=list(VISIBLE_FIELDS))
    rows = table.to_pylist()
    frozen = json.loads(verified_split.read_text(encoding="utf-8"))
    previous = {project["project"] for project in frozen["projects"]}
    requested = set(TEST_PROJECTS) | {PILOT_PROJECT}
    if requested & previous:
        raise ValueError("new project overlaps the previous frozen split")
    seen: set[str] = set()
    grouped: dict[str, list[dict]] = {project: [] for project in requested}
    for row in rows:
        repo = row["repo"]
        if repo not in requested:
            continue
        instance_id = row["instance_id"]
        commit = row["base_commit"]
        statement = row["problem_statement"]
        if (
            not isinstance(repo, str)
            or not isinstance(instance_id, str)
            or not instance_id.startswith(repo.replace("/", "__") + "-")
            or instance_id in seen
            or not isinstance(commit, str)
            or re.fullmatch(r"[0-9a-f]{40}", commit) is None
            or not isinstance(statement, str)
            or not statement.strip()
        ):
            raise ValueError(f"invalid visible task fields: {instance_id}")
        seen.add(instance_id)
        grouped[repo].append(row)
    if any(len(grouped[project]) < PER_TEST_PROJECT for project in TEST_PROJECTS):
        raise ValueError("not enough visible tasks for the frozen holdout")
    if not grouped[PILOT_PROJECT]:
        raise ValueError("no pilot task in independent pilot project")

    def workload(row: dict) -> dict:
        repo = row["repo"]
        return {
            **row,
            "docker_image": image_name(row["instance_id"]),
            "source_repo": str(
                source_root / repo.replace("/", "__")
            ),
            "rollout_index": 0,
        }

    chosen = [
        workload(row)
        for project in TEST_PROJECTS
        for row in sorted(grouped[project], key=lambda item: item["instance_id"])[
            :PER_TEST_PROJECT
        ]
    ]
    pilot = workload(min(
        grouped[PILOT_PROJECT], key=lambda row: row["instance_id"]
    ))
    common = {
        "schema_version": 2,
        "dataset": DATASET,
        "dataset_revision": REVISION,
        "source_split": "lite",
        "selection_policy": "fixed repositories, first eight instance_ids per repository",
    }
    holdout = {**common, "split": "test_ood", "workloads": chosen}
    pilot_manifest = {
        **common,
        "split": "development",
        "selection_policy": "first instance_id in independent pilot repository",
        "workloads": [pilot],
    }
    provenance = {
        "dataset": DATASET,
        "dataset_revision": REVISION,
        "source_split": "lite",
        "parquet_sha256": digest,
        "verified_split_sha256": _digest(verified_split),
        "test_project_counts": dict(sorted(Counter(
            item["repo"] for item in chosen
        ).items())),
        "pilot_project": PILOT_PROJECT,
        "test_instance_ids": [item["instance_id"] for item in chosen],
        "pilot_instance_id": pilot["instance_id"],
        "policy": (
            "Project-disjoint from all prior Verified splits. Only public "
            "visible columns were loaded. Pilot project and test projects "
            "are disjoint. Freeze is not environment or predictor eligibility."
        ),
    }
    return holdout, pilot_manifest, provenance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", required=True, type=Path)
    parser.add_argument("--verified-split", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    holdout, pilot, provenance = freeze(
        args.parquet, args.verified_split, args.source_root.resolve(),
    )
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"frozen output already exists: {output}")
    output.mkdir(parents=True)
    for name, data in (
        ("holdout.json", holdout), ("pilot.json", pilot),
        ("provenance.json", provenance),
    ):
        (output / name).write_text(
            json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
