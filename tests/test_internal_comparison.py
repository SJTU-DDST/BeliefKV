from __future__ import annotations

from types import SimpleNamespace as NS
import json
import time
from unittest.mock import Mock

import pytest

from beliefkv.core.events import RelationType, RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import (
    MODES, ORDER, SCHEDULING_ONLY, arm_environment, validate_runtime,
)
from beliefkv.runtime.sglang_v0520_physical import PhysicalReceiptError
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime


def configure(monkeypatch, mode):
    environment = arm_environment({}, mode)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    for key in ("ONLINE_TOOL_TIMING", "BORROWABLE_PARENT_HEADROOM"):
        monkeypatch.setenv("BELIEFKV_" + key, environment[key])
    return environment


def observed(sequence, kind, **kwargs):
    return RuntimeEvent(f"comparison-{sequence}", float(sequence), kind, "wf", **kwargs)


def request(name):
    return NS(
        rid=f"req-{name}", session_id=f"session-{name}", session_generation=1,
        cache_request_handle=NS(attempt_id=0),
        beliefkv_metadata={
            "root_workflow_id": "wf", "invocation_id": name,
            "context_id": f"ctx-{name}", "context_epoch": 0,
        },
    )


def join_fixture(monkeypatch, *, return_before_wait=False):
    configure(monkeypatch, "join_priority_only")
    runtime = NativeAdmissionRuntime()
    runtime.on_events((
        observed(0, Kind.WORKFLOW_START),
        observed(1, Kind.INVOCATION_CREATE, invocation_id="normal", context_id="ctx-normal"),
        observed(2, Kind.INVOCATION_CREATE, invocation_id="parent", context_id="ctx-parent"),
        observed(
            3, Kind.INVOCATION_CREATE, invocation_id="child", context_id="ctx-child",
            parent_invocation_id="parent", relation_type=RelationType.SPAWN,
        ),
        observed(4, Kind.JOIN_CREATE, join_id="join", member_invocation_ids=("child",)),
    ))
    wait = observed(6, Kind.JOIN_WAIT, invocation_id="parent", join_id="join")
    returned = observed(5, Kind.RETURN, invocation_id="child")
    runtime.on_events((returned, wait) if return_before_wait else (wait,))
    if not return_before_wait:
        runtime.on_events((returned,))
    requests = [request("normal"), request("parent")]
    for item in requests:
        runtime.register_visible_request(item)
    return runtime, requests


@pytest.mark.parametrize("mode", MODES)
def test_independent_modes_align_frontend_and_leave_native_hicache(mode):
    env = arm_environment({"HICACHE_SIZE_GB": "200", "HOST_SPLIT": "80:20"}, mode)
    assert env["HICACHE_SIZE_GB"] == "200"
    assert env["HOST_SPLIT"] == "80:20"
    assert env["NATIVE_CHAT_TOKEN_BLOCK_CACHE"] == env["PREDICTIVE_CHAT_TOKEN_BLOCK_CACHE"] == "1"
    assert env["EXECUTION_HANDOFF"] == "0"
    assert env["JOIN_PREFETCH_TRIGGER"] == "observed_eos"
    if mode in SCHEDULING_ONLY:
        assert env["AB_MODE"] == "off"
        assert env["NATIVE_POLICY_BASELINE"] == "0"
        assert env["PREPARE_HOST"] == env["ENABLE_TOOL_TIMING"] == "0"
        assert not env["BELIEFKV_TOOL_TIMING_ARTIFACT"]
    assert set(ORDER) == set(MODES) - {"full"}


@pytest.mark.parametrize("before", (False, True))
def test_join_only_prioritizes_unlocked_submitted_parent_without_hbm_evidence(monkeypatch, before):
    runtime, requests = join_fixture(monkeypatch, return_before_wait=before)
    try:
        runtime._read_request_reentry = Mock(side_effect=AssertionError("priority cannot require HBM hit"))
        plan = runtime.plan_native_prefill(requests, running_batch=None, adder=None)
        assert [key.invocation_id for key in plan.prioritized] == ["parent", "normal"]
        assert runtime.counts["join_only_priority_ordered"] == 1
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


@pytest.mark.parametrize("restriction", ("quota", "aged_head", "stale_epoch", "still_waiting"))
def test_join_only_keeps_native_order_when_priority_not_eligible(monkeypatch, restriction):
    runtime, requests = join_fixture(monkeypatch)
    try:
        if restriction == "quota":
            runtime._join_priority_normal_admissions = 3
        elif restriction == "aged_head":
            runtime._visible_since[requests[0].rid] = time.monotonic() - 11.
        elif restriction == "stale_epoch":
            requests[1].beliefkv_metadata["context_epoch"] = 1
        else:
            runtime.graph.invocations["parent"].active_tool_calls["running"] = ("execute", 0.)
        plan = runtime.plan_native_prefill(requests, running_batch=None, adder=None)
        assert plan.prioritized[0].invocation_id == "normal"
        assert runtime.counts["join_only_priority_ordered"] == 0
    finally:
        runtime.close()


def test_join_only_consumes_priority_once_on_actual_admission(monkeypatch):
    runtime, requests = join_fixture(monkeypatch)
    try:
        runtime.plan_native_prefill(requests, running_batch=None, adder=None)
        parent = requests[1]
        runtime.on_prefill_candidate_result(parent, admitted=False, result="NO_TOKEN")
        assert runtime._join_priority_pending == {"parent": "join"}
        runtime.on_prefill_candidate_result(parent, admitted=True, result="CONTINUE")
        assert not runtime._join_priority_pending
        assert runtime._join_priority_normal_admissions == 0
        runtime.on_events((
            observed(10, Kind.TOOL_START, invocation_id="parent"),
            observed(11, Kind.TOOL_END, invocation_id="parent"),
        ))
        assert not runtime._join_priority_pending
    finally:
        runtime.close()


@pytest.mark.parametrize("mode", tuple(SCHEDULING_ONLY))
def test_scheduling_only_never_installs_our_eviction_or_retirement_hooks(monkeypatch, mode):
    configure(monkeypatch, mode)
    runtime = NativeAdmissionRuntime()
    cache = NS(
        enable_beliefkv_incremental_prepare=Mock(return_value=True),
        _evict_backed_join_parent=Mock(),
        retire_beliefkv_child_session=Mock(),
    )
    try:
        runtime.attach_native_cache(cache)
        cache.enable_beliefkv_incremental_prepare.assert_not_called()
        assert not hasattr(cache, "beliefkv_join_pressure_validator")
        assert not hasattr(cache, "beliefkv_cold_full_owner_validator")
        runtime._retire_terminal_child(None, None)
        runtime._remember_terminal_root(None, None)
        runtime._retire_ended_workflow_roots("wf")
        runtime._apply_terminal_child_reclaim(None, retry=True)
        cache.retire_beliefkv_child_session.assert_not_called()
        runtime.dispatch_after_forward(())
        assert not runtime.physical_ledger.pending_count
        with pytest.raises(PhysicalReceiptError, match="disabled"):
            runtime.register_physical_action(None)
        runtime._opportunity_writer = Mock()
        runtime._record_runtime_state()
        validate_runtime(mode, runtime._opportunity_writer.record.call_args.args[0])
    finally:
        runtime.close()


@pytest.mark.parametrize("flag", (
    "BELIEFKV_ENABLE_PREPARE_HOST", "BELIEFKV_ENABLE_TOOL_PREFETCH",
    "BELIEFKV_ENABLE_EXECUTION_HANDOFF", "BELIEFKV_BORROWABLE_PARENT_HEADROOM",
    "BELIEFKV_ONLINE_TOOL_TIMING",
))
def test_scheduling_only_rejects_accidentally_enabled_kv_actions(monkeypatch, flag):
    configure(monkeypatch, "scheduler_only")
    monkeypatch.setenv(flag, "1")
    with pytest.raises(ValueError, match="scheduling-only"):
        NativeAdmissionRuntime()


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        arm_environment({}, "reactive_means_scheduler_only")
    with pytest.raises(ValueError):
        validate_runtime("native_matched", {"unexpected": True})


@pytest.mark.parametrize("status,exit_code,live,ready", (
    ("running_native", None, True, False),
    ("completed", 0, True, False),
    ("completed", 0, False, True),
))
def test_internal_matrix_waits_for_complete_pair_and_exact_process_exit(status, exit_code, live, ready):
    from scripts.run_internal_comparison import previous_ready

    state = {"status": status, "exit_code": exit_code, "watcher": {}, "driver": {}}
    assert previous_ready(state, alive=lambda _: live) is ready


@pytest.mark.parametrize("state", (
    {"status": "stopped_for_observed_problem"},
    {"status": "completed", "exit_code": 1},
    {"status": "running_native", "watcher": {}},
))
def test_internal_matrix_does_not_launch_after_failed_or_orphaned_previous_run(state):
    from scripts.run_internal_comparison import previous_ready

    with pytest.raises(ValueError):
        previous_ready(state, alive=lambda _: False)


def test_internal_monitor_rejects_actual_frontend_configuration_drift(tmp_path):
    from scripts.run_internal_comparison import observe_arm

    server = tmp_path / "server"
    server.mkdir()
    (server / "launch_configuration.json").write_text(json.dumps({
        "chat_token_block_cache": False, "internal_comparison_mode": "native_matched",
    }))
    with pytest.raises(ValueError, match="frontend"):
        observe_arm("native_matched", tmp_path)


def test_internal_monitor_rejects_scheduler_only_physical_actions(tmp_path):
    from scripts.run_internal_comparison import observe_arm

    server = tmp_path / "server"
    server.mkdir()
    (server / "physical_action_ack.jsonl").write_text('{"action": "PREPARE_HOST"}\n')
    with pytest.raises(ValueError, match="physical-action"):
        observe_arm("scheduler_only", tmp_path)


def test_freeze_rejects_committed_execution_different_from_cpu_qualification(tmp_path):
    from beliefkv.experiments.source_provenance import sha256_file
    from scripts.run_internal_comparison import verify_cpu_snapshot

    (tmp_path / "runtime.py").write_text("qualified = True\n")
    sources = {"runtime.py": sha256_file(tmp_path / "runtime.py")}
    verify_cpu_snapshot(tmp_path, sources)
    (tmp_path / "runtime.py").write_text("qualified = False\n")
    with pytest.raises(ValueError, match="differs from CPU"):
        verify_cpu_snapshot(tmp_path, sources)


def test_freeze_preserves_unrelated_overview_edits_without_loading_them(tmp_path):
    from scripts.run_internal_comparison import verify_cpu_snapshot

    verify_cpu_snapshot(tmp_path, {"scripts/render_native_policy_overview_zh.py": "unused"})
