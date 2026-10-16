from copy import deepcopy

import pytest

from scripts.resume_v77_after_v76_postprocess_20261016 import recovery_state


def inputs():
    previous = {
        "status": "driver_failed", "exit_code": 2, "run_root": "/tmp/v76",
        "driver": {"pid": 123, "start_ticks": "456"},
    }
    summary = {
        "workflow_count": 2,
        "workflows": [
            {"instance_id": "a", "outcome": "completed", "guard": True},
            {"instance_id": "b", "outcome": "incomplete"},
        ],
    }
    comparison = {
        "same_task_set": True, "same_physical_pool_capacity": True,
        "runs": {"reactive": {"run": "/tmp/v76/reactive"}, "native": {}},
    }
    return previous, summary, comparison, ["a", "b"], ["a", "b"]


def test_verified_recovery_retains_failure_and_all_outcome_labels():
    args = inputs()
    before = deepcopy(args)
    state = recovery_state(*args)
    assert args == before
    assert state["status"] == "completed"
    assert state["original_driver_status"] == "driver_failed"
    assert state["original_driver_exit_code"] == 2
    assert state["outcomes"] == {"completed": 1, "incomplete": 1}
    assert state["driver"] == before[0]["driver"]


@pytest.mark.parametrize("change", [
    "exit", "status", "summary_count", "duplicate_result", "unfinished",
    "duplicate_finish", "tasks", "capacity", "arm_name", "run_identity",
])
def test_unverified_or_different_failures_do_not_authorize_resume(change):
    previous, summary, comparison, expected, finished = inputs()
    if change == "exit":
        previous["exit_code"] = 1
    elif change == "status":
        previous["status"] = "running"
    elif change == "summary_count":
        summary["workflow_count"] = 1
    elif change == "duplicate_result":
        summary["workflows"][1]["instance_id"] = "a"
    elif change == "unfinished":
        finished.pop()
    elif change == "duplicate_finish":
        finished.append("a")
    elif change == "tasks":
        comparison["same_task_set"] = False
    elif change == "capacity":
        comparison["same_physical_pool_capacity"] = False
    elif change == "arm_name":
        comparison["runs"]["predictive_h2d"] = comparison["runs"].pop("reactive")
    else:
        comparison["runs"]["reactive"]["run"] = "/tmp/other/reactive"
    with pytest.raises(ValueError):
        recovery_state(previous, summary, comparison, expected, finished)
