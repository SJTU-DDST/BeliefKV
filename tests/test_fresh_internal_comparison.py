"""A fresh matrix cannot use the GPU early or silently reuse mismatched controls."""

from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from scripts import run_fresh_internal_comparison as launcher


SPEC = Path(__file__).resolve().parents[1] / "configs/migration/next_predictive_baselines_20261022.json"


@pytest.fixture
def held(tmp_path, monkeypatch):
    marker = tmp_path / "pause"
    marker.write_text("GPU is reserved until 2026-10-22 06:00 Beijing.\n")
    monkeypatch.setattr(launcher, "PAUSE", marker)
    stat = marker.stat()
    hold = tmp_path / "hold.json"
    hold.write_text(json.dumps({
        "gpu_not_before": launcher.MINIMUM_TIME.isoformat(), "automatic_resume_allowed": True,
    }))
    plan = {
        "hold_file": str(hold), "pause_sha256": launcher.sha256_file(marker),
        "pause_text": marker.read_text(), "pause_identity": [stat.st_dev, stat.st_ino, stat.st_uid],
        "gpu_not_before": launcher.MINIMUM_TIME.isoformat(), "run_root": str(tmp_path / "run"),
        "order": ["full", "join_priority_only", "native_matched"], "port": 18524,
    }
    return plan, hold


def registration(tmp_path, plan):
    path = tmp_path / "campaign.registration.json"
    path.write_text(json.dumps(plan))
    return path


@pytest.mark.parametrize("seconds,allowed", ((-86400, False), (-1, False), (0, True), (1, True)))
def test_exact_october22_deadline(held, seconds, allowed):
    plan, _ = held
    assert launcher.check_hold(
        plan, released=False, at=launcher.MINIMUM_TIME + timedelta(seconds=seconds),
    ) is allowed
    assert launcher.PAUSE.exists()


@pytest.mark.parametrize("change", ("disabled", "replaced", "changed", "removed", "earlier", "naive"))
def test_newer_or_invalid_hold_is_preserved(held, change):
    plan, hold = held
    config = json.loads(hold.read_bytes())
    if change == "disabled":
        config["automatic_resume_allowed"] = False
    elif change == "replaced":
        replacement = launcher.PAUSE.with_suffix(".new")
        replacement.write_text(plan["pause_text"])
        replacement.replace(launcher.PAUSE)
    elif change == "changed":
        launcher.PAUSE.write_text("User says to continue CPU work.")
    elif change == "removed":
        launcher.PAUSE.unlink()
    else:
        config["gpu_not_before"] = (
            "2026-10-21T06:00:00+08:00" if change == "earlier" else "2026-10-22T06:00:00"
        )
    hold.write_text(json.dumps(config))
    with pytest.raises(launcher.HoldChanged):
        launcher.check_hold(plan, released=False, at=launcher.MINIMUM_TIME)
    assert launcher.PAUSE.exists() is (change != "removed")


def test_extended_hold_delays_start_and_stops_an_existing_campaign(held):
    plan, hold = held
    hold.write_text(json.dumps({
        "gpu_not_before": (launcher.MINIMUM_TIME + timedelta(hours=1)).isoformat(),
        "automatic_resume_allowed": True,
    }))
    assert not launcher.check_hold(plan, released=False, at=launcher.MINIMUM_TIME)
    launcher.PAUSE.unlink()
    with pytest.raises(launcher.HoldChanged, match="extended"):
        launcher.check_hold(plan, released=True, at=launcher.MINIMUM_TIME)


def test_another_pause_during_execution_is_not_cleared(held):
    plan, _ = held
    with pytest.raises(launcher.HoldChanged, match="new user pause"):
        launcher.check_hold(plan, released=True, at=launcher.MINIMUM_TIME)
    assert launcher.PAUSE.read_text() == plan["pause_text"]


def test_before_deadline_watcher_never_probes_gpu_freezes_or_preloads(held, tmp_path, monkeypatch):
    plan, _ = held
    path = registration(tmp_path, plan)
    monkeypatch.setattr(launcher.reservation, "now", lambda: launcher.MINIMUM_TIME - timedelta(seconds=1))
    monkeypatch.setattr(launcher.reservation, "process", Mock(return_value={"pid": 123}))
    for owner, name in (
        (launcher.qualified, "resources"), (launcher, "verify"),
        (launcher.subprocess, "run"), (launcher.subprocess, "Popen"),
    ):
        monkeypatch.setattr(owner, name, Mock(side_effect=AssertionError(name)))

    def pause(_seconds):
        state = json.loads(path.with_suffix(".state.json").read_bytes())
        assert state["status"] == "waiting_until_authorized_time"
        assert not state["completed_arms"]
        raise launcher.HoldChanged("end the CPU-only test wait")

    monkeypatch.setattr(launcher.time, "sleep", pause)
    launcher.watch(path)
    state = json.loads(path.with_suffix(".state.json").read_bytes())
    assert state["status"] == "cancelled_hold_changed"
    assert launcher.PAUSE.read_text() == plan["pause_text"]
    assert not Path(plan["run_root"]).exists()


def test_after_deadline_stale_qualification_cannot_probe_or_clear_hold(held, tmp_path, monkeypatch):
    plan, _ = held
    path = registration(tmp_path, plan)
    monkeypatch.setattr(launcher.reservation, "now", lambda: launcher.MINIMUM_TIME)
    monkeypatch.setattr(launcher.reservation, "process", Mock(return_value={"pid": 123}))
    monkeypatch.setattr(launcher, "verify", Mock(side_effect=ValueError("main advanced")))
    monkeypatch.setattr(launcher.qualified, "resources", Mock(side_effect=AssertionError("GPU probe")))
    launcher.watch(path)
    state = json.loads(path.with_suffix(".state.json").read_bytes())
    assert state["status"] == "stopped_for_investigation"
    assert "main advanced" in state["error"]
    assert launcher.PAUSE.exists()


def test_all_fresh_arms_keep_identical_work_and_frontend_but_different_kv_controls():
    spec = json.loads(SPEC.read_bytes())
    original = deepcopy(spec)
    base = {
        "MAX_RUNNING_REQUESTS": "12", "SAMPLING_SEED": "23",
        "BELIEFKV_NATIVE_PREFILL_ORDER": "1", "BELIEFKV_PHYSICAL_ACTIONS_DISABLED": "1",
        "WORKFLOW_ARRIVAL_SCHEDULE": "/old/schedule",
        "MAX_COMPLETION_TOKENS": "16384", "AGENT_RECURSION_LIMIT": "2048",
    }
    env = launcher.campaign_environment(base, spec, Path("/frozen"), Path("/engine"), 18524)
    assert env["MAX_RUNNING_REQUESTS"] == "8"
    assert env["SAMPLING_SEED"] == env["SERVER_RANDOM_SEED"] == "21"
    assert env["MAX_COMPLETION_TOKENS"] == "16384"
    assert env["AGENT_RECURSION_LIMIT"] == "2048"
    assert env["WORKLOAD_MANIFEST"].startswith("/frozen/configs/")
    assert env["WORKFLOW_ARRIVAL_SCHEDULE"] == ""
    assert "BELIEFKV_PHYSICAL_ACTIONS_DISABLED" not in env
    assert "BELIEFKV_NATIVE_PREFILL_ORDER" not in env
    for mode in spec["order"]:
        arm = arm_environment(env, mode)
        assert arm["MAX_RUNNING_REQUESTS"] == "8"
        assert arm["SERVER_RANDOM_SEED"] == "21"
        assert arm["ROOT_COUNT"] == "36"
        assert arm["NATIVE_CHAT_TOKEN_BLOCK_CACHE"] == arm["PREDICTIVE_CHAT_TOKEN_BLOCK_CACHE"] == "1"
        assert arm["PREPARE_HOST"] == str(int(mode == "full"))
    assert spec == original
    assert base["MAX_RUNNING_REQUESTS"] == "12"


@pytest.mark.parametrize("setting", (None, "0", "1"))
def test_frontier_restore_is_explicit_in_the_spec_and_not_inherited_from_an_old_run(setting):
    spec = json.loads(SPEC.read_bytes())
    if setting is not None:
        spec["common_environment"]["EXECUTION_FRONTIER_RESTORE"] = setting
    env = launcher.campaign_environment(
        {"EXECUTION_FRONTIER_RESTORE": "0"}, spec, Path("/frozen"), Path("/engine"), 18524,
    )
    expected = "1" if setting is None else setting
    assert env["EXECUTION_FRONTIER_RESTORE"] == expected
    assert arm_environment(env, "full")["BELIEFKV_ENABLE_FRONTIER_RESTORE"] == expected
    assert arm_environment(env, "join_priority_only")["BELIEFKV_ENABLE_FRONTIER_RESTORE"] == "0"


@pytest.mark.parametrize("field,value", (
    ("MAX_RUNNING_REQUESTS", "12"), ("ROOT_COUNT", "24"), ("SERVER_RANDOM_SEED", "123"),
    ("PREFETCH_RESIDENCY_PROTECTION", "1"), ("PREFILL_SCHEDULING_POLICY", "workflow"),
))
def test_configuration_mismatch_blocks_freeze(field, value):
    spec = json.loads(SPEC.read_bytes())
    spec["common_environment"][field] = value
    with pytest.raises(ValueError, match="qualified batch8"):
        launcher.campaign_environment({}, spec, Path("/frozen"), Path("/engine"), 18524)


def test_full_is_always_fresh_and_first():
    spec = json.loads(SPEC.read_bytes())
    spec["order"] = ["join_priority_only", "native_matched"]
    with pytest.raises(ValueError, match="start with Full"):
        launcher.campaign_environment({}, spec, Path("/frozen"), Path("/engine"), 18524)


@pytest.mark.parametrize("fault", (
    None, "patch", "engine", "removed_case", "skipped", "failed", "duplicate", "report_changed",
))
def test_replacement_engine_must_retain_and_bind_its_complete_cpu_checks(tmp_path, fault):
    previous, report = tmp_path / "previous.xml", tmp_path / "current.xml"
    previous.write_text('<testsuite><testcase classname="engine" name="old"/></testsuite>')
    added = '<testcase classname="engine" name="new"/>'
    old = '<testcase classname="engine" name="old"/>'
    if fault == "removed_case":
        old = ""
    elif fault == "skipped":
        added = '<testcase classname="engine" name="new"><skipped/></testcase>'
    elif fault == "failed":
        added = '<testcase classname="engine" name="new"><failure/></testcase>'
    elif fault == "duplicate":
        added = old
    report.write_text("<testsuite>" + old + added + "</testsuite>")
    certificate = tmp_path / "engine.json"
    certificate.write_text(json.dumps({
        "engine_checkout": str(tmp_path / "engine"),
        "tree": "tree", "canonical_patch_sha256": "patch",
        "report": str(report), "report_sha256": launcher.sha256_file(report),
        "previous_report": str(previous), "previous_report_sha256": launcher.sha256_file(previous),
    }))
    if fault == "report_changed":
        report.write_text(report.read_text() + "\n")
    arguments = {
        "engine": tmp_path / ("different" if fault == "engine" else "engine"),
        "patch_sha256": "different" if fault == "patch" else "patch",
    }
    if fault is None:
        result = launcher.verify_engine_qualification(certificate, **arguments)
        assert result["passing_cases"] == 2 and result["retained_cases"] == 1
        assert result["gpu_validation_pending"]
    else:
        with pytest.raises(ValueError):
            launcher.verify_engine_qualification(certificate, **arguments)


def summary(*, guarded=False):
    return {
        "workflow_count": 2, "completed_workflows": 2,
        "system_jct_eligible_workflows": 2, "measurement_valid_workflows": 2,
        "native_agent_jct_eligible_workflows": 2 - int(guarded),
        "agent_control": {"guard_intervened_completions": int(guarded)},
        "workflows": [
            {
                "instance_id": "a", "native_agent_jct_eligible": True,
                "outcome": "completed", "measurement_valid": True, "system_jct_eligible": True,
            },
            {
                "instance_id": "b", "native_agent_jct_eligible": not guarded,
                "outcome": "completed", "measurement_valid": True, "system_jct_eligible": True,
                "native_agent_jct_exclusion_reasons": (
                    ["guard_intervened_completion"] if guarded else []
                ),
            },
        ],
    }


def test_clean_completion_still_does_not_accept_performance():
    outcome = launcher.completed_arm(summary(), 0, 2)
    assert outcome["status"] == "completed"
    assert outcome["original_exit_code"] == 0
    assert not outcome["performance_accepted"]


def test_completed_guarded_arm_preserves_nonzero_exit_and_exact_exclusions():
    outcome = launcher.completed_arm(summary(guarded=True), 1, 2)
    assert outcome["status"] == "completed_with_eligibility_exclusions"
    assert outcome["original_exit_code"] == 1
    assert outcome["native_agent_jct_eligible_workflows"] == 1
    assert outcome["native_agent_jct_exclusions"] == [{
        "instance_id": "b", "native_agent_jct_exclusion_reasons": ["guard_intervened_completion"],
    }]
    assert not outcome["performance_accepted"]


@pytest.mark.parametrize("field,value", (
    ("workflow_count", 1), ("completed_workflows", 1), ("system_jct_eligible_workflows", 1),
    ("measurement_valid_workflows", 1), ("native_agent_jct_eligible_workflows", 1),
))
def test_missing_or_invalid_work_is_never_treated_as_completion(field, value):
    data = summary()
    data[field] = value
    with pytest.raises(ValueError):
        launcher.completed_arm(data, 0, 2)


@pytest.mark.parametrize("exit_code", (1, 2, -9, 130, 143))
def test_unexplained_nonzero_exit_is_preserved_as_a_failure(exit_code):
    with pytest.raises(ValueError, match="process failed"):
        launcher.completed_arm(summary(), exit_code, 2)


def test_guard_does_not_hide_a_second_measurement_failure():
    data = summary(guarded=True)
    data["workflows"][1]["native_agent_jct_exclusion_reasons"].append("model_request_timeout")
    with pytest.raises(ValueError, match="process failed"):
        launcher.completed_arm(data, 1, 2)


@pytest.mark.parametrize("change", ("missing", "duplicate", "incomplete", "invalid_measurement"))
def test_summary_counts_cannot_hide_invalid_workflow_rows(change):
    data = summary()
    if change == "missing":
        data["workflows"].pop()
    elif change == "duplicate":
        data["workflows"][1]["instance_id"] = "a"
    elif change == "incomplete":
        data["workflows"][1]["outcome"] = "incomplete"
    else:
        data["workflows"][1]["measurement_valid"] = False
    with pytest.raises(ValueError, match="full system-valid"):
        launcher.completed_arm(data, 0, 2)


def test_batch8_server_validation_rejects_a_batch12_server(tmp_path):
    server = tmp_path / "server"
    server.mkdir()
    (server / "server_info.json").write_text(json.dumps({
        "max_running_requests": 12, "random_seed": 21,
        "context_length": 262144, "enable_beliefkv_admission": True,
    }))
    with pytest.raises(ValueError, match="max_running_requests"):
        launcher.observe("full", tmp_path)


def test_final_validation_requires_persisted_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher.comparison, "observe_arm", Mock())
    with pytest.raises(ValueError, match="missing final"):
        launcher.observe("native_matched", tmp_path, final=True)


def test_latest_main_is_checked_before_launch_and_frozen_sources_before_every_arm(monkeypatch):
    monkeypatch.setattr(launcher.comparison, "verify_sources", Mock())
    monkeypatch.setattr(launcher, "_require_clean", Mock())
    monkeypatch.setattr(launcher.qualified, "command", Mock(return_value="changed"))
    plan = {"development_checkout": "/main", "code_commit": "registered"}
    launcher.verify(plan)
    with pytest.raises(ValueError, match="main advanced"):
        launcher.verify(plan, require_latest_main=True)
    assert launcher.comparison.verify_sources.call_count == 2


def ready_watcher(held, tmp_path, monkeypatch):
    plan, _ = held
    plan["environment"] = json.loads(SPEC.read_bytes())["common_environment"]
    path = registration(tmp_path, plan)
    monkeypatch.setattr(launcher.reservation, "now", lambda: launcher.MINIMUM_TIME)
    monkeypatch.setattr(launcher.reservation, "process", Mock(return_value={"pid": 123}))
    monkeypatch.setattr(launcher, "verify", Mock())
    monkeypatch.setattr(launcher.qualified, "resources", Mock(return_value={"ready": True}))
    monkeypatch.setattr(launcher.qualified, "machine_helper", Mock(return_value=NS(
        clear_stale_socket=Mock(),
    )))
    monkeypatch.setattr(launcher.subprocess, "run", Mock())
    monkeypatch.setattr(launcher, "observe", Mock())
    monkeypatch.setattr(launcher.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(launcher.comparison, "postprocess", Mock())
    return plan, path


def test_full_then_join_then_native_collects_fresh_arms_and_retains_guard_exit(
    held, tmp_path, monkeypatch,
):
    plan, path = ready_watcher(held, tmp_path, monkeypatch)
    launched = []

    def launch(argv, **kwargs):
        assert not launcher.PAUSE.exists()
        env = kwargs["env"]
        mode = env["BELIEFKV_INTERNAL_COMPARISON_MODE"]
        assert env["MAX_RUNNING_REQUESTS"] == "8"
        assert env["SERVER_RANDOM_SEED"] == "21"
        launched.append(mode)
        arm = Path(env["RUN_ROOT"])
        client = arm / "client_36"
        client.mkdir(parents=True)
        data = summary(guarded=mode == "join_priority_only")
        data.update(
            workflow_count=36, completed_workflows=36, system_jct_eligible_workflows=36,
            measurement_valid_workflows=36,
            native_agent_jct_eligible_workflows=35 if mode == "join_priority_only" else 36,
        )
        data["workflows"].extend(
            {**data["workflows"][0], "instance_id": f"workflow-{index}"} for index in range(34)
        )
        (client / "summary.json").write_text(json.dumps(data))
        return NS(pid=123, poll=lambda: 0, wait=lambda: int(mode == "join_priority_only"))

    monkeypatch.setattr(launcher.subprocess, "Popen", launch)
    launcher.watch(path)
    state = json.loads(path.with_suffix(".state.json").read_bytes())
    assert launched == state["completed_arms"] == ["full", "join_priority_only", "native_matched"]
    assert state["status"] == "collected_performance_review_pending"
    assert state["original_exit_codes"]["join_priority_only"] == 1
    assert not state["performance_accepted"]
    assert launcher.comparison.postprocess.call_count == 3
    assert launcher.verify.call_args_list[0].kwargs == {"require_latest_main": True}
    assert launcher.verify.call_args_list[-1].kwargs == {}
    comparison_command = launcher.subprocess.run.call_args.args[0]
    assert not any("full_reference" in value for value in comparison_command)
    assert "--exclude-workload-tail" in comparison_command


def test_failed_initial_launch_restores_a_stop_marker_without_replacing_a_new_hold(
    held, tmp_path, monkeypatch,
):
    _, path = ready_watcher(held, tmp_path, monkeypatch)
    monkeypatch.setattr(
        launcher.subprocess, "Popen", Mock(side_effect=OSError("server launch failed")),
    )
    launcher.watch(path)
    state = json.loads(path.with_suffix(".state.json").read_bytes())
    assert state["status"] == "stopped_for_investigation"
    assert not state["completed_arms"]
    assert json.loads(launcher.PAUSE.read_bytes())["reason"] == (
        "fresh_campaign_stopped_before_first_qualified_arm"
    )


def test_new_pause_during_a_live_arm_stops_owned_work_and_preserves_the_new_marker(
    held, tmp_path, monkeypatch,
):
    _, path = ready_watcher(held, tmp_path, monkeypatch)
    driver = NS(pid=123, poll=lambda: None, wait=lambda **kwargs: 143)

    def launch(*args, **kwargs):
        launcher.PAUSE.write_text("A newer user reservation.")
        return driver

    monkeypatch.setattr(launcher.subprocess, "Popen", launch)
    monkeypatch.setattr(launcher, "stop_arm", Mock())
    launcher.watch(path)
    state = json.loads(path.with_suffix(".state.json").read_bytes())
    assert state["status"] == "cancelled_hold_changed"
    assert state["current_arm"] == "full"
    launcher.stop_arm.assert_called_once()
    assert launcher.PAUSE.read_text() == "A newer user reservation."
