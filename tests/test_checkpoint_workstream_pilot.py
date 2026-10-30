from copy import deepcopy
import json
from pathlib import Path

import pytest

from beliefkv.experiments.delegation_policy import (
    PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE, delegation_contract,
)
from scripts.prepare_checkpoint_workstream_pilot import checkpoint_manifest, source_paths


ROOT = Path(__file__).resolve().parents[1]


def inputs():
    return (
        json.loads((ROOT / "configs/workloads/locobench_parallel_pilot6_20261030.json").read_bytes()),
        json.loads((ROOT / "experiments/reports/workflow_mix_20261029/parallel_pilot6_declaration.json").read_bytes()),
    )


def test_source_grounded_pilot_preserves_original_project_state_and_all_long_requirements():
    source, declaration = inputs()
    before = deepcopy((source, declaration))
    manifest, contract = checkpoint_manifest(source, declaration)
    assert (source, declaration) == before
    assert len({row["repo"] for row in manifest["workloads"]}) == 6
    assert contract["scope_counts"] == {"investigation": 2, "implementation": 2, "multi_stage": 2}
    assert contract["shared_execution_protocol"]["child_warning_target_calls"] == [4, 8]
    assert contract["shared_execution_protocol"]["root_phase_warning_target_calls"] == [8, 16]
    assert contract["shape_review"]["automatic_expansion_to_eighteen"] is False
    assert contract["formal_comparison_ready"] is False
    assert contract["future_trace_or_reference_solution_used"] is False
    for old, task, row in zip(
        source["workloads"], manifest["workloads"], contract["assignments"], strict=True,
    ):
        for key in old.keys() - {"instance_id", "problem_statement", "dataset_provenance"}:
            assert task[key] == old[key]
        assert task["instance_id"] != old["instance_id"]
        provenance = task["dataset_provenance"]["derived_task"]
        assert provenance["previous_derived_instance_id"] == old["instance_id"]
        assert provenance["future_trace_or_reference_solution_used"] is False
        assert row["known_starting_paths"] == provenance["known_starting_paths"]
        for entry in row["known_starting_paths"]:
            assert entry["path"] in task["problem_statement"][:8192]
        if row["scope_hypothesis"] == "multi_stage":
            context = old["problem_statement"].split("\n\nOriginal public task context:\n", 1)[1]
            assert context in task["problem_statement"]
            assert provenance["original_full_task_success_claimed"] is True
        else:
            assert provenance["original_full_task_success_claimed"] is False
    by_repo = {task["repo"]: task for task in manifest["workloads"]}
    assert "src/color_utils.py" in by_repo["locobench/python_web_dashboard_medium_039"]["problem_statement"]
    assert "Page.publish" in by_repo["locobench/python_web_cms_hard_074"]["problem_statement"]


@pytest.mark.parametrize("variant", ("population", "duplicate_project", "assignment", "provenance"))
def test_checkpoint_preparation_rejects_changed_source_identity(variant):
    source, declaration = inputs()
    if variant == "population":
        source["workloads"].pop()
    elif variant == "duplicate_project":
        source["workloads"][1]["repo"] = source["workloads"][0]["repo"]
    elif variant == "assignment":
        declaration["assignments"][0]["project"] = "different/project"
    else:
        source["workloads"][0]["dataset_provenance"]["source_provenance_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        checkpoint_manifest(source, declaration)


def test_known_paths_are_checked_against_actual_pinned_source_not_the_latest_workspace():
    source, _ = inputs()
    task = source["workloads"][0]
    with pytest.raises(ValueError, match="absent"):
        source_paths(task, ["physage_academy/not_in_the_source.py"])
    with pytest.raises(ValueError, match="unsafe"):
        source_paths(task, ["../outside.py"])
    task["base_commit"] = "not-an-original-commit"
    import subprocess

    with pytest.raises(subprocess.CalledProcessError):
        source_paths(task, ["physage_academy/src/physage_academy/networking/protocol.py"])


def test_changed_prompt_protocol_cannot_be_grouped_with_the_old_capture_contract():
    contract = delegation_contract(PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE)
    assert contract["checkpoint_assignment_protocol_version"] == 3
    assert contract["root_owns_primary_implementation"] is True
    assert contract["child_unrelated_failures"] == "classify_and_handoff_without_expanding_scope"
    assert contract["model_call_limit_enforced"] is False
    assert contract["parallel_round_count_enforced"] is False
