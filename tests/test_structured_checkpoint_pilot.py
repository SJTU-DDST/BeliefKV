from copy import deepcopy
import json
from pathlib import Path

import pytest

from beliefkv.experiments.deepagents_swebench import load_workload_bundle
from scripts.prepare_structured_checkpoint_pilot import structured_manifest


ROOT = Path(__file__).resolve().parents[1]


def inputs():
    return (
        json.loads((ROOT / "configs/workloads/locobench_checkpoint_pilot6_20261030.json").read_bytes()),
        json.loads((ROOT / "experiments/reports/checkpoint_pilot6_20261030/declaration.json").read_bytes()),
    )


def test_required_task_fields_do_not_shrink_or_replace_the_actual_workload_objectives(tmp_path):
    source, declaration = inputs()
    before = deepcopy((source, declaration))
    manifest, contract = structured_manifest(source, declaration)
    assert (source, declaration) == before
    assert contract["delegation_contract"]["checkpoint_assignment_protocol_version"] == 3
    assert contract["supporting_edit_write_paths_max"] == 2
    assert contract["formal_comparison_ready"] is False
    assert contract["no_model_call_or_output_limit_added"] is True
    for old, task, row in zip(
        source["workloads"], manifest["workloads"], contract["assignments"], strict=True,
    ):
        assert task["problem_statement"] == old["problem_statement"]
        for key in old.keys() - {"instance_id", "dataset_provenance"}:
            assert task[key] == old[key]
        assert task["task_access"] == (
            "read_only" if row["scope_hypothesis"] == "investigation" else "read_write"
        )
        assert task["dataset_provenance"]["derived_task"]["task_objective_changed"] is False
    path = tmp_path / "workload.json"
    path.write_text(json.dumps(manifest))
    parsed = load_workload_bundle(path)
    assert [task.task_access for task in parsed.workloads].count("read_only") == 2
    assert len({task.repo for task in parsed.workloads}) == 6


def test_manifest_rejects_undeclared_task_access_instead_of_silently_granting_edits(tmp_path):
    source, declaration = inputs()
    manifest, _ = structured_manifest(source, declaration)
    manifest["workloads"][0]["task_access"] = "unknown"
    path = tmp_path / "workload.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="task access"):
        load_workload_bundle(path)
