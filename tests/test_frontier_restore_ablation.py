from unittest.mock import Mock

import pytest

from beliefkv.experiments.internal_comparison import MODES, arm_environment, validate_runtime
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_execution_frontier_restore import fixture


def test_disabled_frontier_does_not_restore_speculative_tool_work_or_delay_admission(monkeypatch):
    monkeypatch.setenv("BELIEFKV_ENABLE_FRONTIER_RESTORE", "0")
    runtime, requests, cache = fixture(monkeypatch)
    runtime.plan_native_prefill = Mock(side_effect=AssertionError("no extra admission plan"))
    runtime._wait_prefetch_opportunity = Mock(side_effect=AssertionError("no tool forecast"))
    runtime._issue_handoff_prefetch_steps = Mock(side_effect=AssertionError("no early H2D"))
    try:
        assert runtime.tool_restore_mode == "execution"
        assert not runtime.enable_frontier_restore
        assert not runtime.enable_tool_prefetch
        assert runtime.enable_prepare_host
        runtime.dispatch_frontier_restore(requests, running_batch=None)
        runtime.dispatch_execution_handoff(requests, running_batch=None)
        runtime.dispatch_tool_prefetch()
        assert not runtime.defer_prefill_for_prefetch(requests[0])
        cache.inspect_beliefkv_reentry.assert_not_called()
        cache.session_refs.snapshot_latest_session_leaf_anchors.assert_not_called()
        assert not runtime.physical_ledger.pending_count
        assert not runtime._frontier_probe_after_ms
        assert runtime._execution_handoff is None
    finally:
        runtime.close()


def test_disabled_frontier_preserves_eos_and_background_backup_dispatch(monkeypatch):
    monkeypatch.setenv("BELIEFKV_ENABLE_FRONTIER_RESTORE", "0")
    runtime, requests, _ = fixture(monkeypatch)
    runtime.enable_final_stage_prefetch = True
    runtime._eos_prefetch_keys["child"] = runtime.visible[requests[0].rid]
    runtime.dispatch_join_prefetch = Mock()
    runtime.dispatch_frontier_restore = Mock(side_effect=AssertionError("frontier disabled"))
    runtime.dispatch_tool_prefetch = Mock(side_effect=AssertionError("no speculative fallback"))
    runtime.dispatch_join_prepare = Mock(return_value=False)
    runtime.dispatch_tool_prepare = Mock(return_value=False)
    runtime.dispatch_idle_prepare = Mock()
    try:
        runtime.dispatch_after_forward(requests)
        runtime.dispatch_join_prefetch.assert_called_once()
        runtime.dispatch_join_prepare.assert_called_once()
        runtime.dispatch_tool_prepare.assert_called_once()
        runtime.dispatch_idle_prepare.assert_called_once()
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("setting", ("0", "1"))
def test_frontier_restore_ablation_matches_observed_runtime_and_other_controls(
    monkeypatch, tmp_path, mode, setting,
):
    baseline = arm_environment({
        "PREFILL_SCHEDULING_POLICY": "join_priority",
        "TOOL_RESTORE_MODE": "execution",
    }, mode)
    environment = arm_environment({
        "PREFILL_SCHEDULING_POLICY": "join_priority",
        "TOOL_RESTORE_MODE": "execution",
        "EXECUTION_FRONTIER_RESTORE": setting,
    }, mode)
    changed = {
        name for name in baseline.keys() | environment.keys()
        if baseline.get(name) != environment.get(name)
    }
    assert changed <= {"EXECUTION_FRONTIER_RESTORE", "BELIEFKV_ENABLE_FRONTIER_RESTORE"}
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    for name in ("ONLINE_TOOL_TIMING", "BORROWABLE_PARENT_HEADROOM"):
        monkeypatch.setenv("BELIEFKV_" + name, environment[name])
    if mode == "native_matched":
        assert environment["BELIEFKV_ENABLE_FRONTIER_RESTORE"] == "0"
        validate_runtime(mode, {}, execution_frontier_restore=setting == "1")
        return
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "runtime.sock"))
    runtime._opportunity_writer = Mock()
    try:
        runtime._record_runtime_state()
        state = runtime._opportunity_writer.record.call_args.args[0]
        enabled = setting == "1" and mode in ("full", "kv_only")
        assert state["execution_frontier_restore"] is enabled
        assert not state["tool_prefetch"]
        assert not state["execution_handoff"]
        kwargs = {
            "tool_restore_mode": environment["BELIEFKV_TOOL_RESTORE_MODE"],
            "execution_frontier_restore": setting == "1",
        }
        validate_runtime(mode, state, **kwargs)
        with pytest.raises(ValueError, match="execution_frontier_restore"):
            validate_runtime(mode, {**state, "execution_frontier_restore": not enabled}, **kwargs)
    finally:
        runtime._opportunity_writer = None
        runtime.close()


def test_frontier_ablation_cannot_enable_kv_in_a_scheduling_only_runtime(monkeypatch):
    environment = arm_environment({}, "scheduler_only")
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_ENABLE_TOOL_PREFETCH", "1")
    monkeypatch.setenv("BELIEFKV_TOOL_RESTORE_MODE", "execution")
    monkeypatch.setenv("BELIEFKV_ENABLE_FRONTIER_RESTORE", "0")
    with pytest.raises(ValueError, match="scheduling-only"):
        NativeAdmissionRuntime()


@pytest.mark.parametrize("setting", ("", "yes", "2"))
def test_invalid_frontier_restore_control_is_rejected(monkeypatch, setting):
    with pytest.raises(ValueError, match="execution frontier"):
        arm_environment({"EXECUTION_FRONTIER_RESTORE": setting}, "full")
    monkeypatch.setenv("BELIEFKV_ENABLE_FRONTIER_RESTORE", setting)
    with pytest.raises(ValueError, match="execution frontier"):
        NativeAdmissionRuntime()
