from copy import deepcopy

import pytest

from scripts.bind_prediction_training_capture import ROOT, configuration


def inputs():
    base = {
        "source_profile": "nested_oneshot", "order": ["native_matched"],
        "common_environment": {
            "ROOT_COUNT": "25", "CLIENT_ROOT_CONCURRENCY": "25",
            "MODEL_CACHE_LAYOUT": "full_only", "MAX_RUNNING_REQUESTS": "24",
            "CHUNKED_PREFILL_SIZE": "2048", "ENABLE_MIXED_CHUNK": "0",
            "FANOUT_PROFILE": "native_in_graph_parallel_workstreams",
            "CHILD_WORK_BUDGET_WARNING_CALLS": "4", "CHILD_WORK_BUDGET_TARGET_CALLS": "8",
            "ROOT_WORK_BUDGET_WARNING_CALLS": "8", "ROOT_WORK_BUDGET_TARGET_CALLS": "16",
        },
        "training_protocol": {},
    }
    original = {
        "archive_sha256": "pinned", "workloads": [{
            "repo": "train", "instance_id": "locobench__new", "docker_image": "original",
            "problem_statement": "complete original instructions",
        }],
    }
    qualified = deepcopy(original)
    qualified["workloads"][0]["sandbox_environment"] = {"PYTHONPATH": "/workspace"}
    population = {
        "selected": [{"project": "train", "original_id": "new"}],
        "held_out_projects_excluded": ["calibration", "evaluation"],
        "training_only": True, "new_independent_project_count": 0,
    }
    return base, original, qualified, population


def test_bound_capture_keeps_original_tasks_and_native_protocol_separate_from_serving_workload():
    base, original, qualified, population = inputs()
    result = configuration(base, original, qualified, population, ROOT / "qualified.json")
    assert result["common_environment"]["ROOT_COUNT"] == "1"
    assert result["common_environment"]["CLIENT_ROOT_CONCURRENCY"] == "1"
    assert result["order"] == ["native_matched"]
    assert result["training_protocol"]["preserve_formal_original73_workload"]
    assert qualified["workloads"][0]["problem_statement"] == original["workloads"][0]["problem_statement"]
    assert base["common_environment"]["ROOT_COUNT"] == "25"


@pytest.mark.parametrize("change", ("held_out", "rewritten_task", "different_image", "different_budget"))
def test_binding_rejects_holdout_leaks_task_rewrites_image_replacement_and_protocol_changes(change):
    base, original, qualified, population = inputs()
    if change == "held_out":
        population["held_out_projects_excluded"].append("train")
    elif change == "rewritten_task":
        qualified["workloads"][0]["problem_statement"] = "shortened"
    elif change == "different_image":
        qualified["workloads"][0]["docker_image"] = "replacement"
    else:
        base["common_environment"]["CHILD_WORK_BUDGET_TARGET_CALLS"] = "2"
    with pytest.raises(ValueError):
        configuration(base, original, qualified, population, ROOT / "qualified.json")
