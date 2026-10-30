from copy import deepcopy
import json
from pathlib import Path

import pytest

from beliefkv.experiments.deepagents_swebench import load_workload_bundle
from scripts.prepare_parallel_checkpoint_pilot import parallel_manifest


ROOT = Path(__file__).resolve().parents[1]


def inputs():
    return (
        json.loads((ROOT / "configs/workloads/locobench_structured_checkpoint_pilot6_20261030.json").read_bytes()),
        json.loads((ROOT / "experiments/reports/structured_checkpoint_pilot6_20261030/declaration.json").read_bytes()),
    )


def test_parallel_api_changes_only_submission_protocol_and_preserves_task_semantics(tmp_path):
    source, declaration = inputs()
    before = deepcopy((source, declaration))
    manifest, contract = parallel_manifest(source, declaration)
    assert (source, declaration) == before
    for previous, task, row in zip(
        source["workloads"], manifest["workloads"], contract["assignments"], strict=True,
    ):
        for key in previous.keys() - {"instance_id", "dataset_provenance"}:
            assert previous[key] == task[key]
        assert row["previous_instance_id"] == previous["instance_id"]
        assert task["dataset_provenance"]["derived_task"]["task_objective_changed"] is False
    protocol = contract["delegation_contract"]
    assert protocol["parallel_checkpoint_tool"] == protocol["root_retry_tool_choice"] == "parallel_tasks"
    assert protocol["parallel_checkpoint_members"] == [2, 4]
    assert protocol["root_retry_fanout_generation_bounds"] is None
    assert protocol["first_tool_choice"] == "auto"
    assert not protocol["parallel_round_count_enforced"]
    assert not contract["formal_comparison_ready"]
    assert not contract["model_tokens_or_tool_calls_rewritten"]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    workload = load_workload_bundle(path)
    assert len({task.repo for task in workload.workloads}) == 6


@pytest.mark.parametrize("invalid", ("missing", "duplicate_project", "different_access"))
def test_parallel_pilot_rejects_a_changed_population_or_task_access(invalid):
    source, declaration = inputs()
    if invalid == "missing":
        source["workloads"].pop()
    elif invalid == "duplicate_project":
        source["workloads"][1]["repo"] = source["workloads"][0]["repo"]
    else:
        declaration["assignments"][0]["task_access"] = "different"
    with pytest.raises(ValueError):
        parallel_manifest(source, declaration)
