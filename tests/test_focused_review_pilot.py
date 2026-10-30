from copy import deepcopy
import json
from pathlib import Path

import pytest

from beliefkv.experiments.deepagents_swebench import load_workload_bundle
from beliefkv.runtime.checkpoint_repository_tools import FOCUSED_TOOL_PROFILE
from scripts.prepare_focused_review_pilot import focused_manifest


ROOT = Path(__file__).resolve().parents[1]


def inputs():
    return (
        json.loads((ROOT / "configs/workloads/locobench_parallel_checkpoint_pilot6_20261030.json").read_bytes()),
        json.loads((ROOT / "experiments/reports/parallel_checkpoint_pilot6_20261030/declaration.json").read_bytes()),
    )


def test_review_pilot_preserves_original_sources_context_and_four_larger_objectives(tmp_path):
    source, declaration = inputs()
    before = deepcopy((source, declaration))
    manifest, contract = focused_manifest(source, declaration)
    assert (source, declaration) == before
    assert contract["objective_change_count"] == 2
    assert contract["unchanged_medium_and_full_objectives"] == 4
    assert not contract["root_child_soft_budgets_changed"]
    assert not contract["fixed_delegation_DAG_added"]
    assert not contract["formal_comparison_ready"]
    for old, task, row in zip(
        source["workloads"], manifest["workloads"], contract["assignments"], strict=True,
    ):
        for key in old.keys() - {"instance_id", "dataset_provenance", "problem_statement"}:
            assert old[key] == task[key]
        assert task["child_task_access"] == "read_only"
        assert task["repository_tool_profile"] == FOCUSED_TOOL_PROFILE
        if row["task_objective_changed"]:
            assert row["scope_hypothesis"] == "investigation"
            assert row["known_source_symbols"]
            assert all(
                item["last_line"] >= item["first_line"] > 0
                and item["navigation_only_not_a_behavioral_answer"]
                for item in row["known_source_symbols"]
            )
            assert task["problem_statement"] != old["problem_statement"]
        else:
            assert task["problem_statement"] == old["problem_statement"]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    workload = load_workload_bundle(path)
    assert len({task.repo for task in workload.workloads}) == 6
    assert all(task.children_read_only for task in workload.workloads)
    assert sum(task.task_access == "read_write" for task in workload.workloads) == 4


@pytest.mark.parametrize("field,value", (
    ("child_task_access", "read_write"),
    ("repository_tool_profile", "invented"),
))
def test_new_task_controls_reject_unknown_modes_instead_of_weakening_access(tmp_path, field, value):
    source, _ = inputs()
    source["workloads"][0][field] = value
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(source))
    with pytest.raises(ValueError, match="unsupported"):
        load_workload_bundle(path)
