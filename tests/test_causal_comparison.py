import json
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as comparison


@pytest.fixture
def reference(tmp_path):
    artifact = tmp_path / "timing.json"
    artifact.write_text("{}")
    return {"arms": {"full": {
        "ROOT_COUNT": "36", "MAX_RUNNING_REQUESTS": "8",
        "SAMPLING_SEED": "21", "SERVER_RANDOM_SEED": "21", "PYTHONHASHSEED": "21",
        "HICACHE_SIZE_GB": "200", "HOST_SPLIT": "80:20",
        "MODEL_CONTEXT_TOKENS": "262144", "JOIN_PREFETCH_TRIGGER": "observed_eos",
        "PREFILL_SCHEDULING_POLICY": "workflow", "PREFETCH_RESIDENCY_PROTECTION": "1",
        "TOOL_RESTORE_MODE": "speculative", "RESTORE_TIERS": "complete",
        "IDLE_PREPARE_POLICY": "all", "TOOL_CONTINUITY": "0",
        "EXECUTION_FRONTIER_RESTORE": "0", "ONLINE_TOOL_TIMING": "1",
        "PARALLEL_WAIT_PREFETCH": "1", "WAIT_PREFETCH_CONTEXTS": "2",
        "TOOL_TIMING_ARTIFACT": str(artifact), "H2D_SEED_ARTIFACT": str(artifact),
        "TRANSFER_SERVICE_SEED": str(artifact),
        "BELIEFKV_NATIVE_TELEMETRY_DIR": "old/native",
        "BELIEFKV_ADMISSION_TELEMETRY_DIR": "old/admission",
        "BELIEFKV_ADMISSION_OPPORTUNITY_DIR": "old/opportunities",
    }}}


@pytest.mark.parametrize("label", comparison.ORDER)
def test_each_control_preserves_common_work_and_only_enables_its_policy(reference, tmp_path, label):
    checkout, engine, arm, plugin = (tmp_path / name for name in ("checkout", "engine", label, "plugin"))
    env, flags, event_socket = comparison.environment(
        reference, label, checkout, engine, arm, 18554, plugin,
    )
    native, custom_kv = label == "native_matched", label in ("full", "no_background_prepare")
    assert env["PYTHONHASHSEED"] == env["SERVER_RANDOM_SEED"] == env["SAMPLING_SEED"] == "21"
    assert env["MAX_RUNNING_REQUESTS"] == "8"
    assert env["HICACHE_SIZE_GB"] == "200" and env["BELIEFKV_FULL_MAMBA_HOST_SPLIT"] == "80:20"
    assert env["CONTEXT_LENGTH"] == "262144" and env["MEM_FRACTION_STATIC"] == "0.94"
    assert env["SGLANG_ENABLE_DELAY_SAMPLE"] == "1"
    assert env["BELIEFKV_CAUSAL_REPLAY_BRIDGE"] == "1"
    assert env["BELIEFKV_REPLAY_TOKEN_CAPTURE"] == "0"
    assert env["BELIEFKV_JOIN_PREFETCH_TRIGGER"] == "observed_eos"
    assert env["BELIEFKV_ENABLE_FRONTIER_RESTORE"] == "0"
    assert env["BELIEFKV_ENABLE_TOOL_CONTINUITY"] == "0"
    assert env["BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH"] == str(int(custom_kv))
    assert env["BELIEFKV_ENABLE_TOOL_PREFETCH"] == str(int(custom_kv))
    assert env["BELIEFKV_ENABLE_RESIDENT_FIRST"] == str(int(custom_kv))
    assert env["BELIEFKV_ENABLE_BACKGROUND_PREPARE"] == str(int(label == "full"))
    assert "--enable-custom-logit-processor" in flags
    assert ("--enable-beliefkv-admission" in flags) is not native
    if native:
        assert event_socket is None and env["BELIEFKV_NATIVE_TELEMETRY_DIR"] == str(arm / "server")
        assert "BELIEFKV_ADMISSION_TELEMETRY_DIR" not in env
    else:
        assert event_socket == Path("/tmp/bkv-causal-18554.sock")
        assert env["BELIEFKV_ADMISSION_TELEMETRY_DIR"] == str(arm / "server")
        assert "BELIEFKV_NATIVE_TELEMETRY_DIR" not in env
    for name in ("BELIEFKV_TOOL_TIMING_ARTIFACT", "BELIEFKV_H2D_SEED", "BELIEFKV_TRANSFER_SERVICE_SEED"):
        assert bool(env[name]) is custom_kv
        assert bool(env[name + "_SHA256"]) is custom_kv
    assert reference["arms"]["full"]["BELIEFKV_NATIVE_TELEMETRY_DIR"] == "old/native"


@pytest.mark.parametrize("field,value", (
    ("MAX_RUNNING_REQUESTS", "12"), ("SERVER_RANDOM_SEED", "22"),
    ("PYTHONHASHSEED", "22"), ("HICACHE_SIZE_GB", "160"),
    ("JOIN_PREFETCH_TRIGGER", "semantic"), ("PREFILL_SCHEDULING_POLICY", "join_priority"),
    ("TOOL_RESTORE_MODE", "execution"), ("EXECUTION_FRONTIER_RESTORE", "1"),
))
def test_comparison_does_not_silently_change_the_requested_profile(reference, tmp_path, field, value):
    reference["arms"]["full"][field] = value
    with pytest.raises(ValueError):
        comparison.environment(reference, "full", tmp_path, tmp_path, tmp_path, 18554, tmp_path)


def receipt():
    return {
        "request_id": "r", "prompt_tokens": 12, "output_tokens": 3,
        "prompt_sha256": "prompt", "output_sha256": "output",
        "native_finish_reason": {"type": "stop", "matched": 9},
        "exact_tokens_verified": True,
    }


def result_fixture(tmp_path):
    arm = tmp_path / "full"
    replay = arm / "replay"
    replay.mkdir(parents=True)
    path = replay / "native_requests.jsonl"
    path.write_text(json.dumps(receipt()) + "\n")
    plan = {
        "source_plan": {"requests": 1, "prompt_tokens": 12, "output_tokens": 3},
        "token_source_sha256": "tokens", "workflow_ids": ["w"],
    }
    result = {
        "causal_execution_completed": True, "exact_tokens_verified": True,
        "original_native_stopping_verified": True,
        "native_session_http_lifecycle_completed": True,
        "live_control_events_delivered": True, "completed_requests": 1,
        "source_plan": dict(plan["source_plan"]),
        "token_source": {"token_source_sha256": "tokens"},
        "workflow_elapsed_ms": {"w": 5000.}, "native_requests_sha256": sha256_file(path),
    }
    return plan, result, arm


@pytest.mark.parametrize("change", (
    None, "incomplete", "stopping", "control", "population", "tokens",
    "workflow", "receipt", "input_total", "output_total",
))
def test_complete_comparison_requires_actual_work_and_causal_receipts(tmp_path, change):
    plan, result, arm = result_fixture(tmp_path)
    if change == "incomplete":
        result["causal_execution_completed"] = False
    elif change == "stopping":
        result["original_native_stopping_verified"] = False
    elif change == "control":
        result["live_control_events_delivered"] = False
    elif change == "population":
        result["completed_requests"] = 2
    elif change == "tokens":
        result["token_source"]["token_source_sha256"] = "other"
    elif change == "workflow":
        result["workflow_elapsed_ms"] = {"other": 5000.}
    elif change == "receipt":
        (arm / "replay/native_requests.jsonl").write_text("{}\n")
    elif change in ("input_total", "output_total"):
        key = "prompt_tokens" if change == "input_total" else "output_tokens"
        plan["source_plan"][key] += 1
        result["source_plan"][key] += 1
    if change is None:
        assert comparison.validate_result(plan, result, arm, live_control=True) == {
            "r": (12, 3, "prompt", "output", {"type": "stop", "matched": 9}),
        }
    else:
        with pytest.raises(ValueError):
            comparison.validate_result(plan, result, arm, live_control=True)


@pytest.mark.parametrize("duplicate", (False, True))
def test_receipt_population_rejects_duplicates_and_unverified_tokens(tmp_path, duplicate):
    row = receipt()
    if not duplicate:
        row["exact_tokens_verified"] = False
    path = tmp_path / "requests.jsonl"
    path.write_text((json.dumps(row) + "\n") * (2 if duplicate else 1))
    with pytest.raises(ValueError):
        comparison.receipt_work(path)


def test_cleanup_never_signals_a_reused_pid_and_keeps_known_orphan_children(tmp_path, monkeypatch):
    parent_pid, child_pid = os.getpid(), os.getppid()
    parent = {"pid": parent_pid, "ppid": 1, "start_ticks": "old"}
    child = {"pid": child_pid, "ppid": 1, "start_ticks": "child"}
    current = {
        parent_pid: {"pid": parent_pid, "ppid": 1, "start_ticks": "reused"},
        child_pid: child,
    }
    monkeypatch.setattr(comparison.reservation, "process", current.get)
    monkeypatch.setattr(comparison.reservation, "alive", lambda row: (
        row["pid"] in current and current[row["pid"]]["start_ticks"] == row["start_ticks"]
    ))
    signaled = []

    def signal_owned(pid, sig):
        signaled.append(pid)
        current.pop(pid)

    monkeypatch.setattr(comparison.os, "kill", signal_owned)
    comparison.cleanup(tmp_path, [parent, child], "completed_test")
    assert signaled == [child_pid]
    assert current[parent_pid]["start_ticks"] == "reused"
    report = json.loads((tmp_path / "owned_process_cleanup.json").read_bytes())
    assert report["reason"] == "completed_test" and report["remaining_processes"] == []


@pytest.mark.parametrize("failure", (None, "driver", "changed_work", "capacity"))
def test_supervisor_runs_cold_controls_and_stops_on_failures_before_advancing(
    reference, tmp_path, monkeypatch, failure,
):
    registration = tmp_path / "registered.json"
    run = tmp_path / "run"
    plan = {
        "run_root": str(run), "order": list(comparison.ORDER), "port": 18554,
        "reference_environment": reference["arms"]["full"],
        "engine_checkout": str(tmp_path / "engine"),
        "source_arm": str(tmp_path / "source"),
        "source_capacity": {"device_full_tokens": 1797968},
        "source_server_parameters": {"random_seed": 21, "max_running_requests": 8},
        "source_plan": {"requests": 1, "prompt_tokens": 12, "output_tokens": 3},
        "token_source_sha256": "tokens", "workflow_ids": ["w"],
        "quality_exclusions": [], "scope": "test",
    }
    registration.write_text(json.dumps(plan))
    root = {"pid": 100, "ppid": 1, "start_ticks": "root", "state": "S"}
    scheduler = {"pid": 101, "ppid": 100, "start_ticks": "scheduler", "state": "S"}
    driver_identity = {"pid": 102, "ppid": 1, "start_ticks": "driver", "state": "S"}
    identities = {row["pid"]: row for row in (root, scheduler, driver_identity)}
    current = []
    launched, cleaned, configurations = [], [], []

    class Process:
        def __init__(self, pid, code):
            self.pid, self.code = pid, code

        def poll(self):
            return self.code

        def wait(self):
            return self.code

    def popen(command, **kwargs):
        if command[0] == "bash":
            label = Path(kwargs["env"]["RUN_ROOT"]).name
            arm = run / label
            current[:] = [label]
            launched.append(("server", label, kwargs["env"]))
            (arm / "server/native_telemetry_ready.json").write_text(json.dumps({"scheduler_pid": 101}))
            capacity = {"device_full_tokens": 123} if failure == "capacity" else plan["source_capacity"]
            (arm / "server/native_capacity_census.json").write_text(json.dumps({"capacity": capacity}))
            return Process(100, None)
        label = current[0]
        output = Path(command[command.index("--output-dir") + 1])
        output.mkdir()
        row = receipt()
        if failure == "changed_work" and label == "native_matched":
            row["output_sha256"] = "changed"
        path = output / "native_requests.jsonl"
        path.write_text(json.dumps(row) + "\n")
        result = {
            "causal_execution_completed": True, "exact_tokens_verified": True,
            "original_native_stopping_verified": True,
            "native_session_http_lifecycle_completed": True,
            "live_control_events_delivered": "--event-socket" in command,
            "completed_requests": 1, "source_plan": plan["source_plan"],
            "token_source": {"token_source_sha256": "tokens"},
            "workflow_elapsed_ms": {"w": 5000.},
            "native_requests_sha256": sha256_file(path),
            "started_monotonic_ms": 100., "finished_monotonic_ms": 5200.,
        }
        (output / "summary.json").write_text(json.dumps(result))
        launched.append(("driver", label, command))
        return Process(102, 1 if failure == "driver" else 0)

    def get(url, **_kwargs):
        if url.endswith("/health"):
            return NS(is_success=True)
        return NS(json=lambda: {
            **plan["source_server_parameters"],
            "enable_beliefkv_admission": current[0] != "native_matched",
            "enable_custom_logit_processor": True, "enable_session_radix_cache": True,
        })

    monkeypatch.setattr(comparison.subprocess, "Popen", popen)
    monkeypatch.setattr(comparison.subprocess, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(comparison.httpx, "get", get)
    monkeypatch.setattr(comparison.comparison, "verify_sources", lambda _plan: None)
    monkeypatch.setattr(comparison.fresh, "check_hold", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(comparison.qualified, "resources", lambda _port: {"ready": True})
    monkeypatch.setattr(comparison.qualified, "command", lambda _command: "101")
    monkeypatch.setattr(comparison.qualified, "last_runtime_state", lambda _path: {})
    monkeypatch.setattr(comparison.comparison, "observe_arm", lambda mode, *_args, **_kwargs: configurations.append(mode))
    monkeypatch.setattr(comparison.reservation, "process", identities.get)
    monkeypatch.setattr(comparison, "owned_processes", lambda rows: (
        [root, scheduler, *(row for row in rows if row["pid"] == 102)]
        if rows else []
    ))
    monkeypatch.setattr(comparison, "cleanup", lambda arm, rows, reason: cleaned.append(
        (arm.name, [row["pid"] for row in rows], reason),
    ))
    monkeypatch.setattr(comparison.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(comparison.signal, "signal", lambda *_args: None)
    comparison.watch(registration)
    state = json.loads(registration.with_suffix(".state.json").read_bytes())
    if failure is None:
        assert state["completed_arms"] == list(comparison.ORDER)
        assert state["status"] == "collected_performance_review_pending"
        assert len(cleaned) == 4
        report = json.loads((run / "causal_comparison.json").read_bytes())
        assert report["all_normal_tails_retained"] and not report["performance_accepted"]
        assert all(row["mean_jct_ms"] == 5000. for row in report["results"].values())
        commands = [row for row in launched if row[0] == "driver"]
        assert [("--event-socket" in command) for _, _, command in commands] == [True, False, True, True]
        assert "join_priority_only" in configurations
    else:
        assert state["status"] == "stopped_for_investigation"
        assert not state.get("performance_accepted")
        assert all(label not in state["completed_arms"] for label in ("join_priority_only", "no_background_prepare"))
        assert cleaned[-1][2] == "causal_comparison_failure_or_stop"
        if failure == "changed_work":
            assert state["completed_arms"] == ["full"]
            assert "work changed" in state["error"]
        elif failure == "capacity":
            assert not any(row[0] == "driver" for row in launched)
