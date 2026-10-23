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
    for name, digest in (
        ("BELIEFKV_TOOL_TIMING_ARTIFACT", "BELIEFKV_TOOL_TIMING_SHA256"),
        ("BELIEFKV_H2D_SEED", "BELIEFKV_H2D_SEED_SHA256"),
        ("BELIEFKV_TRANSFER_SERVICE_SEED", "BELIEFKV_TRANSFER_SERVICE_SEED_SHA256"),
    ):
        assert bool(env[name]) is custom_kv
        assert bool(env[digest]) is custom_kv
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


def test_generated_tool_model_settings_are_consumed_by_the_real_runtime_constructor(
    reference, tmp_path, monkeypatch,
):
    import beliefkv.runtime.sglang_v0520_runtime as native

    env, _, _ = comparison.environment(
        reference, "full", tmp_path, tmp_path, tmp_path, 18554, tmp_path,
    )
    for name, value in env.items():
        if name.startswith("BELIEFKV_"):
            monkeypatch.setenv(name, value)
    for name in (
        "BELIEFKV_H2D_SEED", "BELIEFKV_H2D_SEED_SHA256",
        "BELIEFKV_TRANSFER_SERVICE_SEED", "BELIEFKV_TRANSFER_SERVICE_SEED_SHA256",
        "BELIEFKV_ADMISSION_OPPORTUNITY_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    checked = []
    monkeypatch.setattr(native, "validate_tool_timing_artifact", lambda path, **kwargs: checked.append(
        (path, kwargs["expected_sha256"], kwargs["model_path"]),
    ))
    runtime = native.NativeAdmissionRuntime(
        event_socket_path=str(tmp_path / "cpu-events.sock"), model_path="cpu-contract-model",
    )
    try:
        artifact = reference["arms"]["full"]["TOOL_TIMING_ARTIFACT"]
        assert checked == [(artifact, sha256_file(Path(artifact)), "cpu-contract-model")]
        assert runtime._tool_timing_only and runtime.enable_tool_prefetch
    finally:
        runtime.close()


def test_no_tool_prefetch_changes_only_tool_h2d_and_preserves_offload_settings(reference, tmp_path):
    original, original_flags, original_socket = comparison.environment(
        reference, "full", tmp_path, tmp_path, tmp_path, 18554, tmp_path,
    )
    disabled, flags, event_socket = comparison.environment(
        reference, "no_tool_prefetch", tmp_path, tmp_path, tmp_path, 18554, tmp_path,
    )
    assert {
        name for name in original.keys() | disabled.keys()
        if original.get(name) != disabled.get(name)
    } == {"BELIEFKV_ENABLE_TOOL_PREFETCH"}
    assert disabled["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "0"
    assert disabled["BELIEFKV_INTERNAL_COMPARISON_MODE"] == "full"
    assert disabled["PREPARE_HOST"] == disabled["BELIEFKV_ENABLE_BACKGROUND_PREPARE"] == "1"
    assert disabled["ENABLE_TOOL_TIMING"] == disabled["BELIEFKV_ONLINE_TOOL_TIMING"] == "1"
    assert disabled["BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH"] == "1"
    assert disabled["BELIEFKV_ENABLE_RESIDENT_FIRST"] == "1"
    assert disabled["BELIEFKV_BORROWABLE_PARENT_HEADROOM"] == "1"
    assert flags == original_flags and event_socket == original_socket


def test_no_tool_prefetch_validates_the_observed_runtime_switch(reference, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        comparison.comparison, "observe_arm",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    comparison.observed_configuration({}, "no_tool_prefetch", tmp_path)
    assert calls == [(("full", tmp_path), {
        "max_running_requests": 8, "runtime_overrides": {"tool_prefetch": False},
    })]


def test_position_control_disables_delayed_sampling_for_every_requested_policy(reference, tmp_path):
    for label in comparison.CONTROLS:
        env, _, _ = comparison.environment(
            reference, label, tmp_path, tmp_path, tmp_path, 18558, tmp_path,
            sampling_mode="position",
        )
        assert env["BELIEFKV_CAUSAL_REPLAY_SAMPLING"] == "position"
        assert env["SGLANG_ENABLE_DELAY_SAMPLE"] == "0"


@pytest.mark.parametrize("jct,duration,accepted", (
    (100., 200., True), (104., 208., True), (106., 200., False),
    (100., 211., False), (80., 180., True),
))
def test_native_source_calibration_checks_jct_and_duration_without_trimming_tails(jct, duration, accepted):
    result = comparison.source_speed_check(
        {"mean_jct_ms": 100., "replay_duration_ms": 200.},
        {"mean_jct_ms": jct, "replay_duration_ms": duration}, .05,
    )
    assert result["accepted"] is accepted
    assert result["replay_to_source_ratios"]["mean_jct_ms"] == jct / 100.


@pytest.mark.parametrize("value", (None, 0, -1, float("nan"), float("inf"), True))
def test_source_calibration_rejects_unmeasured_or_invalid_durations(value):
    with pytest.raises(ValueError):
        comparison.source_speed_check(
            {"mean_jct_ms": 100., "replay_duration_ms": 200.},
            {"mean_jct_ms": 100., "replay_duration_ms": value}, .05,
        )


@pytest.fixture
def predecessor(tmp_path):
    registration = tmp_path / "previous.json"
    previous = {
        "run_root": str(tmp_path / "previous"), "order": ["full", "native_matched"],
        "source_arm": "same-source", "source_plan": {"requests": 1},
        "token_source_sha256": "tokens", "source_capacity": {"full": 10},
        "source_server_parameters": {"random_seed": 21}, "workflow_ids": ["w"],
        "quality_exclusions": [], "quality_exclusion_details": [],
        "reference_environment": {"MAX_RUNNING_REQUESTS": "8"}, "engine_tree": "engine",
    }
    registration.write_text(json.dumps(previous))
    state = {
        "run_root": previous["run_root"], "status": "running_native_matched",
        "completed_arms": ["full"], "original_exit_codes": {"full": 0},
        "watcher": {"pid": 10, "start_ticks": "10"},
    }
    state_path = registration.with_suffix(".state.json")
    state_path.write_text(json.dumps(state))
    return {**previous, "after_registration": str(registration)}, previous, state, state_path


@pytest.mark.parametrize("status,live_pid,expected", (
    ("running_native_matched", 10, False),
    ("starting_native_matched", 10, False),
    ("waiting_resources_native_matched", 10, False),
    ("collected_performance_review_pending", 10, False),
    ("collected_performance_review_pending", 12, False),
    ("collected_performance_review_pending", None, True),
))
def test_followup_waits_for_complete_matrix_and_owned_process_release(
    predecessor, monkeypatch, status, live_pid, expected,
):
    plan, previous, state, path = predecessor
    state.update(
        status=status, completed_arms=previous["order"],
        original_exit_codes={label: 0 for label in previous["order"]},
        owned=[{"pid": 12, "start_ticks": "12"}],
    )
    path.write_text(json.dumps(state))
    monkeypatch.setattr(comparison.reservation, "alive", lambda row: row["pid"] == live_pid)
    assert comparison.predecessor_ready(plan) is expected


@pytest.mark.parametrize("change", (
    "source_plan", "token_source_sha256", "source_capacity", "reference_environment",
    "engine_tree", "run_root", "failed", "dead_supervisor", "missing_arm", "failed_arm",
))
def test_followup_rejects_changed_work_failed_predecessor_and_orphaned_runs(
    predecessor, monkeypatch, change,
):
    plan, previous, state, path = predecessor
    state.update(
        status="collected_performance_review_pending",
        completed_arms=previous["order"],
        original_exit_codes={label: 0 for label in previous["order"]},
    )
    if change in ("source_plan", "token_source_sha256", "source_capacity", "reference_environment", "engine_tree"):
        plan[change] = "changed"
    elif change == "run_root":
        state["run_root"] = "another-run"
    elif change == "failed":
        state["status"] = "stopped_for_investigation"
    elif change == "dead_supervisor":
        state["status"] = "running_native_matched"
    elif change == "missing_arm":
        state["completed_arms"] = ["full"]
    elif change == "failed_arm":
        state["original_exit_codes"]["native_matched"] = 1
    path.write_text(json.dumps(state))
    monkeypatch.setattr(comparison.reservation, "alive", lambda _row: False)
    with pytest.raises(ValueError):
        comparison.predecessor_ready(plan)


def test_followup_compares_actual_per_request_work_with_every_predecessor_arm(
    predecessor, monkeypatch,
):
    plan, previous, state, path = predecessor
    state.update(
        status="collected_performance_review_pending",
        completed_arms=previous["order"],
        original_exit_codes={label: 0 for label in previous["order"]},
    )
    path.write_text(json.dumps(state))
    monkeypatch.setattr(comparison.reservation, "alive", lambda _row: False)
    verified, observed, validated = [], [], []
    monkeypatch.setattr(comparison.comparison, "verify_sources", verified.append)
    monkeypatch.setattr(comparison, "observed_configuration", lambda _plan, label, *_args, **_kwargs: observed.append(label))
    for label in previous["order"]:
        directory = Path(previous["run_root"]) / label / "replay"
        directory.mkdir(parents=True)
        (directory / "summary.json").write_text("{}")
    changed = False

    def validate(_plan, _result, arm, *, live_control):
        validated.append((arm.name, live_control))
        return {"r": ("different" if changed and arm.name == "native_matched" else "same")}

    monkeypatch.setattr(comparison, "validate_result", validate)
    assert comparison.predecessor_work(plan) == {"r": "same"}
    assert verified == [previous]
    assert observed == previous["order"]
    assert validated == [("full", True), ("native_matched", False)]
    changed = True
    with pytest.raises(ValueError, match="request work changed"):
        comparison.predecessor_work(plan)


def receipt():
    return {
        "request_id": "r", "prompt_tokens": 12, "output_tokens": 3,
        "prompt_sha256": "prompt", "output_sha256": "output",
        "native_finish_reason": {"type": "stop", "matched": 9},
        "exact_tokens_verified": True,
    }


@pytest.mark.parametrize("change", (
    None, "instance_as_workflow", "duplicate", "missing", "wrong_count",
    "untyped_eligibility", "no_eligible_work",
))
def test_source_quality_uses_explicit_runtime_identity_and_retains_guard_reasons(change):
    summary = {
        "native_agent_jct_eligible_workflows": 1,
        "workflows": [
            {"workflow_id": "runtime:a", "instance_id": "task_a",
             "native_agent_jct_eligible": True},
            {"workflow_id": "runtime:b", "instance_id": "task_b",
             "native_agent_jct_eligible": False,
             "native_agent_jct_exclusion_reasons": ["guard_intervened_completion"]},
        ],
    }
    if change == "instance_as_workflow":
        summary["workflows"][1]["workflow_id"] = "task_b"
    elif change == "duplicate":
        summary["workflows"].append(dict(summary["workflows"][0]))
    elif change == "missing":
        summary["workflows"].pop()
    elif change == "wrong_count":
        summary["native_agent_jct_eligible_workflows"] = 2
    elif change == "untyped_eligibility":
        summary["workflows"][0]["native_agent_jct_eligible"] = 1
    elif change == "no_eligible_work":
        summary["workflows"][0]["native_agent_jct_eligible"] = False
        summary["native_agent_jct_eligible_workflows"] = 0
    if change is None:
        assert comparison.source_quality(summary, ["runtime:a", "runtime:b"]) == [{
            "workflow_id": "runtime:b", "instance_id": "task_b",
            "reasons": ["guard_intervened_completion"],
        }]
    else:
        with pytest.raises(ValueError, match="qualifications"):
            comparison.source_quality(summary, ["runtime:a", "runtime:b"])


def result_fixture(tmp_path):
    arm = tmp_path / "full"
    replay = arm / "replay"
    replay.mkdir(parents=True)
    path = replay / "native_requests.jsonl"
    path.write_text(json.dumps(receipt()) + "\n")
    plan = {
        "source_plan": {"nodes": 2, "requests": 1, "prompt_tokens": 12, "output_tokens": 3},
        "token_source_sha256": "tokens", "workflow_ids": ["w"],
    }
    result = {
        "causal_execution_completed": True, "exact_tokens_verified": True,
        "original_native_stopping_verified": True,
        "native_session_http_lifecycle_completed": True,
        "live_control_events_delivered": True, "completed_requests": 1,
        "replay_process_python_hash_seed": "21",
        "control_delivery": {"mode": "per_workflow_harness_queued", "workflows": {
            "w": {"accepted_batch_count": 1},
        }},
        "source_plan": dict(plan["source_plan"]),
        "token_source": {"token_source_sha256": "tokens"},
        "workflow_elapsed_ms": {"w": 5000.}, "native_requests_sha256": sha256_file(path),
    }
    return plan, result, arm


@pytest.mark.parametrize("change", (
    None, "incomplete", "stopping", "control", "population", "tokens",
    "workflow", "receipt", "input_total", "output_total", "hash_seed", "control_dropped",
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
    elif change == "hash_seed":
        result["replay_process_python_hash_seed"] = None
    elif change == "control_dropped":
        result["control_delivery"]["workflows"]["w"]["accepted_batch_count"] = 0
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


@pytest.mark.parametrize("mode", (None, "legacy", "position"))
def test_position_population_requires_per_request_evidence_of_the_actual_sampler(tmp_path, mode):
    row = receipt()
    if mode is not None:
        row["native_receipt"] = {"sampling_path": mode}
    path = tmp_path / "receipts.jsonl"
    path.write_text(json.dumps(row) + "\n")
    if mode == "position":
        assert comparison.receipt_work(path, sampling_mode="position")
    else:
        with pytest.raises(ValueError, match="registered position sampler"):
            comparison.receipt_work(path, sampling_mode="position")


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


@pytest.mark.parametrize("failure", (
    None, "driver", "changed_work", "capacity", "no_tool_prefetch",
    "queued_no_tool_prefetch", "failed_predecessor", "source_slowdown",
))
def test_supervisor_runs_cold_controls_and_stops_on_failures_before_advancing(
    reference, tmp_path, monkeypatch, failure,
):
    queued = failure == "queued_no_tool_prefetch"
    failed_predecessor = failure == "failed_predecessor"
    order = (
        ["no_tool_prefetch"] if failure in (
            "no_tool_prefetch", "queued_no_tool_prefetch", "failed_predecessor",
        ) else list(comparison.ORDER)
    )
    if failure == "source_slowdown":
        order = ["native_matched", "no_tool_prefetch"]
    if failure in ("no_tool_prefetch", "queued_no_tool_prefetch"):
        failure = None
    registration = tmp_path / "registered.json"
    run = tmp_path / "run"
    plan = {
        "run_root": str(run), "order": order, "port": 18554,
        "reference_environment": reference["arms"]["full"],
        "engine_checkout": str(tmp_path / "engine"),
        "source_arm": str(tmp_path / "source"),
        "source_capacity": {"device_full_tokens": 1797968},
        "source_server_parameters": {"random_seed": 21, "max_running_requests": 8},
        "source_plan": {"nodes": 2, "requests": 1, "prompt_tokens": 12, "output_tokens": 3},
        "token_source_sha256": "tokens", "workflow_ids": ["w"],
        "quality_exclusions": [], "scope": "test",
    }
    if failure == "source_slowdown":
        plan.update(
            maximum_replay_slowdown=.05,
            source_metrics={"mean_jct_ms": 4000., "replay_duration_ms": 4000.},
        )
    registration.write_text(json.dumps(plan))
    root = {"pid": 100, "ppid": 1, "start_ticks": "root", "state": "S"}
    scheduler = {"pid": 101, "ppid": 100, "start_ticks": "scheduler", "state": "S"}
    driver_identity = {"pid": 102, "ppid": 1, "start_ticks": "driver", "state": "S"}
    identities = {row["pid"]: row for row in (root, scheduler, driver_identity)}
    current = []
    launched, cleaned, configurations = [], [], []
    predecessor_checks = []

    def previous_ready(_plan):
        predecessor_checks.append(1)
        if failed_predecessor:
            raise ValueError("predecessor failed")
        return len(predecessor_checks) >= 3

    def resources(_port):
        if queued:
            assert len(predecessor_checks) >= 3
        return {"ready": True}

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
        assert kwargs["env"]["PYTHONHASHSEED"] == "21"
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
            "replay_process_python_hash_seed": "21",
            "control_delivery": {"mode": (
                "per_workflow_harness_queued" if "--event-socket" in command else "trace_only"
            ), "workflows": {"w": {"accepted_batch_count": 1}} if "--event-socket" in command else {}},
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
    monkeypatch.setattr(comparison.qualified, "resources", resources)
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
    if queued or failed_predecessor:
        monkeypatch.setattr(comparison, "predecessor_ready", previous_ready)
        monkeypatch.setattr(comparison, "predecessor_work", lambda _plan: {
            "r": (12, 3, "prompt", "output", {"type": "stop", "matched": 9}),
        })
    comparison.watch(registration)
    state = json.loads(registration.with_suffix(".state.json").read_bytes())
    if failure is None:
        assert state["completed_arms"] == order
        assert state["status"] == "collected_performance_review_pending"
        assert len(cleaned) == len(order)
        report = json.loads((run / "causal_comparison.json").read_bytes())
        assert report["all_normal_tails_retained"] and not report["performance_accepted"]
        assert all(row["mean_jct_ms"] == 5000. for row in report["results"].values())
        commands = [row for row in launched if row[0] == "driver"]
        assert [("--event-socket" in command) for _, _, command in commands] == [
            label != "native_matched" for label in order
        ]
        if "join_priority_only" in order:
            assert "join_priority_only" in configurations
        if order == ["no_tool_prefetch"]:
            environment = next(row[2] for row in launched if row[0] == "server")
            assert environment["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "0"
            assert environment["BELIEFKV_ENABLE_BACKGROUND_PREPARE"] == "1"
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
        elif failure == "failed_predecessor":
            assert not launched
            assert "predecessor failed" in state["error"]
        elif failure == "source_slowdown":
            assert state["completed_arms"] == ["native_matched"]
            assert not any(row[1] == "no_tool_prefetch" for row in launched)
            check = json.loads((run / "native_matched/source_replay_calibration.json").read_bytes())
            assert check["accepted"] is False
            assert "slowdown tolerance" in state["error"]
