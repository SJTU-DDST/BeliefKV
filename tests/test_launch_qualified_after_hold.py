from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest

from scripts import launch_qualified_after_hold as launcher
from tests.test_resume_semantic_h2d_ab import plan


def current_plan():
    configuration = plan()
    configuration.update(
        root_count=36,
        server_running=8,
        fanout_profile="native_in_graph_nested_2to4",
        context_tokens=262144,
        context_lifecycle={
            "window_tokens": 262144, "compression_trigger_ratio": .95,
            "tokenizer_path": "/models/tokenizer",
        },
        workflow_arrival_batch_size=0,
        workflow_arrival_batch_interval_ms=0,
        join_prefetch_trigger="report_service_or_eos",
        native_reference_run="/old-native",
        native_reference_timeline="/old-native/timeline.json",
        native_reference_cache="/old-native/cache.json",
        chat_token_block_cache_by_arm={"native": False, "predictive_h2d": True},
        arrival_schedule=[
            {"instance_id": f"task-{index}", "offset_seconds": 0.}
            for index in range(36)
        ],
    )
    return configuration


def policy():
    return {
        "join_prefetch_trigger": "selective_report_or_eos",
        "selective_prediction_min_missing_full_bytes": 512 * 1024**2,
        "selective_prediction_interval_ms": 200,
        "selective_prediction_max_candidates": 4,
        "selective_prediction_max_advance_ms": 1000,
        "eos_to_native_arrival_startup_floor_ms": 50,
        "execution_handoff": False,
        "observed_eos_requires_service_forecast": False,
        "mamba_prepare": False,
    }


def candidate():
    return {
        "environment": launcher.environment(
            current_plan(), {"latest_policy": policy()}, Path("/frozen"), Path("/engine"),
        ),
    }


@pytest.fixture
def one_time_hold(tmp_path, monkeypatch):
    for name, relative in (
        ("OPS", "."), ("HOLD", "hold.json"), ("PAUSE", "pause"),
        ("QUALIFIED", "qualified.json"), ("REGISTRATION", "registration.json"),
        ("STATE", "state.json"), ("LOG", "watch.log"), ("DRIVER_LOG", "driver.log"),
        ("RUN", "run"),
    ):
        monkeypatch.setattr(launcher, name, tmp_path / relative)
    launcher.PAUSE.write_text("User hold for CPU-only work.\n")
    hold = {
        "automatic_resume_allowed": True,
        "gpu_not_before": launcher.MINIMUM_TIME.isoformat(),
    }
    launcher.HOLD.write_text(json.dumps(hold))
    marker = launcher.PAUSE.stat()
    registration = {
        "pause_sha256": launcher.sha256_file(launcher.PAUSE),
        "pause_text": launcher.PAUSE.read_text(),
        "pause_identity": [marker.st_dev, marker.st_ino, marker.st_uid],
        "operator_source_sha256": launcher.sha256_file(Path(launcher.__file__)),
    }
    launcher.REGISTRATION.write_text(json.dumps(registration))
    monkeypatch.setattr(launcher, "process", Mock(return_value={"pid": 123, "start_ticks": "9"}))
    monkeypatch.setattr(launcher, "now", lambda: launcher.MINIMUM_TIME - timedelta(seconds=1))
    return hold, registration


def test_latest_policy_overrides_stale_plan_without_changing_workload():
    configuration = current_plan()
    original = deepcopy(configuration)
    env = launcher.environment(
        configuration, {"latest_policy": policy()}, Path("/frozen"), Path("/new-engine"),
    )
    assert env["JOIN_PREFETCH_TRIGGER"] == "selective_report_or_eos"
    assert env["EOS_PROTOCOL_WINDOW_MS"] == "50"
    assert env["JOIN_PREDICTION_MIN_FULL_BYTES"] == str(512 * 1024**2)
    assert env["JOIN_PREDICTION_INTERVAL_MS"] == "200"
    assert env["JOIN_PREDICTION_MAX_CANDIDATES"] == "4"
    assert env["PREFETCH_LEAD_MS"] == "1000"
    assert env["SGLANG_SOURCE_CHECKOUT"] == "/new-engine"
    assert env["EXECUTION_HANDOFF"] == "0"
    assert env["ROOT_COUNT"] == "36"
    assert env["MAX_RUNNING_REQUESTS"] == "8"
    assert env["SERVER_RANDOM_SEED"] == "21"
    assert env["MODEL_CONTEXT_TOKENS"] == env["CONTEXT_WINDOW_TOKENS"] == "262144"
    assert env["CONTEXT_COMPRESSION_TRIGGER_RATIO"] == "0.95"
    assert env["ARRIVAL_BATCH_SIZE"] == env["ARRIVAL_BATCH_INTERVAL_MS"] == "0"
    assert env["HOST_SPLIT"] == "80:20"
    assert env["ARM_ORDER"] == "predictive_h2d native"
    assert env["REQUIRE_DELEGATION_SHAPE"] == "1"
    assert env["PREPARE_HOST"] == "1"
    assert env["PREDICTIVE_CHAT_TOKEN_BLOCK_CACHE"] == "1"
    assert env["NATIVE_CHAT_TOKEN_BLOCK_CACHE"] == "0"
    assert all(env[key] == "" for key in (
        "NATIVE_REFERENCE_RUN", "NATIVE_REFERENCE_TIMELINE", "NATIVE_REFERENCE_CACHE",
    ))
    assert configuration == original


def test_eos_only_policy_overrides_a_stale_prediction_plan():
    configuration = current_plan()
    proof = {**policy(), "join_prefetch_trigger": "observed_eos"}
    env = launcher.environment(
        configuration, {"latest_policy": proof}, Path("/frozen"), Path("/engine"),
    )
    assert env["JOIN_PREFETCH_TRIGGER"] == "observed_eos"
    assert configuration["join_prefetch_trigger"] == "report_service_or_eos"
    assert env["PREPARE_HOST"] == env["ENABLE_TOOL_TIMING"] == "1"
    assert env["BORROWABLE_PARENT_HEADROOM"] == "1"
    assert env["EXECUTION_HANDOFF"] == "0"


def test_qualified_server_seed_overrides_a_stale_plan_for_a_fresh_iteration():
    configuration = current_plan()
    configuration["server_random_seed"] = 1234
    proof = {**policy(), "server_random_seed": 21}
    env = launcher.environment(
        configuration, {"latest_policy": proof}, Path("/frozen"), Path("/engine"),
    )
    assert env["SERVER_RANDOM_SEED"] == "21"
    assert configuration["server_random_seed"] == 1234


def test_batch12_freezes_both_arms_with_the_same_running_cap_and_seed():
    configuration = current_plan()
    configuration["server_running"] = 12
    proof = {**policy(), "server_running": 12, "server_random_seed": 21}
    env = launcher.environment(
        configuration, {"latest_policy": proof}, Path("/frozen"), Path("/engine"),
    )
    assert env["MAX_RUNNING_REQUESTS"] == "12"
    assert env["SERVER_RANDOM_SEED"] == env["SAMPLING_SEED"] == "21"
    assert env["ARM_ORDER"] == "predictive_h2d native"
    assert env["NATIVE_REFERENCE_RUN"] == ""
    assert configuration["server_running"] == 12


@pytest.mark.parametrize("qualified,planned", ((12, 8), (8, 12), (16, 16)))
def test_batch_change_requires_matching_supported_qualification(qualified, planned):
    configuration = current_plan()
    configuration["server_running"] = planned
    with pytest.raises(ValueError, match="36-root contract"):
        launcher.environment(
            configuration, {"latest_policy": {**policy(), "server_running": qualified}},
            Path("/frozen"), Path("/engine"),
        )


@pytest.mark.parametrize("field,value", (
    ("root_count", 24), ("server_running", 48), ("host_split", "70:30"),
    ("fanout_profile", "native_dynamic_1to4"), ("context_tokens", 131072),
    ("context_lifecycle", {"window_tokens": 262144, "compression_trigger_ratio": .4}),
    ("arrival_schedule", [{"instance_id": "task", "offset_seconds": 0}]),
    ("arrival_schedule", [
        {"instance_id": f"task-{index}", "offset_seconds": index}
        for index in range(36)
    ]),
))
def test_mismatched_common_workload_blocks_new_freeze(field, value):
    configuration = current_plan()
    configuration[field] = value
    with pytest.raises(ValueError, match="36-root contract"):
        launcher.environment(configuration, {"latest_policy": policy()}, Path("/a"), Path("/b"))


def test_driver_clears_inherited_beliefkv_overrides():
    inherited = {
        "PATH": "/bin", "CUDA_VISIBLE_DEVICES": "3",
        "BELIEFKV_JOIN_PREFETCH_TRIGGER": "report_service_or_eos",
        "BELIEFKV_PHYSICAL_ACTIONS_DISABLED": "1",
        "BELIEFKV_UNUSED": "inherited",
    }
    env = launcher.driver_environment(candidate(), inherited)
    assert env["PATH"] == "/bin"
    assert env["CUDA_VISIBLE_DEVICES"] == "0"
    assert env["JOIN_PREFETCH_TRIGGER"] == "selective_report_or_eos"
    assert env["BELIEFKV_EXPERIMENT_PAUSE_FILE"] == str(launcher.PAUSE)
    assert "BELIEFKV_PHYSICAL_ACTIONS_DISABLED" not in env
    assert "BELIEFKV_UNUSED" not in env
    assert inherited["CUDA_VISIBLE_DEVICES"] == "3"


@pytest.mark.parametrize("offset,expected", ((-1, False), (0, True), (1, True)))
def test_hold_opens_only_at_authorized_deadline(one_time_hold, offset, expected):
    hold, registration = one_time_hold
    assert launcher.require_hold(
        hold, registration["pause_sha256"],
        launcher.MINIMUM_TIME + timedelta(seconds=offset), registration["pause_identity"],
    ) is expected


@pytest.mark.parametrize("deadline", (
    "2026-10-20T06:00:00+08:00", "2026-10-21T06:00:00",
))
def test_stale_or_naive_deadline_is_rejected(one_time_hold, deadline):
    hold, registration = one_time_hold
    hold["gpu_not_before"] = deadline
    with pytest.raises(launcher.HoldChanged, match="earlier"):
        launcher.require_hold(hold, registration["pause_sha256"], launcher.MINIMUM_TIME)


def test_later_user_deadline_is_respected(one_time_hold):
    hold, registration = one_time_hold
    hold["gpu_not_before"] = (launcher.MINIMUM_TIME + timedelta(hours=2)).isoformat()
    assert not launcher.require_hold(hold, registration["pause_sha256"], launcher.MINIMUM_TIME)


@pytest.mark.parametrize("change", ("disabled", "changed", "replaced", "removed"))
def test_newer_hold_never_gets_cleared(one_time_hold, change):
    hold, registration = one_time_hold
    if change == "disabled":
        hold["automatic_resume_allowed"] = False
    elif change == "changed":
        launcher.PAUSE.write_text("New user hold")
    elif change == "replaced":
        replacement = launcher.PAUSE.with_suffix(".new")
        replacement.write_text(registration["pause_text"])
        replacement.replace(launcher.PAUSE)
    else:
        launcher.PAUSE.unlink()
    with pytest.raises(launcher.HoldChanged):
        launcher.require_hold(
            hold, registration["pause_sha256"], launcher.MINIMUM_TIME,
            registration["pause_identity"],
        )
    assert launcher.PAUSE.exists() is (change != "removed")


def test_before_deadline_watcher_never_touches_gpu_or_preloads(one_time_hold, monkeypatch):
    calls = []
    for name in ("resources", "verify_latest", "machine_helper"):
        monkeypatch.setattr(launcher, name, Mock(side_effect=AssertionError(name)))
    monkeypatch.setattr(launcher.subprocess, "Popen", Mock(side_effect=AssertionError("Popen")))

    def interrupt_wait(seconds):
        calls.append(json.loads(launcher.STATE.read_bytes()))
        raise launcher.HoldChanged("test stops the CPU-only wait")

    monkeypatch.setattr(launcher.time, "sleep", interrupt_wait)
    launcher.watch()
    assert calls[0]["status"] == "waiting_until_authorized_time"
    assert launcher.PAUSE.is_file()
    assert not launcher.RUN.exists()
    assert not launcher.DRIVER_LOG.exists()


def test_changed_hold_stops_watcher_without_resource_probe(one_time_hold, monkeypatch):
    launcher.PAUSE.write_text("Do not start; new user hold")
    monkeypatch.setattr(launcher, "resources", Mock(side_effect=AssertionError("GPU probe")))
    launcher.watch()
    state = json.loads(launcher.STATE.read_bytes())
    assert state["status"] == "cancelled_hold_changed"
    assert launcher.PAUSE.read_text() == "Do not start; new user hold"


def test_registration_is_cpu_only_and_binds_marker_identity(one_time_hold, monkeypatch):
    launcher.REGISTRATION.unlink()
    popen = Mock(return_value=Mock(pid=321))
    monkeypatch.setattr(launcher.subprocess, "Popen", popen)
    launcher.register()
    args, kwargs = popen.call_args
    assert args[0][-1] == "--watch"
    assert kwargs["env"]["CUDA_VISIBLE_DEVICES"] == "99999"
    record = json.loads(launcher.REGISTRATION.read_bytes())
    marker = launcher.PAUSE.stat()
    assert record["pause_identity"] == [marker.st_dev, marker.st_ino, marker.st_uid]
    assert record["operator_source_sha256"] == launcher.sha256_file(Path(launcher.__file__))
    assert not launcher.RUN.exists()
    assert not launcher.DRIVER_LOG.exists()


def test_registration_preserves_existing_schedule(one_time_hold, monkeypatch):
    popen = Mock()
    monkeypatch.setattr(launcher.subprocess, "Popen", popen)
    with pytest.raises(FileExistsError, match="preserve"):
        launcher.register()
    popen.assert_not_called()


def runtime_state():
    return {
        "physical_disabled": False, "join_prefetch_trigger": "selective_report_or_eos",
        "execution_handoff": False,
        "selective_join_prefetch": {
            "enabled": True, "min_full_bytes": 512 * 1024**2,
            "interval_ms": 200., "max_candidates": 4,
        },
    }


def test_live_runtime_accepts_current_policy_and_unavailable_startup_observation():
    frozen = candidate()
    assert launcher.runtime_failure(frozen, None, None) is None
    assert launcher.runtime_failure(frozen, {}, runtime_state()) is None


@pytest.mark.parametrize("enabled", (False, True, None))
def test_live_eos_only_runtime_requires_prediction_to_be_disabled(enabled):
    frozen = candidate()
    frozen["environment"]["JOIN_PREFETCH_TRIGGER"] = "observed_eos"
    runtime = runtime_state()
    runtime["join_prefetch_trigger"] = "observed_eos"
    runtime["selective_join_prefetch"] = {"enabled": enabled}
    expected = None if enabled is False else "frozen_runtime_configuration_mismatch"
    assert launcher.runtime_failure(frozen, {}, runtime) == expected


@pytest.mark.parametrize("field,value", (
    ("join_prefetch_trigger", "report_service_or_eos"), ("execution_handoff", True),
    ("selective_join_prefetch", {"enabled": False}),
))
def test_live_runtime_detects_configuration_override(field, value):
    runtime = runtime_state()
    runtime[field] = value
    assert launcher.runtime_failure(candidate(), {}, runtime) == "frozen_runtime_configuration_mismatch"


def test_live_runtime_detects_disabled_physical_actions():
    runtime = {**runtime_state(), "physical_disabled": True}
    assert launcher.runtime_failure(candidate(), {}, runtime) == "physical_actions_disabled"


@pytest.mark.parametrize("field", ("writer_error", "failed_records", "dropped_records"))
def test_writer_failures_do_not_get_reported_as_success(field):
    assert launcher.runtime_failure(
        candidate(), {field: 1}, runtime_state(),
    ) == "native_telemetry_failure"


def test_runtime_tail_skips_torn_record_and_reads_last_valid_state(tmp_path):
    path = tmp_path / "observations.jsonl"
    path.write_bytes(
        b"x" * (2 * 1024**2 + 10) + b"\n"
        + json.dumps({"event": "admission_runtime_state", "counts": {"latest": 1}}).encode()
        + b'\n{"event":"other"}\n{"event":"admission_runtime_state","counts":'
    )
    assert launcher.last_runtime_state(path)["counts"] == {"latest": 1}
    assert launcher.last_runtime_state(tmp_path / "missing") is None


@pytest.mark.parametrize("xml,count", (
    ('<testsuite><testcase classname="a" name="one"/></testsuite>', 2),
    ('<testsuite><testcase classname="a" name="one"><skipped/></testcase></testsuite>', 1),
    ('<testsuite><testcase classname="a" name="one"><failure/></testcase></testsuite>', 1),
    ('<testsuite><testcase classname="a" name="one"/><testcase classname="a" name="one"/></testsuite>', 2),
))
def test_cpu_qualification_rejects_missing_failed_or_duplicate_cases(tmp_path, xml, count):
    path = tmp_path / "cpu.xml"
    path.write_text(xml)
    with pytest.raises(ValueError, match="CPU qualification"):
        launcher.checks(path, count)


def test_cpu_qualification_accepts_distinct_passing_cases(tmp_path):
    path = tmp_path / "cpu.xml"
    path.write_text('<testsuite><testcase classname="a" name="one"/></testsuite>')
    launcher.checks(path, 1)


@pytest.fixture
def cpu_qualification(tmp_path, monkeypatch):
    source = tmp_path / "source.py"
    source.write_text("value = 1\n")
    xml = tmp_path / "cpu.xml"
    xml.write_text('<testsuite><testcase classname="cpu" name="contract"/></testsuite>')
    proof = {
        "status": "cpu_qualified_gpu_validation_pending",
        "suites": {"application": {
            "report": str(xml), "distinct_current_source_cpu_cases": 1,
            "source_sha256": {str(source): launcher.sha256_file(source)},
        }},
        "source_sha256": {},
        "candidate_engine": str(tmp_path / "engine"),
        "canonical_patch": {"actual_tree": "qualified-tree"},
        "latest_policy": policy(),
    }
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(proof))
    monkeypatch.setattr(launcher, "_require_clean", Mock())
    monkeypatch.setattr(launcher, "command", Mock(return_value="main"))
    monkeypatch.setattr(launcher, "git_source_state", Mock(return_value={
        "canonical_patch": {"actual_tree": "qualified-tree"},
    }))
    return path, proof, source


@pytest.mark.parametrize("corruption", ("source", "policy", "engine", "unqualified"))
def test_current_cpu_rejects_stale_source_or_incompatible_policy(cpu_qualification, corruption):
    path, proof, source = cpu_qualification
    if corruption == "source":
        source.write_text("value = 2\n")
    elif corruption == "policy":
        proof["latest_policy"]["observed_eos_requires_service_forecast"] = True
    elif corruption == "engine":
        proof["canonical_patch"]["actual_tree"] = "different-tree"
    else:
        proof["status"] = "gpu_pending_unqualified"
    path.write_text(json.dumps(proof))
    with pytest.raises(ValueError):
        launcher.current_cpu({"latest_cpu_qualification": str(path)})


def test_current_cpu_validates_bound_sources_before_freezing(cpu_qualification):
    path, proof, source = cpu_qualification
    observed, current, engine = launcher.current_cpu({"latest_cpu_qualification": str(path)})
    assert observed == path
    assert current["suites"]["application"]["source_sha256"][str(source)] == launcher.sha256_file(source)
    assert engine["actual_tree"] == proof["canonical_patch"]["actual_tree"]


@pytest.mark.parametrize("trigger", ("observed_eos", "report_service_or_eos", "unknown"))
def test_current_cpu_accepts_qualified_eos_only_and_rejects_other_modes(
    cpu_qualification, trigger,
):
    path, proof, _ = cpu_qualification
    proof["latest_policy"]["join_prefetch_trigger"] = trigger
    path.write_text(json.dumps(proof))
    if trigger == "observed_eos":
        _, current, _ = launcher.current_cpu({"latest_cpu_qualification": str(path)})
        assert current["latest_policy"]["join_prefetch_trigger"] == trigger
    else:
        with pytest.raises(ValueError, match="authorized current policy"):
            launcher.current_cpu({"latest_cpu_qualification": str(path)})


def test_changed_qualification_refreshes_snapshot_in_fresh_cpu_interpreter(
    one_time_hold, cpu_qualification, tmp_path, monkeypatch,
):
    path, proof, _ = cpu_qualification
    monkeypatch.setattr(launcher, "current_cpu", Mock(return_value=(path, proof, {})))
    monkeypatch.setattr(launcher, "command", Mock(return_value="latest-main"))
    frozen = {
        "code_commit": "latest-main", "cpu_qualification": str(path),
        "cpu_qualification_sha256": launcher.sha256_file(path),
        "execution_checkout": str(tmp_path / "app"),
        "engine_checkout": str(tmp_path / "engine"),
        "source_sha256": {}, "external_inputs": [],
    }
    launcher.QUALIFIED.write_text(json.dumps({**frozen, "code_commit": "old-main"}))

    def refresh(*args, **kwargs):
        launcher.QUALIFIED.write_text(json.dumps(frozen))

    run = Mock(side_effect=refresh)
    monkeypatch.setattr(launcher.subprocess, "run", run)
    assert launcher.verify_latest() == frozen
    arguments, kwargs = run.call_args
    assert arguments[0][-1] == "--prepare"
    assert kwargs["env"]["CUDA_VISIBLE_DEVICES"] == "99999"
    assert kwargs["check"] is True
    run.reset_mock()
    assert launcher.verify_latest() == frozen
    run.assert_not_called()


def test_native_engine_snapshot_has_isolated_but_identical_sources(tmp_path, monkeypatch):
    repo, original = tmp_path / "app", tmp_path / "native"
    original.mkdir()
    patch = repo / "patches/sglang-v0.5.20-beliefkv-staging.patch"
    patch.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(original)], check=True)
    native_source = original / "source.py"
    native_source.write_text("value = 1\n")
    subprocess.run(["git", "-C", str(original), "add", "source.py"], check=True)
    subprocess.run([
        "git", "-C", str(original), "-c", "user.name=CPU Test",
        "-c", "user.email=cpu@example.invalid", "commit", "-qm", "upstream",
    ], check=True)
    native_source.write_text("value = 2\n")
    patch.write_bytes(subprocess.check_output(["git", "-C", str(original), "diff"]))
    canonical = launcher.git_source_state(original, canonical_patch=patch)["canonical_patch"]
    proof = {
        "candidate_engine": str(original),
        "suites": {"engine": {"source_sha256": {
            str(native_source): launcher.sha256_file(native_source),
        }}},
    }
    monkeypatch.setattr(launcher, "REPO", repo)
    isolated, observed = launcher.frozen_engine(proof, canonical)
    assert isolated.resolve() != original.resolve()
    assert observed["actual_tree"] == canonical["actual_tree"]
    assert (isolated / "source.py").read_text() == "value = 2\n"
    native_source.write_text("value = 3\n")
    assert (isolated / "source.py").read_text() == "value = 2\n"


@pytest.fixture
def permitted_launch(one_time_hold, monkeypatch):
    monkeypatch.setattr(launcher, "now", lambda: launcher.MINIMUM_TIME)
    frozen = {**candidate(), "execution_checkout": "/frozen"}
    monkeypatch.setattr(launcher, "verify_latest", Mock(return_value=frozen))
    monkeypatch.setattr(launcher, "resources", Mock(return_value={"ready": True}))
    monkeypatch.setattr(launcher, "machine_helper", Mock(return_value=Mock()))
    monkeypatch.setattr(launcher.subprocess, "run", Mock())
    monkeypatch.setattr(launcher.time, "sleep", Mock())
    driver = Mock(pid=321)
    driver.poll.return_value = 0
    driver.wait.return_value = 0
    monkeypatch.setattr(launcher.subprocess, "Popen", Mock(return_value=driver))
    return frozen, driver


def test_launch_rechecks_resources_and_exact_hold_immediately_before_driver(permitted_launch):
    launcher.watch()
    assert launcher.resources.call_count >= 4
    assert launcher.verify_latest.call_count == 4
    assert launcher.subprocess.run.call_args.args[0][-6:] == [
        "--memory-policy", "preferred", "--node", "1", "--size-gb", "200",
    ]
    kwargs = launcher.subprocess.Popen.call_args.kwargs
    assert kwargs["env"]["CUDA_VISIBLE_DEVICES"] == "0"
    assert kwargs["env"]["JOIN_PREFETCH_TRIGGER"] == "selective_report_or_eos"
    assert not launcher.PAUSE.exists()
    assert json.loads(launcher.STATE.read_bytes())["status"] == "completed"
    permitted_launch[1].wait.assert_called_once()


def test_late_user_pause_is_preserved_without_starting_driver(permitted_launch):
    checks = []

    def resource_check(port):
        checks.append(port)
        if len(checks) == 4:
            launcher.PAUSE.write_text("A new user hold")
        return {"ready": True}

    launcher.resources.side_effect = resource_check
    launcher.watch()
    launcher.subprocess.Popen.assert_not_called()
    assert launcher.PAUSE.read_text() == "A new user hold"
    assert json.loads(launcher.STATE.read_bytes())["status"] == "cancelled_hold_changed"


def test_failed_launch_restores_user_pause(permitted_launch):
    marker = launcher.PAUSE.read_text()
    launcher.subprocess.Popen.side_effect = OSError("cannot launch driver")
    with pytest.raises(OSError, match="cannot launch"):
        launcher.watch()
    assert launcher.PAUSE.read_text() == marker
    assert json.loads(launcher.STATE.read_bytes())["status"] == "preflight_or_launch_failed"
