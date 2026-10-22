from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.experiments import module_ablation as modules
from beliefkv.experiments.internal_comparison import validate_runtime
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts import run_module_ablations_after_full as operator


def common():
    return {
        "ROOT_COUNT": "36", "MAX_RUNNING_REQUESTS": "8",
        "SAMPLING_SEED": "21", "SERVER_RANDOM_SEED": "21",
        "MODEL_CONTEXT_TOKENS": "262144", "CONTEXT_WINDOW_TOKENS": "262144",
        "PREFILL_SCHEDULING_POLICY": "join_priority", "PREFETCH_RESIDENCY_PROTECTION": "0",
        "TOOL_RESTORE_MODE": "execution", "RESTORE_TIERS": "dependency",
        "BELIEFKV_ENABLE_FRONTIER_RESTORE": "0", "EXECUTION_FRONTIER_RESTORE": "0",
        "BELIEFKV_ENABLE_TOOL_CONTINUITY": "1", "TOOL_CONTINUITY": "1",
        "HOST_SPLIT": "80:20", "HICACHE_SIZE_GB": "200",
        "PARALLEL_WAIT_PREFETCH": "1", "WAIT_PREFETCH_CONTEXTS": "2",
    }


@pytest.mark.parametrize("label", modules.ORDER)
def test_module_removal_keeps_workload_seed_capacity_and_other_active_controls(label):
    base = common()
    before = deepcopy(base)
    env = modules.environment(base, label)
    for name in (
        "ROOT_COUNT", "MAX_RUNNING_REQUESTS", "SAMPLING_SEED", "SERVER_RANDOM_SEED",
        "MODEL_CONTEXT_TOKENS", "CONTEXT_WINDOW_TOKENS", "HOST_SPLIT", "HICACHE_SIZE_GB",
    ):
        assert env[name] == base[name]
    assert env["BELIEFKV_ENABLE_FRONTIER_RESTORE"] == "0"
    assert env["PREFETCH_RESIDENCY_PROTECTION"] == "0"
    assert base == before
    if label == "no_background_prepare":
        assert env["PREPARE_HOST"] == "1"
        assert env["ENABLE_TOOL_TIMING"] == "1"
        assert env["BELIEFKV_ENABLE_BACKGROUND_PREPARE"] == "0"
    if label == "no_tool_duration_model":
        assert env["ONLINE_TOOL_TIMING"] == env["ENABLE_TOOL_TIMING"] == "0"
        assert env["PREPARE_HOST"] == "1"
        assert env["BORROWABLE_PARENT_HEADROOM"] == "1"
    if label == "schedule_only":
        assert env["BELIEFKV_ENABLE_TOOL_CONTINUITY"] == "1"
        assert env["BELIEFKV_ENABLE_JOIN_PARENT_PRIORITY"] == "1"
        assert env["PREPARE_HOST"] == "0"
    if label == "kv_only":
        assert env["BELIEFKV_NATIVE_PREFILL_ORDER"] == "1"
        assert env["BELIEFKV_ENABLE_TOOL_CONTINUITY"] == "0"


@pytest.mark.parametrize("label", modules.ORDER)
def test_actual_runtime_controls_match_the_component_removal(monkeypatch, tmp_path, label):
    env = modules.environment(common(), label)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    for name in ("ONLINE_TOOL_TIMING", "BORROWABLE_PARENT_HEADROOM"):
        monkeypatch.setenv("BELIEFKV_" + name, env[name])
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "events.sock"))
    runtime._opportunity_writer = Mock()
    try:
        runtime._restore_tiers_supported = True
        runtime._record_runtime_state()
        state = runtime._opportunity_writer.record.call_args.args[0]
        validate_runtime(
            env["BELIEFKV_INTERNAL_COMPARISON_MODE"], state,
            scheduling_policy=env["PREFILL_SCHEDULING_POLICY"],
            tool_restore_mode=env["TOOL_RESTORE_MODE"],
            tool_continuity=env["TOOL_CONTINUITY"] == "1",
            expected_overrides=modules.runtime_overrides(label),
        )
        modules.verify_activity(label, state)
        if label == "no_background_prepare":
            runtime._native_cache = NS(prepare_beliefkv_idle_full=Mock(side_effect=AssertionError()))
            runtime.dispatch_idle_prepare()
            runtime.dispatch_spawn_prepare()
            assert runtime.enable_prepare_host
        if label == "no_child_reclaim":
            runtime._native_cache = Mock()
            runtime._retire_terminal_child(Mock(), Mock())
            runtime._native_cache.retire_beliefkv_child_session.assert_not_called()
    finally:
        runtime._opportunity_writer = None
        runtime.close()


@pytest.mark.parametrize("field,value", (
    ("ROOT_COUNT", "16"), ("MAX_RUNNING_REQUESTS", "12"),
    ("SAMPLING_SEED", "22"), ("SERVER_RANDOM_SEED", "22"),
    ("MODEL_CONTEXT_TOKENS", "131072"), ("PREFILL_SCHEDULING_POLICY", "workflow"),
    ("BELIEFKV_ENABLE_FRONTIER_RESTORE", "1"),
    ("BELIEFKV_ENABLE_TOOL_CONTINUITY", "0"),
))
def test_changed_reference_regime_is_rejected(field, value):
    base = common()
    base[field] = value
    with pytest.raises(ValueError):
        modules.environment(base, modules.ORDER[0])


@pytest.mark.parametrize("label,field", (
    ("no_background_prepare", "idle_full_prepare_native_calls"),
    ("no_background_prepare", "spawn_full_prepare_issued_batches"),
    ("no_child_reclaim", "terminal_child_reclaim_calls"),
    ("no_join_priority", "join_parent_priority_admitted"),
))
def test_disabled_module_activity_stops_its_arm(label, field):
    with pytest.raises(ValueError):
        modules.verify_activity(label, {"counts": {field: 1}})


@pytest.mark.parametrize("overrides", ({"physical_disabled": True}, {"prepare_host": False}, {"background_prepare": 0}))
def test_expected_overrides_cannot_bypass_physical_or_general_mode_checks(overrides):
    with pytest.raises(ValueError, match="unsupported module"):
        validate_runtime("full", {}, expected_overrides=overrides)


WATCHER = {"pid": 11, "start_ticks": "222"}


def reference_state(status="running_full_tool_continuity"):
    return {
        "status": status, "watcher": dict(WATCHER), "driver": {"pid": 22, "start_ticks": "333"},
        "order": ["full"], "completed_arms": ["full"] if status.startswith("collected") else [],
    }


@pytest.mark.parametrize("status", ("running_full_tool_continuity", "postprocessing_full"))
def test_active_full_blocks_all_gpu_probes_and_ablation_launches(status):
    assert not operator.reference_ready(
        {"reference_watcher": dict(WATCHER)}, reference_state(status), alive=lambda _: True,
    )
    with pytest.raises(ValueError, match="exited"):
        operator.reference_ready(
            {"reference_watcher": dict(WATCHER)}, reference_state(status), alive=lambda _: False,
        )


@pytest.mark.parametrize("live", (True, False))
def test_completed_full_requires_driver_and_supervisor_exit(live):
    assert operator.reference_ready(
        {"reference_watcher": dict(WATCHER)},
        reference_state("collected_performance_review_pending"), alive=lambda _: live,
    ) is (not live)


@pytest.mark.parametrize("fault", ("pid_reused", "error", "missing_arm", "failed", "wrong_order"))
def test_failed_or_changed_full_never_releases_the_queue(fault):
    state = reference_state("collected_performance_review_pending")
    if fault == "pid_reused":
        state["watcher"]["start_ticks"] = "999"
    elif fault == "error":
        state["error"] = "configuration failure"
    elif fault == "missing_arm":
        state["completed_arms"] = []
    elif fault == "failed":
        state["status"] = "stopped_for_investigation"
    else:
        state["order"] = ["full", "native"]
    with pytest.raises(ValueError):
        operator.reference_ready(
            {"reference_watcher": dict(WATCHER)}, state, alive=lambda _: False,
        )


def test_frozen_workload_paths_are_rebound_for_every_arm():
    reference = {
        "environment": {**common(), "WORKLOAD_MANIFEST": "/old/frozen/configs/workloads/workload.json"},
        "execution_checkout": "/old/frozen",
    }
    result = operator.environments(reference, Path("/new/frozen"), 18532)
    assert tuple(result) == modules.ORDER
    for arm in result.values():
        env = arm["environment"]
        assert env["WORKLOAD_MANIFEST"] == "/new/frozen/configs/workloads/workload.json"
        assert env["PYTHONPATH"] == "/new/frozen"
        assert env["PORT"] == "18532"


def test_waiting_for_full_never_touches_gpu_or_starts_a_child(tmp_path, monkeypatch):
    import json

    reference_path = tmp_path / "reference.state.json"
    reference_path.write_text(json.dumps(reference_state()))
    plan = {
        "reference_state": str(reference_path), "reference_watcher": dict(WATCHER),
        "run_root": str(tmp_path / "runs"), "order": list(modules.ORDER),
    }
    registration = tmp_path / "queue.registration.json"
    registration.write_text(json.dumps(plan))
    monkeypatch.setattr(operator.fresh, "check_hold", Mock())
    monkeypatch.setattr(operator.reservation, "process", Mock(return_value=WATCHER))
    monkeypatch.setattr(operator.reservation, "alive", Mock(return_value=True))
    monkeypatch.setattr(operator, "reference_ready", Mock(return_value=False))
    monkeypatch.setattr(operator.qualified, "resources", Mock(side_effect=AssertionError("GPU")))
    monkeypatch.setattr(operator.subprocess, "Popen", Mock(side_effect=AssertionError("launch")))

    def stop(_):
        raise operator.fresh.HoldChanged("test ends the CPU-only wait")

    monkeypatch.setattr(operator.time, "sleep", stop)
    operator.watch(registration)
    state = json.loads(registration.with_suffix(".state.json").read_bytes())
    assert state["status"] == "cancelled_hold_changed"
    assert state["completed_arms"] == []
    assert not Path(plan["run_root"]).exists()


def test_sequential_queue_checks_effective_mode_and_collects_each_arm(tmp_path, monkeypatch):
    import json

    run = tmp_path / "runs"
    reference_path = tmp_path / "reference.state.json"
    reference_path.write_text(json.dumps(reference_state("collected_performance_review_pending")))
    labels = ("schedule_only", "no_background_prepare")
    plan = {
        "reference_state": str(reference_path), "reference_watcher": dict(WATCHER),
        "run_root": str(run), "order": list(labels), "port": 18532,
        "arms": {
            label: {
                "environment": modules.environment(common(), label),
                "runtime_overrides": modules.runtime_overrides(label),
            }
            for label in labels
        },
    }
    registration = tmp_path / "queue.registration.json"
    registration.write_text(json.dumps(plan))
    monkeypatch.setattr(operator.fresh, "check_hold", Mock())
    monkeypatch.setattr(operator.fresh, "verify", Mock())
    monkeypatch.setattr(operator.reservation, "process", Mock(return_value=WATCHER))
    monkeypatch.setattr(operator, "reference_ready", Mock(return_value=True))
    monkeypatch.setattr(operator, "verify_reference", Mock(return_value={"sealed": True}))
    monkeypatch.setattr(operator, "wait_resources", Mock())
    monkeypatch.setattr(operator.qualified, "resources", Mock(return_value={"ready": True}))
    helper = NS(clear_stale_socket=Mock())
    monkeypatch.setattr(operator.qualified, "machine_helper", Mock(return_value=helper))
    monkeypatch.setattr(operator.subprocess, "run", Mock())
    observed = Mock()
    monkeypatch.setattr(operator, "observe", observed)
    postprocess = Mock()
    monkeypatch.setattr(operator.comparison, "postprocess", postprocess)
    monkeypatch.setattr(operator, "compare", Mock())
    monkeypatch.setattr(operator.fresh, "completed_arm", Mock(return_value={"status": "completed"}))
    launches = []

    def start(*args, **kwargs):
        label = Path(kwargs["env"]["RUN_ROOT"]).name
        launches.append(label)
        # Each predecessor must have finished postprocessing before the next launch.
        assert postprocess.call_count == len(launches) - 1
        client = run / label / "client_36"
        client.mkdir(parents=True)
        (client / "summary.json").write_text("{}")
        return NS(pid=22, poll=lambda: 0, wait=lambda: 0)

    monkeypatch.setattr(operator.subprocess, "Popen", start)
    operator.watch(registration)
    state = json.loads(registration.with_suffix(".state.json").read_bytes())
    assert state["status"] == "collected_performance_review_pending"
    assert state["completed_arms"] == list(labels)
    assert state["original_exit_codes"] == dict.fromkeys(labels, 0)
    assert launches == list(labels)
    assert [call.kwargs["runtime_mode"] for call in postprocess.call_args_list] == [
        "join_priority_only", "full",
    ]
    assert observed.call_args_list == [
        ((plan, label), {"final": True}) for label in labels
    ]
