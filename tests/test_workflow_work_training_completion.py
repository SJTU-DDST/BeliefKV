import pytest

from experiments.reports.workflow_work_training_20261029.finish_training import source_ready


def completed():
    return {
        "status": "source_captured_gpu_replay_pending", "original_exit_code": 0,
        "source_selection": {"accepted": True, "workflow_count": 8},
        "source_requests": 100, "watcher": {"pid": 1}, "driver": {"pid": 2},
    }


def test_training_completion_waits_for_actual_source_exit():
    plan, state = {"environment": {"ROOT_COUNT": "8"}}, completed()
    assert not source_ready(plan, state, alive=lambda identity: identity["pid"] == 1)
    assert source_ready(plan, state, alive=lambda _identity: False)


@pytest.mark.parametrize("status", (
    "capture_failed", "capture_interrupted", "source_captured_selection_rejected",
))
def test_failed_training_collection_cannot_trigger_export(status):
    with pytest.raises(ValueError, match="did not complete"):
        source_ready({}, {"status": status}, alive=lambda _identity: False)


@pytest.mark.parametrize("change", (
    {"original_exit_code": 1}, {"source_requests": 0},
    {"source_selection": {"accepted": True, "workflow_count": 7}},
))
def test_training_completion_rejects_incomplete_source_population(change):
    with pytest.raises(ValueError, match="exact successful population"):
        source_ready(
            {"environment": {"ROOT_COUNT": "8"}}, {**completed(), **change},
            alive=lambda _identity: False,
        )
