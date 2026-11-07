import json

import pytest

from scripts import hold_after_frozen_repetition as subject


class EndProbe(Exception):
    pass


@pytest.mark.parametrize("mode", (
    "coordinator_live", "worker_live", "all_exited", "changed", "cancel", "existing",
))
def test_gpu_hold_waits_for_all_owners_and_preserves_newer_instructions(
    tmp_path, monkeypatch, mode,
):
    hold, marker = tmp_path / "hold.json", tmp_path / "paused"
    request, stage = tmp_path / "request.json", tmp_path / "stage.state.json"
    hold.write_text(json.dumps({"automatic_resume_allowed": True, "preserved": "yes"}))
    initial = hold.read_bytes()
    stage.write_text(json.dumps({
        "watcher": {"pid": 2}, "server": {"pid": 3}, "driver": {"pid": 4},
        "owned": [{"pid": 5}],
    }))
    request.write_text(json.dumps({
        "coordinator": {"pid": 1}, "registration_states": [str(stage)],
        "hold_sha256": subject.repeat.campaign.sha256_file(hold),
        "inputs_sha256": {}, "requested_at": "test", "campaign": "frozen-test",
        "hold_backup": "old-hold",
    }))
    if mode == "changed":
        hold.write_text(json.dumps({"automatic_resume_allowed": False, "newer": "instruction"}))
    if mode == "cancel":
        request.with_suffix(".cancel.json").write_text("{}")
    if mode == "existing":
        marker.write_text("newer pause")
    alive = {1} if mode == "coordinator_live" else {5} if mode == "worker_live" else set()
    monkeypatch.setattr(subject, "HOLD", hold)
    monkeypatch.setattr(subject, "MARKER", marker)
    monkeypatch.setattr(subject.reservation, "alive", lambda owner: owner["pid"] in alive)

    def end_probe(_delay):
        raise EndProbe

    monkeypatch.setattr(subject.time, "sleep", end_probe)
    if alive:
        with pytest.raises(EndProbe):
            subject.watch(request)
    else:
        subject.watch(request)
    state = json.loads(request.with_suffix(".state.json").read_text())
    if alive:
        assert state["status"] == "waiting_current_frozen_repetition"
        assert not marker.exists()
        assert hold.read_bytes() == initial
    elif mode in ("changed", "cancel"):
        assert not marker.exists()
        assert state["status"] in (
            "superseded_by_changed_reservation", "cancelled_by_newer_instruction",
        )
    else:
        actual = json.loads(hold.read_text())
        assert actual["automatic_resume_allowed"] is False
        assert actual["resume_requires_explicit_user_release"] is True
        assert actual["preserved"] == "yes"
        assert state["status"] == "GPU_hold_active_until_explicit_user_release"
        if mode == "existing":
            assert marker.read_text() == "newer pause"
        else:
            assert json.loads(marker.read_text())["CPU_analysis_allowed"] is True
