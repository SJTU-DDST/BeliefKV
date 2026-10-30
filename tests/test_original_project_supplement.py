from copy import deepcopy
import json
from pathlib import Path
import zipfile

import pytest

from experiments.reports.workflow_mix_20261029.finish_parallel_pilot import validate_population as review_population
from experiments.reports.workflow_mix_20261029.live_start import verify_started_population
from scripts.prepare_original_project_supplement import (
    CONFIG, HERE, INVENTORIES, MANIFEST, POOL, missing_projects,
)
from scripts.prepare_original_task_mix import source_integrity, validate_population


def test_supplement_covers_all_missing_original_projects_without_rewriting_tasks():
    manifest = json.loads(MANIFEST.read_bytes())
    pool = json.loads(POOL.read_bytes())
    inventories = [json.loads(path.read_bytes()) for path in INVENTORIES]
    covered, missing = missing_projects(pool["candidates"], inventories)
    declaration = json.loads((HERE / "declaration.json").read_bytes())
    tasks = manifest["workloads"]
    assert len(covered) == 22
    assert len(tasks) == len(missing) == 26
    assert len(covered | {row["repo"].split("/", 1)[1] for row in tasks}) == 48
    assert not covered & {row["repo"].split("/", 1)[1] for row in tasks}
    assert {row["project_id"] for row in missing} == {
        row["repo"].split("/", 1)[1] for row in tasks
    }
    with zipfile.ZipFile(manifest["archive"]) as archive:
        validate_population(tasks, archive)
        for task in tasks:
            assert source_integrity(task, archive)["all_original_file_bytes_match"]
    assert max(task["frozen_repository_context"]["tokens"] for task in tasks) > 170000
    assert min(task["frozen_repository_context"]["tokens"] for task in tasks) < 25000
    assert {
        row["original_category"] for row in declaration["assignments"]
    } == {"code_comprehension", "architectural_understanding", "feature_implementation"}
    assert declaration["shape_review"]["model_work_p80_p20_diagnostic_minimum"] == 3
    assert not declaration["original_task_objectives_changed"]


def test_source_population_and_inflight_window_are_independent_of_decode_limit():
    config = json.loads(CONFIG.read_bytes())["common_environment"]
    assert config["ROOT_COUNT"] == "26"
    assert config["CLIENT_ROOT_CONCURRENCY"] == "12"
    assert config["MAX_RUNNING_REQUESTS"] == "24"
    assert not config["WORKFLOW_ARRIVAL_SCHEDULE"]
    assert config["TOOL_PREFETCH"] == config["LAYER_HANDOFF"] == "0"


@pytest.mark.parametrize("count", (6, 18, 26, 48))
def test_complete_reviewer_uses_declared_population_and_rejects_missing_members(count):
    rows = [{"task_id": f"task{i}", "project": f"locobench/project{i}"} for i in range(count)]
    declaration = {"assignments": [
        {"instance_id": row["task_id"], "project": row["project"]} for row in rows
    ]}
    review_population(rows, declaration)
    with pytest.raises(ValueError, match="predeclared"):
        review_population(rows[:-1], declaration)
    broken = deepcopy(declaration)
    broken["assignments"][-1]["project"] = broken["assignments"][0]["project"]
    with pytest.raises(ValueError, match="distinct"):
        review_population(rows, broken)


@pytest.mark.parametrize("started", (12, 19, 26))
def test_queued_capture_live_check_accepts_started_subset_of_frozen_population(started):
    manifest = {
        "instance_ids": [f"task{i}" for i in range(26)],
        "config": {"concurrency": 12, "workflow_arrival_schedule": None},
    }
    verify_started_population(
        manifest, manifest["instance_ids"][:started], started, 26, 12, queued_source=True,
    )
    with pytest.raises(ValueError):
        verify_started_population(
            manifest, manifest["instance_ids"][:12] + ["undeclared"], 13, 26, 12,
            queued_source=True,
        )
    with pytest.raises(ValueError):
        verify_started_population(
            manifest, manifest["instance_ids"][:started], started - 1, 26, 12,
            queued_source=True,
        )


def test_scheduled_capture_cannot_use_hidden_client_backlog():
    manifest = {
        "instance_ids": [f"task{i}" for i in range(26)],
        "config": {"concurrency": 12, "workflow_arrival_schedule": "declared-arrivals.json"},
    }
    with pytest.raises(ValueError):
        verify_started_population(
            manifest, manifest["instance_ids"][:12], 12, 26, 12, queued_source=True,
        )
    with pytest.raises(ValueError):
        verify_started_population(
            manifest, manifest["instance_ids"][:12], 12, 26, 12, queued_source=False,
        )
