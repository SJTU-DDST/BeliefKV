import json

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
