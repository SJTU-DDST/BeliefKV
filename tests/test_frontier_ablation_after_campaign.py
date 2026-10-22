from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from scripts import run_frontier_ablation_after_campaign as launcher


SPEC = Path(__file__).resolve().parents[1] / "configs/migration/next_predictive_baselines_20261022.json"
WATCHER = {"pid": 12, "start_ticks": "123"}
DRIVER = {"pid": 23, "start_ticks": "234"}


def reference_state(*, status="running_native_matched", completed=None):
    return {
        "status": status, "order": list(launcher.REFERENCE_ORDER),
        "watcher": dict(WATCHER), "driver": dict(DRIVER),
        "completed_arms": list(launcher.REFERENCE_ORDER) if completed is None else completed,
    }


@pytest.mark.parametrize("status", ("running_native_matched", "postprocessing_native_matched"))
def test_reference_wait_requires_the_exact_supervisor_to_be_live(status):
    plan = {"reference_watcher": dict(WATCHER)}
    state = reference_state(status=status)
    assert not launcher.reference_ready(plan, state, alive=lambda _: True)
    with pytest.raises(ValueError, match="exited"):
        launcher.reference_ready(plan, state, alive=lambda _: False)


@pytest.mark.parametrize("watcher_live,driver_live", ((True, False), (False, True), (True, True), (False, False)))
def test_completed_collection_waits_for_both_reference_processes_to_exit(watcher_live, driver_live):
    state = reference_state(status="collected_performance_review_pending")
    assert launcher.reference_ready(
        {"reference_watcher": dict(WATCHER)}, state,
        alive=lambda identity: watcher_live if identity["pid"] == WATCHER["pid"] else driver_live,
    ) is (not watcher_live and not driver_live)


@pytest.mark.parametrize("fault", ("pid_reused", "missing_identity", "failed", "missing_arm", "reordered", "error"))
def test_invalid_reference_never_becomes_ready(fault):
    plan = {"reference_watcher": dict(WATCHER)}
    state = reference_state(status="collected_performance_review_pending")
    if fault == "pid_reused":
        state["watcher"]["start_ticks"] = "999"
    elif fault == "missing_identity":
        plan["reference_watcher"] = {}
    elif fault == "failed":
        state["status"] = "stopped_for_investigation"
    elif fault == "missing_arm":
        state["completed_arms"].pop()
    elif fault == "reordered":
        state["order"].reverse()
    else:
        state["error"] = "failed telemetry"
    with pytest.raises(ValueError):
        launcher.reference_ready(plan, state, alive=lambda _: False)


def reference_plan():
    spec = json.loads(SPEC.read_bytes())
    environment = launcher.fresh.campaign_environment(
        {"PARALLEL_WAIT_PREFETCH": "1", "WAIT_PREFETCH_CONTEXTS": "2"},
        spec, Path("/old/frozen"), Path("/engine"), 18524,
    )
    return {
        "order": list(launcher.REFERENCE_ORDER), "environment": environment,
        "execution_checkout": "/old/frozen", "engine_checkout": "/engine",
    }


def test_candidate_changes_only_frontier_control_after_rebinding_paths():
    reference = reference_plan()
    before = deepcopy(reference)
    candidate = launcher.candidate_environment(reference, Path("/new/frozen"), 18528)
    baseline = arm_environment(launcher.fresh.campaign_environment(
        {}, {
            "order": list(launcher.REFERENCE_ORDER),
            "common_environment": {
                **reference["environment"],
                "WORKLOAD_MANIFEST": "configs/workloads/locobench_agent_long_36root_20261020.json",
            },
        }, Path("/new/frozen"), Path("/engine"), 18528,
    ), "full")
    changed = {key for key in baseline if baseline[key] != candidate[key]}
    assert changed == {"EXECUTION_FRONTIER_RESTORE", "BELIEFKV_ENABLE_FRONTIER_RESTORE"}
    assert candidate["BELIEFKV_ENABLE_FRONTIER_RESTORE"] == "0"
    assert candidate["BELIEFKV_TOOL_RESTORE_MODE"] == "execution"
    assert candidate["BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH"] == "1"
    assert candidate["BELIEFKV_ENABLE_PREPARE_HOST"] == "1"
    assert candidate["PARALLEL_WAIT_PREFETCH"] == "1"
    assert candidate["WAIT_PREFETCH_CONTEXTS"] == "2"
    assert candidate["BELIEFKV_ENABLE_JOIN_LAST_CHILD_PRIORITY"] == "0"
    assert candidate["WORKLOAD_MANIFEST"].startswith("/new/frozen/")
    assert reference == before


@pytest.mark.parametrize("field,value", (
    ("MAX_RUNNING_REQUESTS", "12"), ("ROOT_COUNT", "24"),
    ("PARALLEL_WAIT_PREFETCH", "0"), ("WAIT_PREFETCH_CONTEXTS", "1"),
    ("JOIN_LAST_CHILD_PRIORITY", "1"), ("EXECUTION_FRONTIER_RESTORE", "0"),
))
def test_candidate_rejects_a_different_reference_regime(field, value):
    reference = reference_plan()
    reference["environment"][field] = value
    with pytest.raises(ValueError):
        launcher.candidate_environment(reference, Path("/new/frozen"), 18528)


@pytest.mark.parametrize("change", ("documentation", "runtime", "added_source"))
def test_only_documentation_can_advance_main_without_invalidating_candidate(
    tmp_path, monkeypatch, change,
):
    runtime = tmp_path / "runtime.py"
    runtime.write_text("qualified = True\n")
    plan = {
        "development_checkout": str(tmp_path),
        "source_sha256": {"runtime.py": launcher.sha256_file(runtime)},
    }
    monkeypatch.setattr(launcher.fresh, "verify", Mock())
    monkeypatch.setattr(launcher.fresh, "_require_clean", Mock())
    if change == "runtime":
        runtime.write_text("qualified = False\n")
    (tmp_path / "notes.md").write_text("new documentation\n")
    paths = "runtime.py\nextra.py" if change == "added_source" else "runtime.py"
    monkeypatch.setattr(launcher.qualified, "command", Mock(side_effect=[paths, "latest-doc-commit"]))
    if change == "documentation":
        assert launcher.verify_candidate(plan) == "latest-doc-commit"
    else:
        with pytest.raises(ValueError, match="source"):
            launcher.verify_candidate(plan)


def test_registration_freezes_one_candidate_and_starts_only_a_cpu_watcher(tmp_path, monkeypatch):
    root, experiments = tmp_path / "main", tmp_path / "experiments"
    root.mkdir()
    reference = reference_plan()
    reference.update({
        "run_root": str(tmp_path / "reference_run"),
        "code_commit": "previous", "external_inputs": [], "engine_tree": "qualified-engine",
        "hold_file": str(tmp_path / "hold.json"), "gpu_not_before": "2026-10-22T06:00:00+08:00",
    })
    reference_path = tmp_path / "reference.registration.json"
    reference_path.write_text(json.dumps(reference))
    reference_path.with_suffix(".state.json").write_text(json.dumps(reference_state()))
    source_path = tmp_path / "cpu_sources.json"
    source_path.write_text(json.dumps({
        name: "digest" for name in (
            "scripts/run_frontier_ablation_after_campaign.py",
            "tests/test_frontier_ablation_after_campaign.py",
            "tests/test_frontier_restore_ablation.py",
        )
    }))
    report = tmp_path / "cpu.xml"
    report.write_text("<testsuite/>")
    args = NS(
        reference_registration=reference_path, registration=tmp_path / "next.registration.json",
        run_root=tmp_path / "run_v123", cpu_report=report, cpu_sources=source_path, port=18528,
    )
    monkeypatch.setattr(launcher, "ROOT", root)
    monkeypatch.setattr(launcher.comparison, "EXPERIMENTS", experiments)
    monkeypatch.setattr(launcher.fresh, "verify", Mock())
    monkeypatch.setattr(launcher.fresh, "check_hold", Mock())
    monkeypatch.setattr(launcher.fresh, "_require_clean", Mock())
    monkeypatch.setattr(launcher.comparison, "qualify_report", Mock(return_value=1))
    monkeypatch.setattr(launcher.comparison, "verify_cpu_snapshot", Mock())
    monkeypatch.setattr(launcher, "verify_candidate", Mock())
    monkeypatch.setattr(launcher, "reference_ready", Mock(return_value=False))
    monkeypatch.setattr(launcher.reservation, "alive", Mock(return_value=True))
    monkeypatch.setattr(launcher.reservation, "process", Mock(return_value={"pid": 99, "start_ticks": "999"}))
    monkeypatch.setattr(launcher.qualified, "command", Mock(side_effect=["main", "a" * 40]))

    def freeze(arguments, **kwargs):
        checkout = Path(arguments[-2])
        (checkout / "third_party").mkdir(parents=True)

    monkeypatch.setattr(launcher.subprocess, "run", Mock(side_effect=freeze))
    watcher = Mock(pid=99)
    monkeypatch.setattr(launcher.subprocess, "Popen", Mock(return_value=watcher))
    launcher.register(args)
    plan = launcher.load(args.registration)
    assert plan["order"] == ["full"]
    assert plan["reference_registration"] == str(reference_path)
    assert plan["environment"]["BELIEFKV_ENABLE_FRONTIER_RESTORE"] == "0"
    assert plan["reference_code_commit"] == "previous"
    assert plan["performance_accepted"] is False
    assert not args.run_root.exists()
    launcher.subprocess.Popen.assert_called_once()
    environment = launcher.subprocess.Popen.call_args.kwargs["env"]
    assert environment["CUDA_VISIBLE_DEVICES"] == "99999"
    assert "--watch" in launcher.subprocess.Popen.call_args.args[0]
    launcher.fresh.check_hold.assert_called_once_with(reference, released=True)
    assert not (tmp_path / "hold.json").exists()


@pytest.fixture
def watched(tmp_path, monkeypatch):
    state_path = tmp_path / "reference.state.json"
    state_path.write_text(json.dumps(reference_state()))
    plan = {
        "reference_state": str(state_path), "reference_watcher": dict(WATCHER),
        "run_root": str(tmp_path / "candidate"), "port": 18528,
        "environment": {"BELIEFKV_ENABLE_FRONTIER_RESTORE": "0"},
    }
    registration = tmp_path / "candidate.registration.json"
    registration.write_text(json.dumps(plan))
    monkeypatch.setattr(launcher.reservation, "process", Mock(return_value={"pid": 34, "start_ticks": "345"}))
    monkeypatch.setattr(launcher.fresh, "check_hold", Mock(return_value=True))
    monkeypatch.setattr(launcher, "reference_ready", Mock(return_value=False))
    for owner, name in (
        (launcher.qualified, "resources"), (launcher, "verify_candidate"),
        (launcher, "verify_reference"), (launcher.subprocess, "run"),
        (launcher.subprocess, "Popen"),
    ):
        monkeypatch.setattr(owner, name, Mock(side_effect=AssertionError(name)))
    return plan, registration, state_path


def test_waiting_for_reference_does_not_probe_gpu_preflight_or_launch(watched, monkeypatch):
    plan, path, _ = watched

    def stop(_seconds):
        state = launcher.load(path.with_suffix(".state.json"))
        assert state["status"] == "waiting_reference_campaign"
        raise launcher.fresh.HoldChanged("end the CPU-only test wait")

    monkeypatch.setattr(launcher.time, "sleep", stop)
    launcher.watch(path)
    assert launcher.load(path.with_suffix(".state.json"))["status"] == "cancelled_hold_changed"
    launcher.qualified.resources.assert_not_called()
    launcher.subprocess.run.assert_not_called()
    launcher.subprocess.Popen.assert_not_called()
    assert not Path(plan["run_root"]).exists()


@pytest.mark.parametrize("failure", ("reference", "candidate", "hold"))
def test_validation_failure_cannot_reach_gpu_or_launch(watched, monkeypatch, failure):
    plan, path, _ = watched
    launcher.reference_ready.return_value = True
    if failure == "reference":
        launcher.verify_reference.side_effect = ValueError("reference invalid")
    elif failure == "candidate":
        launcher.verify_reference.side_effect = None
        launcher.verify_reference.return_value = {"bound": True}
        launcher.verify_candidate.side_effect = ValueError("qualified source changed")
    else:
        launcher.fresh.check_hold.side_effect = launcher.fresh.HoldChanged("new reservation")
    launcher.watch(path)
    assert launcher.load(path.with_suffix(".state.json"))["status"] in (
        "stopped_for_investigation", "cancelled_hold_changed",
    )
    launcher.qualified.resources.assert_not_called()
    launcher.subprocess.Popen.assert_not_called()
    assert not Path(plan["run_root"]).exists()


def test_candidate_waits_for_idle_resources_without_preflight(watched, monkeypatch):
    plan, path, _ = watched
    launcher.reference_ready.return_value = True
    launcher.verify_reference.side_effect = None
    launcher.verify_reference.return_value = {"bound": True}
    launcher.verify_candidate.side_effect = None
    launcher.verify_candidate.return_value = "commit"
    launcher.qualified.resources.side_effect = None
    launcher.qualified.resources.return_value = {"ready": False}
    monkeypatch.setattr(
        launcher.time, "sleep",
        Mock(side_effect=launcher.fresh.HoldChanged("end resource wait")),
    )
    launcher.watch(path)
    launcher.qualified.resources.assert_called_once_with(18528)
    launcher.subprocess.run.assert_not_called()
    launcher.subprocess.Popen.assert_not_called()
    assert not Path(plan["run_root"]).exists()


@pytest.mark.parametrize("failure", (None, "reference_drift", "runtime"))
def test_only_full_is_launched_after_two_idle_checks_and_stops_only_its_driver(
    watched, monkeypatch, failure,
):
    plan, path, _ = watched
    launcher.reference_ready.return_value = True
    launcher.verify_reference.side_effect = (
        [{"bound": True}, {"bound": False}] if failure == "reference_drift" else None
    )
    launcher.verify_reference.return_value = {"bound": True}
    launcher.verify_candidate.side_effect = None
    launcher.verify_candidate.return_value = "commit"
    launcher.qualified.resources.side_effect = None
    launcher.qualified.resources.return_value = {"ready": True}
    monkeypatch.setattr(launcher.time, "sleep", Mock())
    launcher.subprocess.run.side_effect = None
    monkeypatch.setattr(launcher.qualified, "machine_helper", Mock(return_value=NS(clear_stale_socket=Mock())))
    driver = Mock(pid=45)
    driver.poll.side_effect = None if failure == "runtime" else [None, 0]
    driver.poll.return_value = None
    driver.wait.return_value = -15 if failure == "runtime" else 0

    def launch(*args, **kwargs):
        arm = Path(plan["run_root"]) / "full/client_36"
        arm.mkdir(parents=True)
        (arm / "summary.json").write_text("{}")
        return driver

    launcher.subprocess.Popen.side_effect = launch
    monkeypatch.setattr(launcher.fresh, "observe", Mock(
        side_effect=ValueError("wrong runtime flag") if failure == "runtime" else None,
    ))
    monkeypatch.setattr(launcher.fresh, "stop_arm", Mock())
    monkeypatch.setattr(launcher.fresh, "completed_arm", Mock(return_value={"status": "completed"}))
    monkeypatch.setattr(launcher.comparison, "postprocess", Mock())
    monkeypatch.setattr(launcher, "compare", Mock())
    launcher.watch(path)
    state = launcher.load(path.with_suffix(".state.json"))
    if failure == "reference_drift":
        launcher.subprocess.Popen.assert_not_called()
        assert state["status"] == "stopped_for_investigation"
        assert not Path(plan["run_root"]).exists()
        return
    launcher.subprocess.Popen.assert_called_once()
    launched = launcher.subprocess.Popen.call_args.kwargs["env"]
    assert launched["BELIEFKV_ENABLE_FRONTIER_RESTORE"] == "0"
    assert launched["CUDA_VISIBLE_DEVICES"] == "0"
    assert launched["RUN_ROOT"] == str(Path(plan["run_root"]) / "full")
    if failure == "runtime":
        launcher.fresh.stop_arm.assert_called_once()
        assert launcher.fresh.stop_arm.call_args.args[0] == plan
        assert state["stopped_driver_exit_code"] == -15
        assert state["status"] == "stopped_for_investigation"
        launcher.compare.assert_not_called()
    else:
        launcher.fresh.stop_arm.assert_not_called()
        assert state["status"] == "collected_performance_review_pending"
        assert state["completed_arms"] == ["full"]
        assert state["performance_accepted"] is False
        launcher.compare.assert_called_once_with(plan)
    assert launcher.qualified.resources.call_count == 3


def test_comparison_reuses_all_reference_arms_without_launching_them(tmp_path, monkeypatch):
    plan = {"run_root": str(tmp_path / "candidate"), "reference_run": str(tmp_path / "reference")}
    run = Mock()
    monkeypatch.setattr(launcher.subprocess, "run", run)
    launcher.compare(plan)
    args = run.call_args.args[0]
    arms = [args[index + 1] for index, value in enumerate(args) if value == "--arm"]
    assert [arm.split("=", 1)[0] for arm in arms] == [
        "full_no_frontier", "full", "join_priority_only", "native_matched",
    ]
    assert all(str(tmp_path / "reference") in arm for arm in arms[1:])
    assert "--exclude-workload-tail" in args
    assert args[args.index("--tail-active-workflows") + 1] == "4"
