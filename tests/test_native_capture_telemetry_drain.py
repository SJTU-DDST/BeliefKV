from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from scripts import drain_native_telemetry as drain
from tests.test_causal_telemetry_drain import drain_fixture


def test_native_capture_cli_drains_before_the_shell_can_stop_its_server(tmp_path, monkeypatch):
    arm, clock, _pulse, server = drain_fixture(tmp_path, monkeypatch, [
        {"writer_queue_depth": 97, "snapshot_ts_ms": 10.},
        {"snapshot_ts_ms": 11.},
        {"snapshot_ts_ms": 12.},
    ])
    monkeypatch.setattr(drain, "ServerPidProbe", lambda _pid: server)
    monkeypatch.setenv("BELIEFKV_EXPERIMENT_PAUSE_FILE", str(tmp_path / "paused"))
    drain.main(["--arm", str(arm), "--server-pid", "123"])
    assert (arm / "telemetry_drain.json").is_file()
    assert clock[0] == .5
    assert server.poll.call_count == 3


def test_native_capture_collection_wait_retains_the_user_pause_check(tmp_path, monkeypatch):
    arm, _clock, _pulse, _server = drain_fixture(tmp_path, monkeypatch, [
        {"writer_queue_depth": 97},
    ])
    pause = tmp_path / "paused"
    pause.touch()
    monkeypatch.setenv("BELIEFKV_EXPERIMENT_PAUSE_FILE", str(pause))
    monkeypatch.setattr(drain, "ServerPidProbe", lambda _pid: NS(poll=lambda: None))
    with pytest.raises(ValueError, match="experiment paused"):
        drain.main(["--arm", str(arm), "--server-pid", "123"])
    assert not (arm / "telemetry_drain.json").exists()


@pytest.mark.parametrize("current", (
    {"start_ticks": "10", "state": "Z"},
    {"start_ticks": "11", "state": "S"},
    {},
))
def test_native_capture_pid_probe_rejects_exited_or_reused_server_identity(monkeypatch, current):
    monkeypatch.setattr(drain.comparison.reservation, "process", Mock(side_effect=[
        {"start_ticks": "10", "state": "S"}, current,
    ]))
    assert drain.ServerPidProbe(123).poll() == 1


def test_native_capture_pid_probe_accepts_only_its_original_live_server(monkeypatch):
    monkeypatch.setattr(drain.comparison.reservation, "process", Mock(return_value={
        "start_ticks": "10", "state": "S",
    }))
    assert drain.ServerPidProbe(123).poll() is None


def test_native_capture_pid_probe_requires_observable_process_identity(monkeypatch):
    monkeypatch.setattr(drain.comparison.reservation, "process", Mock(return_value={
        "state": "S",
    }))
    assert drain.ServerPidProbe(123).poll() == 1
