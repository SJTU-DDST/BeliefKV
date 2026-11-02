"""Bound larger pinned-pool teardown without releasing a live predecessor."""

import json

import pytest

from scripts import run_causal_comparison as comparison


@pytest.mark.parametrize("release_after_kill", (110., None, 240.))
def test_large_pool_waits_for_actual_exit_and_still_rejects_a_survivor(
    tmp_path, monkeypatch, release_after_kill,
):
    directory = tmp_path / "server"
    directory.mkdir()
    (directory / "native_capacity_census.json").write_text(json.dumps({
        "capacity": {"host_total_bytes": 400000057344},
    }))
    identity = {"pid": 11010, "ppid": 1, "start_ticks": "owned", "state": "R"}
    clock, killed_at, signals = [0.], [None], []

    def current(pid):
        assert pid == identity["pid"]
        if (
            release_after_kill is not None and killed_at[0] is not None
            and clock[0] >= killed_at[0] + release_after_kill
        ):
            return None
        return identity

    def send(pid, sig):
        assert pid == identity["pid"]
        signals.append(sig)
        if sig == comparison.signal.SIGKILL:
            killed_at[0] = clock[0]

    monkeypatch.setattr(comparison, "owned_processes", lambda _rows: [identity])
    monkeypatch.setattr(comparison.reservation, "process", current)
    monkeypatch.setattr(comparison.reservation, "alive", lambda row: current(row["pid"]) is not None)
    monkeypatch.setattr(comparison.os, "kill", send)
    monkeypatch.setattr(comparison.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(comparison.time, "sleep", lambda value: clock.__setitem__(0, clock[0] + value))
    if release_after_kill is None:
        with pytest.raises(RuntimeError, match="remain after cleanup"):
            comparison.cleanup(tmp_path, [identity], "larger_pool_survivor")
    else:
        comparison.cleanup(tmp_path, [identity], "larger_pool_exit")
    record = json.loads((tmp_path / "owned_process_cleanup.json").read_bytes())
    assert record["post_SIGKILL_grace_seconds"] == 300.
    assert record["observed_Host_pool_bytes"] == 400000057344
    assert bool(record["remaining_processes"]) is (release_after_kill is None)
    assert signals == [comparison.signal.SIGTERM, comparison.signal.SIGKILL]
    assert clock[0] < 313.
