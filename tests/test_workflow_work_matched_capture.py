from copy import deepcopy
import json
from pathlib import Path

import pytest

from experiments.reports.workflow_work_training_20261029.prepare import training_configuration
from experiments.reports.workflow_work_training_20261029.prepare_matched import matched_manifest, WORKLOAD


def test_matched_capture_keeps_the_original_tasks_sources_tests_and_environment():
    root = Path(__file__).resolve().parents[1]
    base = json.loads((root / "configs/workloads/locobench_tools18_20261026.json").read_bytes())
    original = deepcopy(base)
    manifest = matched_manifest(base)
    assert base == original
    assert manifest["workloads"] == original["workloads"][:6]
    assert len({row["repo"] for row in manifest["workloads"]}) == 6
    assert manifest["tool_environment_qualification"] == original["tool_environment_qualification"]
    config = training_configuration(
        json.loads((root / "configs/migration/predictive_decay_tick2s_no_handoff_20261030.json").read_bytes()),
        count=6, workload=WORKLOAD,
    )
    assert config["common_environment"]["ROOT_COUNT"] == "6"
    assert config["training_protocol"]["child_warning_target"] == [4, 8]
    assert config["training_protocol"]["root_warning_target"] == [8, 16]


def test_matched_capture_rejects_repeated_projects():
    row = {"repo": "locobench/project"}
    with pytest.raises(ValueError, match="different projects"):
        matched_manifest({"workloads": [row, row]}, count=2)
