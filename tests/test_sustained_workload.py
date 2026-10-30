from copy import deepcopy
import io
import json
from pathlib import Path
import zipfile

import pytest

from scripts.prepare_sustained_workload import expand


def inputs():
    rows, candidates = [], []
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for index in range(36):
            project = "project-" + str(index)
            paths = []
            for category in ("bug_investigation", "feature_implementation", "multi_session_development"):
                name = project + "_" + category + "_01.json"
                archive.writestr(name, json.dumps({
                    "id": name[:-5], "task_category": category, "difficulty": "medium",
                    "title": "Original task", "description": "Original objective",
                }))
                paths.append(name)
            candidates.append({"project_id": project, "scenario_paths": paths})
            rows.append({
                "repo": "locobench/" + project, "instance_id": project,
                "problem_statement": "Keep original objective", "source_repo": "/original/" + project,
                "docker_image": "unchanged", "base_commit": "original",
                "frozen_repository_context": {"tokens": 100 + index, "sha256": "context"},
                "dataset_provenance": {
                    "reference_fields_exposed": False, "original_scenario": paths[0],
                },
            })
    pool = {"workloads": rows, "archive_sha256": "same", "candidates": candidates}
    base = deepcopy(pool)
    base["workloads"] = rows[:18]
    return base, pool, zipfile.ZipFile(payload)


def test_expansion_retains_original_tasks_and_adds_only_distinct_projects():
    base, pool, archive = inputs()
    previous = deepcopy((base, pool))
    manifest, added, references = expand(base, pool, archive, Path("/tmp/references"))
    assert len(manifest["workloads"]) == len({row["repo"] for row in manifest["workloads"]}) == 36
    actual = {row["repo"]: row for row in manifest["workloads"]}
    for row in base["workloads"]:
        assert actual[row["repo"]] == row
    assert len(added) == len(references) == 18
    assert all("Original objective" in actual[row["project"]]["problem_statement"] for row in added)
    assert (base, pool) == previous
    assert expand(base, pool, archive, Path("/tmp/references"))[0] == manifest


def test_expansion_refuses_repeated_projects_or_changed_archives():
    base, pool, archive = inputs()
    pool["workloads"][-1] = pool["workloads"][0]
    with pytest.raises(ValueError, match="distinct-project"):
        expand(base, pool, archive, Path("/tmp/references"))
    base, pool, archive = inputs()
    pool["archive_sha256"] = "changed"
    with pytest.raises(ValueError, match="distinct-project"):
        expand(base, pool, archive, Path("/tmp/references"))
