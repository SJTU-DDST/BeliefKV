import json

import pytest

from scripts.run_official_quality_after_series import (
    ARMS, complete_summary, live_waiters,
)


def test_quality_order_uses_the_latest_collection_first():
    assert list(ARMS) == ["v76_reactive", "v75_native", "v75_predictive"]
    assert "reactive/client_156" in str(ARMS["v76_reactive"]["client"])


def test_waiting_requires_the_registered_process_identity_and_reaps_zombies():
    registration = {
        "wait_processes": [
            {"pid": 1, "start_ticks": "100"},
            {"pid": 2, "start_ticks": "200"},
            {"pid": 3, "start_ticks": "300"},
            {"pid": 4, "start_ticks": "400"},
        ],
    }
    actual = {
        1: {"pid": 1, "start_ticks": "100", "state": "S"},
        2: {"pid": 2, "start_ticks": "201", "state": "S"},
        3: {"pid": 3, "start_ticks": "300", "state": "Z"},
    }
    assert live_waiters(registration, actual.get) == [actual[1]]
    assert live_waiters(registration, lambda _: None) == []


def test_all_original_outcomes_are_retained_including_incomplete_and_error(tmp_path):
    path = tmp_path / "summary.json"
    summary = {
        "workflow_count": 3,
        "workflows": [
            {"instance_id": "a", "outcome": "completed"},
            {"instance_id": "b", "outcome": "incomplete"},
            {"instance_id": "c", "outcome": "error"},
        ],
    }
    path.write_text(json.dumps(summary))
    assert complete_summary(path, ["a", "b", "c"]) == summary


@pytest.mark.parametrize("ids", (["a", "b"], ["a", "b", "b"], ["a", "b", "different"]))
def test_partial_duplicate_or_changed_cohorts_are_not_scored(tmp_path, ids):
    path = tmp_path / "summary.json"
    path.write_text(json.dumps({
        "workflow_count": 3,
        "workflows": [{"instance_id": instance_id} for instance_id in ids],
    }))
    with pytest.raises(ValueError, match="incomplete task coverage"):
        complete_summary(path, ["a", "b", "c"])
