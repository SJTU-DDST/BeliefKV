import json

import pytest

from beliefkv.experiments import gpu_startup as startup


def observation(*pids):
    return {
        "observed_at": "2026-11-12T13:00:00+00:00",
        "free_memory_mib": 143000, "total_memory_mib": 143771,
        "utilization_percent": 0,
        "compute_processes": [{"pid": pid, "used_memory_mib": 100} for pid in pids],
    }


def identity(pid, parent=1, start="old"):
    return {"pid": pid, "ppid": parent, "start_ticks": start}


def test_monitor_accepts_a_new_owned_descendant_and_rejects_pid_reuse(tmp_path):
    processes = {10: identity(10), 11: identity(11, 10), 12: identity(12, 11)}
    monitor = startup.Monitor(
        tmp_path, process=processes.get, observe=lambda: observation(12),
    )
    monitor.check([identity(10)], force=True)
    processes[10]["start_ticks"] = "reused"
    with pytest.raises(startup.ResourceConflict, match="foreign_GPU_context"):
        monitor.check([identity(10)], force=True)
    rows = [json.loads(line) for line in monitor.path.read_text().splitlines()]
    assert rows[0]["foreign_compute_processes"] == []
    assert rows[1]["foreign_compute_processes"][0]["pid"] == 12


def test_monitor_is_bounded_and_forced_checks_take_a_fresh_sample(tmp_path):
    calls, now = [], [10.]
    monitor = startup.Monitor(
        tmp_path, process=lambda _pid: None, clock=lambda: now[0],
        observe=lambda: calls.append(now[0]) or observation(),
    )
    monitor.check([])
    now[0] = 11.
    monitor.check([])
    monitor.check([], force=True)
    now[0] = 13.
    monitor.check([])
    assert calls == [10., 11., 13.]


def capacity(tokens=805198):
    return {
        "pool_layout": "static_full_only",
        "device_full_tokens": tokens, "device_full_bytes": tokens * 98304,
        "device_total_bytes": tokens * 98304,
        "device_mamba_bytes": 0, "device_mamba_slots": 0,
        "host_full_tokens": 4069011, "host_full_bytes": 400000057344,
        "host_total_bytes": 400000057344, "host_mamba_bytes": 0, "host_mamba_slots": 0,
    }


def test_actual_failed_startup_only_shrinks_device_capacity_and_may_be_retried():
    startup.verify_capacity(capacity(), capacity())
    with pytest.raises(startup.ResourceConflict, match="undersized_device_pool") as caught:
        startup.verify_capacity(capacity(), capacity(619352))
    assert caught.value.unattributed is True
    assert caught.value.evidence["actual"]["host_total_bytes"] == 400000057344


@pytest.mark.parametrize("change", ("host", "device_growth", "bytes", "layout", "extra_field"))
def test_configuration_or_geometry_changes_are_not_resource_retries(change):
    expected, actual = capacity(), capacity(619352)
    if change == "host":
        actual["host_total_bytes"] -= 1
    elif change == "device_growth":
        actual = capacity(805199)
    elif change == "bytes":
        actual["device_full_bytes"] -= 1
    elif change == "layout":
        actual["pool_layout"] = "other"
    else:
        actual["unregistered"] = 1
    with pytest.raises(ValueError) as caught:
        startup.verify_capacity(expected, actual)
    assert not isinstance(caught.value, startup.ResourceConflict)


def run(tmp_path, start, *, cleanup=None, alive=lambda _identity: False, verify=None):
    calls, state = [], {}

    def clean(attempt):
        calls.append(("cleanup", attempt.name))
        return {"identities": [identity(10)], "remaining_processes": []}

    result = startup.retry_startup(
        tmp_path, start, wait_ready=lambda: calls.append(("wait", None)),
        verify_inputs=verify or (lambda: calls.append(("verify", None))),
        cleanup=cleanup or clean, alive=alive, process=lambda _pid: None,
        state=state, pulse=lambda: calls.append(("pulse", None)), observe=observation,
    )
    return result, calls, state


def test_resource_conflict_is_cleaned_and_reverified_before_a_fresh_success(tmp_path):
    starts = []

    def start(attempt, _monitor):
        starts.append(attempt.name)
        (attempt / "server").mkdir()
        (attempt / "server.log").write_text("attempt " + attempt.name)
        if len(starts) == 1:
            raise startup.ResourceConflict("foreign_GPU_context", {"pid": 20})
        return "live-server"

    result, calls, state = run(tmp_path, start)
    assert result == "live-server" and starts == ["0001", "0002"]
    assert [name for name, _ in calls if name != "pulse"] == [
        "wait", "verify", "cleanup", "wait", "verify",
    ]
    failure = json.loads((tmp_path / "startup_attempts/0001/failure.json").read_bytes())
    assert failure["retryable"] and failure["actual_owned_exit_verified"]
    assert (tmp_path / "server").resolve() == tmp_path / "startup_attempts/0002/server"
    assert (tmp_path / "server.log").read_text() == "attempt 0002"
    assert state["startup_resource_retries"] == 1
    assert json.loads((tmp_path / "accepted_startup.json").read_bytes())["attempt"] == 2


@pytest.mark.parametrize("actual_alive,remaining", ((True, []), (False, [identity(10)])))
def test_any_actual_survivor_or_failed_cleanup_prevents_another_start(
    tmp_path, actual_alive, remaining,
):
    starts = []

    def start(attempt, _monitor):
        starts.append(attempt.name)
        raise startup.ResourceConflict("foreign_GPU_context", {})

    with pytest.raises(RuntimeError, match="actual owned process"):
        run(
            tmp_path, start, alive=lambda _row: actual_alive,
            cleanup=lambda _attempt: {"identities": [identity(10)], "remaining_processes": remaining},
        )
    assert starts == ["0001"]
    assert not (tmp_path / "accepted_startup.json").exists()


def test_unattributed_small_pool_does_not_retry_forever(tmp_path):
    starts = []

    def start(attempt, _monitor):
        starts.append(attempt.name)
        startup.verify_capacity(capacity(), capacity(619352))

    with pytest.raises(startup.ResourceConflict, match="undersized_device_pool"):
        run(tmp_path, start)
    assert starts == ["0001", "0002", "0003"]
    final = json.loads((tmp_path / "startup_attempts/0003/failure.json").read_bytes())
    assert final["retryable"] is False and final["unattributed_capacity_failures"] == 3


@pytest.mark.parametrize("error", (ValueError("configuration changed"), RuntimeError("model failed")))
def test_nonresource_failure_is_cleaned_preserved_and_never_retried(tmp_path, error):
    starts = []

    def start(attempt, _monitor):
        starts.append(attempt.name)
        raise error

    with pytest.raises(type(error), match=str(error)):
        run(tmp_path, start)
    assert starts == ["0001"]
    report = json.loads((tmp_path / "startup_attempts/0001/failure.json").read_bytes())
    assert not report["retryable"] and not report["resource_conflict"]


def test_changed_inputs_after_a_conflict_prevent_the_next_launch(tmp_path):
    starts, verifications = [], []

    def start(attempt, _monitor):
        starts.append(attempt.name)
        raise startup.ResourceConflict("foreign_GPU_context", {})

    def verify():
        verifications.append(True)
        if len(verifications) > 1:
            raise ValueError("source changed")

    with pytest.raises(ValueError, match="source changed"):
        run(tmp_path, start, verify=verify)
    assert starts == ["0001"]


def test_foreign_context_before_launch_is_preserved_without_starting_a_model(tmp_path):
    samples = iter([observation(20), observation()])
    started, cleaned = [], []
    state = {}
    result = startup.retry_startup(
        tmp_path, lambda attempt, _monitor: started.append(attempt.name) or "server",
        wait_ready=lambda: None, verify_inputs=lambda: None,
        cleanup=lambda attempt: cleaned.append(attempt.name) or {
            "identities": [], "remaining_processes": [],
        },
        alive=lambda _row: False, process=lambda _pid: None,
        state=state, pulse=lambda: None, observe=lambda: next(samples),
    )
    assert result == "server" and started == ["0002"] and cleaned == ["0001"]
    report = json.loads((tmp_path / "startup_attempts/0001/failure.json").read_bytes())
    assert report["evidence"]["foreign_compute_processes"][0]["pid"] == 20
