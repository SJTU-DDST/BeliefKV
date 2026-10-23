import json
from pathlib import Path

import pytest

from scripts import run_causal_source_capture as capture


def reference(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    for directory in (old, new):
        (directory / "configs").mkdir(parents=True)
        (directory / "configs/tasks.json").write_text('{"tasks": []}')
    return {
        "execution_checkout": str(old),
        "arms": {"native_matched": {
            "WORKLOAD_MANIFEST": str(old / "configs/tasks.json"),
            "ROOT_COUNT": "36", "MAX_RUNNING_REQUESTS": "8",
            "SERVER_RANDOM_SEED": "21", "SAMPLING_SEED": "21", "PYTHONHASHSEED": "21",
            "HICACHE_SIZE_GB": "200", "HOST_SPLIT": "80:20",
            "MODEL_CONTEXT_TOKENS": "262144", "CONTEXT_WINDOW_TOKENS": "262144",
            "CONTEXT_COMPRESSION_TRIGGER_RATIO": "0.95",
            "ARRIVAL_BATCH_SIZE": "0", "ARRIVAL_BATCH_INTERVAL_MS": "0",
            "FANOUT_PROFILE": "native_in_graph_nested_2to4",
            "BELIEFKV_ENABLE_TOOL_PREFETCH": "1", "BELIEFKV_ENABLE_PREPARE_HOST": "1",
        }},
    }, new


def test_recording_preserves_workload_and_disables_all_custom_kv_controls(tmp_path):
    plan, checkout = reference(tmp_path)
    env = capture.environment(plan, checkout, tmp_path / "engine", tmp_path / "run", 18546)
    assert env["BELIEFKV_REPLAY_TOKEN_CAPTURE"] == "1"
    assert env["NATIVE_POLICY_BASELINE"] == "1" and env["AB_MODE"] == "off"
    assert env["BELIEFKV_CHAT_TOKEN_BLOCK_CACHE"] == "1"
    assert env["MAX_RUNNING_REQUESTS"] == "8" and env["ROOT_COUNT"] == "36"
    assert env["WORKLOAD_MANIFEST"] == str(checkout / "configs/tasks.json")
    for name in (
        "PREPARE_HOST", "ENABLE_TOOL_TIMING", "BELIEFKV_ENABLE_TOOL_PREFETCH",
        "BELIEFKV_ENABLE_PREPARE_HOST", "BELIEFKV_ENABLE_BACKGROUND_PREPARE",
        "BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH", "BELIEFKV_ENABLE_RESIDENT_FIRST",
        "BELIEFKV_ENABLE_JOIN_PARENT_PRIORITY", "BELIEFKV_ENABLE_EXECUTION_HANDOFF",
    ):
        assert env[name] == "0"


def test_capture_normalizes_output_before_changing_to_its_frozen_checkout(tmp_path, monkeypatch):
    plan, checkout = reference(tmp_path)
    monkeypatch.chdir(tmp_path)
    env = capture.environment(plan, checkout, tmp_path / "engine", Path("relative/run"), 18558)
    assert env["RUN_ROOT"] == str(tmp_path / "relative/run/native_capture")


@pytest.mark.parametrize("field,value", (
    ("ROOT_COUNT", "12"), ("MAX_RUNNING_REQUESTS", "12"),
    ("SAMPLING_SEED", "22"), ("PYTHONHASHSEED", "22"),
    ("HICACHE_SIZE_GB", "160"), ("HOST_SPLIT", "90:10"),
    ("CONTEXT_WINDOW_TOKENS", "131072"), ("ARRIVAL_BATCH_SIZE", "18"),
    ("FANOUT_PROFILE", "forced_rounds"),
))
def test_capture_rejects_a_different_workload_before_launch(tmp_path, field, value):
    plan, checkout = reference(tmp_path)
    plan["arms"]["native_matched"][field] = value
    with pytest.raises(ValueError, match="preserve"):
        capture.environment(plan, checkout, tmp_path / "engine", tmp_path / "run", 18546)


def test_capture_rejects_changed_tasks(tmp_path):
    plan, checkout = reference(tmp_path)
    (checkout / "configs/tasks.json").write_text('{"tasks": ["changed"]}')
    with pytest.raises(ValueError, match="manifest changed"):
        capture.environment(plan, checkout, tmp_path / "engine", tmp_path / "run", 18546)


@pytest.mark.parametrize("change", (None, "watcher_alive", "driver_alive", "unfinished", "wrong_order"))
def test_reference_must_finish_and_its_processes_must_exit(change):
    plan = {"order": ["full", "native_matched"]}
    state = {
        "status": "collected_performance_review_pending",
        "completed_arms": ["full", "native_matched"],
        "watcher": {"pid": 1}, "driver": {"pid": 2},
    }
    if change == "unfinished":
        state["status"] = "running_native_matched"
    elif change == "wrong_order":
        state["completed_arms"] = ["native_matched", "full"]
    alive = lambda identity: identity["pid"] == {
        "watcher_alive": 1, "driver_alive": 2,
    }.get(change)
    if change is None:
        capture.reference_complete(plan, state, alive=alive)
    else:
        with pytest.raises(ValueError, match="reference pair"):
            capture.reference_complete(plan, state, alive=alive)


@pytest.mark.parametrize("change", (None, "off", "writer_error", "failed_records", "seed", "policy"))
def test_capture_monitor_checks_effective_recording_not_just_environment(tmp_path, change):
    server = tmp_path / "server"
    server.mkdir()
    status = {"replay_token_capture": True, "writer_error": None, "failed_records": 0}
    launch = {"native_policy_baseline": True, "server_random_seed": 21, "python_hash_seed": "21"}
    if change == "off":
        status["replay_token_capture"] = False
    elif change == "writer_error":
        status["writer_error"] = "disk failed"
    elif change == "failed_records":
        status["failed_records"] = 1
    elif change == "seed":
        launch["python_hash_seed"] = "22"
    elif change == "policy":
        launch["native_policy_baseline"] = False
    (server / "native_telemetry_status.json").write_text(json.dumps(status))
    (server / "launch_configuration.json").write_text(json.dumps(launch))
    if change is None:
        capture.observe(tmp_path, final=True)
    else:
        with pytest.raises(ValueError):
            capture.observe(tmp_path, final=True)


def test_final_capture_requires_native_status(tmp_path):
    capture.observe(tmp_path)
    with pytest.raises(ValueError, match="final native telemetry"):
        capture.observe(tmp_path, final=True)


def test_final_capture_requires_launch_configuration(tmp_path):
    server = tmp_path / "server"
    server.mkdir()
    (server / "native_telemetry_status.json").write_text(json.dumps({
        "replay_token_capture": True, "writer_error": None, "failed_records": 0,
    }))
    with pytest.raises(ValueError, match="launch configuration"):
        capture.observe(tmp_path, final=True)


@pytest.mark.parametrize("change", (
    None, "low_joins", "guard", "incomplete", "isolated_tail", "wide_jct",
))
def test_balanced_source_selection_does_not_use_policy_speedups(change):
    summary = {
        "duration_seconds": 3000., "delegation_round_count": 84, "completed_workflows": 36,
        "workflows": [
            {"duration_seconds": 2000. + index * 20, "native_agent_jct_eligible": True}
            for index in range(36)
        ],
    }
    if change == "low_joins":
        summary["delegation_round_count"] = 79
    elif change == "guard":
        summary["workflows"][0]["native_agent_jct_eligible"] = False
    elif change == "incomplete":
        summary["completed_workflows"] = 35
    elif change == "isolated_tail":
        summary["workflows"][-1]["duration_seconds"] = 2990.
    elif change == "wide_jct":
        for index in range(2):
            summary["workflows"][-1-index]["duration_seconds"] = 4900. - index
        summary["duration_seconds"] = 5000.
    selection = capture.balanced_source(summary)
    assert selection["accepted"] is (change is None)
    assert bool(selection["reasons"]) is (change is not None)
    assert selection["criteria"]["minimum_joins"] == 80


def test_predeclared_capture_waits_without_allocating_or_touching_another_job(tmp_path, monkeypatch):
    checks = iter((False, False, True))
    held, waits = [], []
    monkeypatch.setattr(capture.fresh, "check_hold", lambda *_a, **_k: held.append(1))
    monkeypatch.setattr(capture.qualified, "resources", lambda _port: {"ready": next(checks)})
    monkeypatch.setattr(capture.time, "sleep", waits.append)
    state, path = {}, tmp_path / "state.json"
    capture.wait_for_resources({"port": 18558, "wait_for_gpu": True}, state, path, lambda: False)
    assert len(held) == 3 and waits == [10., 10.]
    assert json.loads(path.read_bytes())["status"] == "waiting_gpu_capture"


@pytest.mark.parametrize("cancelled", (False, True))
def test_capture_wait_respects_a_cancellation_and_the_original_fail_fast_mode(
    tmp_path, monkeypatch, cancelled,
):
    monkeypatch.setattr(capture.fresh, "check_hold", lambda *_a, **_k: None)
    monkeypatch.setattr(capture.qualified, "resources", lambda _port: {"ready": False})
    with pytest.raises(ValueError):
        capture.wait_for_resources({"port": 18558}, {}, tmp_path / "state.json", lambda: cancelled)


def test_capture_does_not_use_a_temporary_idle_gap_between_previous_arms(tmp_path, monkeypatch):
    registration = tmp_path / "previous.json"
    state_path = registration.with_suffix(".state.json")
    state_path.write_text(json.dumps({"watcher": {"pid": 10}}))
    polls, queried = [], []
    monkeypatch.setattr(capture.fresh, "check_hold", lambda *_a, **_k: None)
    monkeypatch.setattr(capture.reservation, "alive", lambda _row: not polls)
    monkeypatch.setattr(capture.time, "sleep", lambda _seconds: polls.append(1))
    monkeypatch.setattr(
        capture.qualified, "resources", lambda port: queried.append(port) or {"ready": True},
    )
    capture.wait_for_resources(
        {"port": 18558, "wait_for_gpu": True, "after_comparison": str(registration)},
        {}, tmp_path / "capture.state.json", lambda: False,
    )
    assert polls == [1] and queried == [18558]
