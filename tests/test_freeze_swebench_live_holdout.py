from __future__ import annotations

import hashlib
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scripts.freeze_swebench_live_holdout import (
    PILOT_PROJECT, TEST_PROJECTS, freeze, image_name,
)


def _dataset(tmp_path):
    rows = [
        {
            "repo": repo,
            "instance_id": f"{repo.replace('/', '__')}-{number}",
            "base_commit": "a" * 40,
            "problem_statement": f"Repair issue {number}",
            "patch": "secret expected fix",
            "test_patch": "secret test",
            "hints_text": "secret hint",
        }
        for repo in (*TEST_PROJECTS, PILOT_PROJECT)
        for number in range(8)
    ]
    parquet = tmp_path / "tasks.parquet"
    pq.write_table(pa.Table.from_pylist(rows), parquet)
    old_split = tmp_path / "old.json"
    old_split.write_text(
        json.dumps({"projects": [{"project": "django/django"}]}),
        encoding="utf-8",
    )
    digest = hashlib.sha256(parquet.read_bytes()).hexdigest()
    return parquet, old_split, digest


def test_holdout_is_disjoint_deterministic_and_does_not_load_secrets(tmp_path) -> None:
    parquet, old_split, digest = _dataset(tmp_path)
    holdout, pilot, provenance = freeze(
        parquet, old_split, tmp_path / "sources", expected_sha256=digest,
    )
    assert len(holdout["workloads"]) == 24
    assert provenance["test_project_counts"] == {
        project: 8 for project in sorted(TEST_PROJECTS)
    }
    assert pilot["workloads"][0]["repo"] == PILOT_PROJECT
    assert set(holdout["workloads"][0]) == {
        "repo", "instance_id", "base_commit", "problem_statement",
        "docker_image", "source_repo", "rollout_index",
    }
    assert "secret" not in json.dumps((holdout, pilot, provenance))
    assert holdout["workloads"][0]["docker_image"] == (
        "starryzhang/sweb.eval.x86_64.aws-cloudformation_1776_cfn-lint-0:latest"
    )
    assert freeze(
        parquet, old_split, tmp_path / "sources", expected_sha256=digest,
    ) == (holdout, pilot, provenance)


def test_rejects_changed_source_and_reused_project(tmp_path) -> None:
    parquet, old_split, digest = _dataset(tmp_path)
    with pytest.raises(ValueError, match="digest changed"):
        freeze(parquet, old_split, tmp_path, expected_sha256="0" * 64)
    old_split.write_text(
        json.dumps({"projects": [{"project": TEST_PROJECTS[0]}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="overlaps"):
        freeze(parquet, old_split, tmp_path, expected_sha256=digest)
    with pytest.raises(ValueError, match="invalid SWE-bench"):
        image_name("bad/unsafe-id")
