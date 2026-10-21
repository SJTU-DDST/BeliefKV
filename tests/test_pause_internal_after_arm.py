import json

import pytest

from scripts.pause_internal_after_arm import activate_pause, at_boundary, finish_pause


def state(status="postprocessing_scheduler_only"):
    return {
        "status": status, "current_arm": "scheduler_only",
        "driver": {"pid": 123, "start_ticks": "456"},
        "order": ["join_priority_only", "scheduler_only", "kv_only", "native_matched"],
        "completed_arms": ["join_priority_only"],
    }


@pytest.mark.parametrize("status,live,expected", [
    ("running_scheduler_only", True, False),
    ("running_scheduler_only", False, False),
    ("postprocessing_scheduler_only", True, False),
    ("postprocessing_scheduler_only", False, True),
])
def test_boundary_pause_never_interrupts_running_arm(status, live, expected):
    assert at_boundary(state(status), "scheduler_only", alive=lambda _: live) is expected


def test_newer_pause_marker_is_preserved(tmp_path):
    marker = tmp_path / "pause"
    marker.write_text("newer user hold\n")
    activate_pause(marker, {})
    assert marker.read_text() == "newer user hold\n"


def test_pause_marker_blocks_automatic_resume(tmp_path):
    marker = tmp_path / "pause"
    activate_pause(marker, {"registration": "plan", "arm": "scheduler_only", "requested_at": "now"})
    assert json.loads(marker.read_text())["automatic_resume_allowed"] is False


def test_final_state_keeps_completed_and_pending_arms_separate():
    observed = state("stopped_for_investigation")
    observed["completed_arms"].append("scheduler_only")
    paused = finish_pause(observed, {"arm": "scheduler_only"})
    assert paused["status"] == "paused_after_arm"
    assert paused["completed_arms"] == ["join_priority_only", "scheduler_only"]
    assert paused["pending_arms"] == ["kv_only", "native_matched"]
    assert paused["performance_accepted"] is False


@pytest.mark.parametrize("change", ["not_completed", "later_arm"])
def test_pause_cannot_claim_missing_cleanup_or_hide_a_later_arm(change):
    observed = state()
    if change == "later_arm":
        observed["completed_arms"].append("scheduler_only")
        observed["current_arm"] = "kv_only"
    with pytest.raises(ValueError):
        finish_pause(observed, {"arm": "scheduler_only"})


def test_monitor_activates_only_at_cleanup_then_waits_for_supervisor_exit(tmp_path, monkeypatch):
    from scripts import pause_internal_after_arm as module

    marker, request_path, state_path = (tmp_path / name for name in ("pause", "request.json", "state.json"))
    watcher = {"pid": 101, "start_ticks": "202"}
    request = {
        "state": str(state_path), "watcher": watcher, "arm": "scheduler_only",
        "pause_marker": str(marker), "registration": "plan", "requested_at": "now",
    }
    request_path.write_text(json.dumps(request))
    snapshots = [
        {**state("running_scheduler_only"), "watcher": watcher},
        {**state(), "watcher": watcher},
        {**state("stopped_for_investigation"), "watcher": watcher,
         "completed_arms": ["join_priority_only", "scheduler_only"]},
    ]
    original_load = module.load
    monkeypatch.setattr(module, "load", lambda path: snapshots.pop(0) if path == state_path else original_load(path))
    original_boundary = module.at_boundary
    monkeypatch.setattr(module, "at_boundary", lambda row, arm: original_boundary(row, arm, alive=lambda _: False))
    live = iter((True, True, False))
    monkeypatch.setattr(module.reservation, "alive", lambda _: next(live))
    pauses = []
    monkeypatch.setattr(module.time, "sleep", lambda _: pauses.append(marker.exists()))
    module.watch(request_path)
    assert pauses == [False, True]
    assert original_load(request_path)["status"] == "paused"
    assert original_load(state_path)["status"] == "paused_after_arm"
    assert original_load(state_path)["pending_arms"] == ["kv_only", "native_matched"]
