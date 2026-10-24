import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from scripts import run_causal_comparison as comparison


def drain_fixture(tmp_path, monkeypatch, snapshots):
    arm = tmp_path / "arm"
    (arm / "server").mkdir(parents=True)
    path = arm / "server/native_telemetry_status.json"
    clock, index = [0.], [0]
    base = {
        "writer_error": None, "failed_records": 0, "dropped_records": 0,
        "writer_queue_depth": 0, "pending_batch_count": 0,
        "pending_request_count": 0, "snapshot_ts_ms": 1.,
        "record_counts": {"audit": 10},
    }

    def publish():
        path.write_text(json.dumps({**base, **snapshots[index[0]]}))

    def sleep(seconds):
        clock[0] += seconds
        index[0] = min(index[0] + 1, len(snapshots) - 1)
        publish()

    publish()
    monkeypatch.setattr(comparison.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(comparison.time, "sleep", sleep)
    return arm, clock, Mock(return_value=None), NS(poll=Mock(return_value=None))


def test_completed_replay_waits_for_backlog_and_a_fresh_empty_snapshot(tmp_path, monkeypatch):
    arm, clock, pulse, server = drain_fixture(tmp_path, monkeypatch, [
        {"writer_queue_depth": 97, "snapshot_ts_ms": 10.},
        {"snapshot_ts_ms": 11.},
        {"snapshot_ts_ms": 11.},
        {"snapshot_ts_ms": 12.},
    ])
    comparison.wait_for_telemetry_drain(arm, server=server, pulse=pulse, timeout=1.)
    result = json.loads((arm / "telemetry_drain.json").read_bytes())
    assert clock[0] == .75
    assert pulse.call_count == server.poll.call_count == 4
    assert result["previous_zero_snapshot_ts_ms"] == 11.
    assert result["drained_status"]["snapshot_ts_ms"] == 12.
    assert result["drained_status"]["writer_queue_depth"] == 0


def test_a_new_backlog_invalidates_an_earlier_empty_observation(tmp_path, monkeypatch):
    arm, clock, pulse, server = drain_fixture(tmp_path, monkeypatch, [
        {"snapshot_ts_ms": 1.},
        {"snapshot_ts_ms": 2., "writer_queue_depth": 1},
        {"snapshot_ts_ms": 3.},
        {"snapshot_ts_ms": 4.},
    ])
    comparison.wait_for_telemetry_drain(arm, server=server, pulse=pulse, timeout=1.)
    result = json.loads((arm / "telemetry_drain.json").read_bytes())
    assert clock[0] == .75
    assert result["previous_zero_snapshot_ts_ms"] == 3.


@pytest.mark.parametrize("field", (
    "writer_queue_depth", "pending_batch_count", "pending_request_count",
))
def test_pending_native_work_or_writes_cannot_qualify_cleanup(tmp_path, monkeypatch, field):
    arm, _clock, pulse, server = drain_fixture(tmp_path, monkeypatch, [
        {field: 1, "snapshot_ts_ms": 1.},
        {field: 1, "snapshot_ts_ms": 2.},
    ])
    with pytest.raises(ValueError, match="did not drain"):
        comparison.wait_for_telemetry_drain(arm, server=server, pulse=pulse, timeout=.5)
    assert not (arm / "telemetry_drain.json").exists()


def test_a_stale_zero_queue_snapshot_does_not_prove_that_the_writer_is_alive(tmp_path, monkeypatch):
    arm, _clock, pulse, server = drain_fixture(tmp_path, monkeypatch, [{}])
    with pytest.raises(ValueError, match="did not drain"):
        comparison.wait_for_telemetry_drain(arm, server=server, pulse=pulse, timeout=.5)
    assert not (arm / "telemetry_drain.json").exists()


@pytest.mark.parametrize("change", (
    {"writer_error": "disk write failed"},
    {"failed_records": 1},
    {"dropped_records": 1},
))
def test_writer_failures_stop_the_comparison_before_cleanup(tmp_path, monkeypatch, change):
    arm, clock, pulse, server = drain_fixture(tmp_path, monkeypatch, [change])
    with pytest.raises(ValueError, match="writer failed"):
        comparison.wait_for_telemetry_drain(arm, server=server, pulse=pulse)
    assert clock[0] == 0.
    assert not (arm / "telemetry_drain.json").exists()


def test_server_exit_during_drain_is_not_treated_as_success(tmp_path, monkeypatch):
    arm, _clock, pulse, server = drain_fixture(tmp_path, monkeypatch, [
        {"writer_queue_depth": 97}, {"writer_queue_depth": 97},
    ])
    server.poll.side_effect = [None, 0]
    with pytest.raises(ValueError, match="server exited"):
        comparison.wait_for_telemetry_drain(arm, server=server, pulse=pulse)
    assert not (arm / "telemetry_drain.json").exists()


def test_drain_keeps_the_supervisor_stop_and_hold_checks_active(tmp_path, monkeypatch):
    arm, _clock, pulse, server = drain_fixture(tmp_path, monkeypatch, [
        {"writer_queue_depth": 97},
    ])
    pulse.side_effect = [None, ValueError("stop requested")]
    with pytest.raises(ValueError, match="stop requested"):
        comparison.wait_for_telemetry_drain(arm, server=server, pulse=pulse)
    assert not (arm / "telemetry_drain.json").exists()
