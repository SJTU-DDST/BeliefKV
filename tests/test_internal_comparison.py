from __future__ import annotations

from types import SimpleNamespace as NS
import hashlib
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


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("value", ("0", "1"))
def test_explicit_shared_path_profile_is_preserved_in_declared_arm(mode, value):
    settings = arm_environment({"PROFILE_SHARED_PATH": value}, mode)
    assert settings["PROFILE_SHARED_PATH"] == value
    assert settings["BELIEFKV_PROFILE_SHARED_PATH"] == value


@pytest.mark.parametrize("value", ("", "true", True, 1))
def test_invalid_shared_path_profile_is_rejected(value):
    with pytest.raises(ValueError, match="profiling"):
        arm_environment({"PROFILE_SHARED_PATH": value}, "full")


@pytest.mark.parametrize("explicit,legacy,expected", (
    (None, True, True), (None, None, None), (False, True, False), (True, False, True),
))
def test_launch_frontier_control_precedes_explicit_historical_default(
    tmp_path, monkeypatch, explicit, legacy, expected,
):
    from scripts import run_internal_comparison as operator

    server = tmp_path / "server"
    server.mkdir()
    launch = {
        "chat_token_block_cache": True, "internal_comparison_mode": "full",
        "tool_restore_mode_requested": "execution",
    }
    if explicit is not None:
        launch["execution_frontier_restore_requested"] = explicit
    (server / "launch_configuration.json").write_text(json.dumps(launch))
    monkeypatch.setattr(operator.qualified, "last_runtime_state", Mock(return_value={"sample": True}))
    validator = Mock()
    monkeypatch.setattr(operator, "validate_runtime", validator)
    operator.observe_arm("full", tmp_path, legacy_execution_frontier_restore=legacy)
    assert validator.call_args.kwargs["execution_frontier_restore"] is expected


def join_fixture(
    monkeypatch, *, return_before_wait=False, mode="join_priority_only", scheduling=None,
    tool_continuity=None, tool_continuity_grace_ms=None,
):
    configure(monkeypatch, mode)
    if scheduling is not None:
        monkeypatch.setenv("BELIEFKV_PREFILL_SCHEDULING_POLICY", scheduling)
    if tool_continuity is not None:
        monkeypatch.setenv("BELIEFKV_ENABLE_TOOL_CONTINUITY", str(int(tool_continuity)))
    if tool_continuity_grace_ms is not None:
        monkeypatch.setenv("BELIEFKV_TOOL_CONTINUITY_GRACE_MS", str(tool_continuity_grace_ms))
    if mode in ("full", "kv_only"):
        monkeypatch.setenv("BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH", "0")
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


@pytest.mark.parametrize("mode", ("full", "kv_only"))
def test_tool_prefetch_removal_changes_only_the_restore_switch(mode):
    common = {
        "MAX_RUNNING_REQUESTS": "8", "ROOT_COUNT": "36",
        "SERVER_RANDOM_SEED": "21", "SAMPLING_SEED": "21",
    }
    original = arm_environment(common, mode)
    removed = arm_environment(common, mode, tool_prefetch=False)
    assert {
        name for name in original.keys() | removed.keys()
        if original.get(name) != removed.get(name)
    } == {"BELIEFKV_ENABLE_TOOL_PREFETCH"}
    assert removed["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "0"
    assert removed["ENABLE_TOOL_TIMING"] == removed["ONLINE_TOOL_TIMING"] == "1"
    assert removed["PREPARE_HOST"] == removed["BELIEFKV_ENABLE_BACKGROUND_PREPARE"] == "1"
    assert removed["BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH"] == "1"


@pytest.mark.parametrize("override", ("0", 0, 1))
def test_tool_prefetch_override_rejects_non_boolean_values(override):
    with pytest.raises(ValueError, match="must be a boolean"):
        arm_environment({}, "full", tool_prefetch=override)


@pytest.mark.parametrize("mode", ("join_priority_only", "scheduler_only", "native_matched"))
def test_scheduling_and_native_cannot_enable_tool_prefetch(mode):
    with pytest.raises(ValueError, match="requires custom KV"):
        arm_environment({}, mode, tool_prefetch=True)


@pytest.mark.parametrize("mode", ("full", "kv_only"))
def test_execution_restore_uses_one_path_without_disabling_long_tool_offload(mode):
    settings = arm_environment({
        "TOOL_RESTORE_MODE": "execution", "EXECUTION_FRONTIER_RESTORE": "1",
        "FRONTIER_COLD_RECLAIM": "1", "FRONTIER_COLD_RECLAIM_POLICY": "waiting",
        "TOOL_PREFETCH_PRIORITY_MS": "1500",
    }, mode)
    assert settings["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "0"
    assert settings["BELIEFKV_TOOL_PREFETCH_PRIORITY_MS"] == "0"
    assert settings["BELIEFKV_ENABLE_FRONTIER_RESTORE"] == "1"
    assert settings["BELIEFKV_FRONTIER_COLD_RECLAIM_POLICY"] == "waiting"
    assert settings["ENABLE_TOOL_TIMING"] == settings["ONLINE_TOOL_TIMING"] == "1"
    assert settings["PREPARE_HOST"] == settings["BORROWABLE_PARENT_HEADROOM"] == "1"


def test_no_tool_prefetch_keeps_online_prediction_and_cold_tool_eligibility(
    monkeypatch, tmp_path,
):
    environment = arm_environment({}, "full", tool_prefetch=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    for name in ("ONLINE_TOOL_TIMING", "BORROWABLE_PARENT_HEADROOM"):
        monkeypatch.setenv("BELIEFKV_" + name, environment[name])
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}")
    manifest = tmp_path / "dataset_manifest.json"
    manifest.write_text(json.dumps({"source": {"runtime_environment_contract": {
        "server_identity": {"sglang_version": "0.5.20"},
        "model_revision_sha256": {"config.json": hashlib.sha256(b"{}").hexdigest()},
    }}}))
    artifact = tmp_path / "tool.json"
    artifact.write_text(json.dumps({
        "calibration_coverage": .9,
        "components": {"operational_release": {"trained": True}},
        "metadata": {
            "calibration_status": "calibrated_native_heads_only",
            "native_event_timing_report": {
                "target": "time_until_all_active_tools_return_not_first_gpu_service",
            },
            "dataset_dirs": [str(tmp_path)],
            "dataset_manifest_file_sha256s": [
                hashlib.sha256(manifest.read_bytes()).hexdigest(),
            ],
        },
    }))
    runtime = NativeAdmissionRuntime(
        event_socket_path=str(tmp_path / "runtime.sock"),
        model_path=str(model), tool_timing_artifact_path=str(artifact),
        tool_timing_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
    )
    try:
        runtime.on_events((
            observed(0, Kind.WORKFLOW_START),
            observed(1, Kind.INVOCATION_CREATE,
                     invocation_id="tool", context_id="ctx-tool"),
        ))
        runtime.register_visible_request(request("tool"))
        monkeypatch.setattr("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", lambda: 1.)
        for index in range(5):
            runtime.on_events((observed(
                2 + index * 2, Kind.TOOL_START, invocation_id="tool",
                attributes={
                    "tool_run_id": str(index), "tool_name": "execute",
                    "backend_class": "sandbox", "observed_command_shape": "pytest",
                },
            ),))
            if index < 4:
                runtime.on_events((observed(
                    3 + index * 2, Kind.TOOL_END, invocation_id="tool",
                    attributes={"tool_run_id": str(index), "duration_ms": 10_000.},
                ),))
        key = runtime.context_sessions["ctx-tool"]
        assert runtime.counts["online_tool_supported"] == 1
        assert runtime._long_tool_wait(key)
        assert runtime._live_parent_pressure_key(key)
        monkeypatch.setattr(
            runtime, "inspect_context_h2d_opportunity",
            Mock(side_effect=AssertionError("disabled tool H2D must not inspect targets")),
        )
        runtime.dispatch_tool_prefetch()
        assert runtime._tool_ticket is None
        assert not runtime.counts["tool_prefetch_issued"]
        runtime._opportunity_writer = Mock()
        runtime._record_runtime_state()
        state = runtime._opportunity_writer.record.call_args.args[0]
        validate_runtime("full", state, expected_overrides={"tool_prefetch": False})
        assert state["tool_timing_only"] is state["online_tool_timing"] is True
        assert state["prepare_host"] is state["background_prepare"] is True
        runtime.on_events((observed(
            20, Kind.TOOL_END, invocation_id="tool", attributes={"tool_run_id": "4"},
        ),))
        assert not runtime._live_parent_pressure_key(key)
    finally:
        runtime._opportunity_writer = None
        runtime.close()


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
            runtime._join_priority_normal_admissions = 3
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


def test_join_only_aged_head_keeps_four_ordinary_turns_between_promotions(monkeypatch):
    runtime, requests = join_fixture(monkeypatch)
    try:
        runtime._visible_since[requests[0].rid] = time.monotonic() - 11.
        runtime.on_events((
            observed(20, Kind.INVOCATION_CREATE, invocation_id="parent2", context_id="ctx-parent2"),
            observed(
                21, Kind.INVOCATION_CREATE, invocation_id="child2", context_id="ctx-child2",
                parent_invocation_id="parent2", relation_type=RelationType.SPAWN,
            ),
            observed(22, Kind.JOIN_CREATE, join_id="join2", member_invocation_ids=("child2",)),
            observed(23, Kind.JOIN_WAIT, invocation_id="parent2", join_id="join2"),
            observed(24, Kind.RETURN, invocation_id="child2"),
        ))
        second = request("parent2")
        runtime.register_visible_request(second)
        plan = runtime.plan_native_prefill([*requests, second], running_batch=None, adder=None)
        assert plan.prioritized[0].invocation_id == "parent"
        runtime.on_prefill_candidate_result(requests[1], admitted=True, result="CONTINUE")
        assert runtime._join_priority_normal_admissions == 0
        for index in range(4):
            plan = runtime.plan_native_prefill([requests[0], second], running_batch=None, adder=None)
            assert plan.prioritized[0].invocation_id == "normal"
            name = f"ordinary-{index}"
            runtime.on_events((observed(
                30 + index, Kind.INVOCATION_CREATE, invocation_id=name, context_id=f"ctx-{name}",
            ),))
            ordinary = request(name)
            runtime.register_visible_request(ordinary)
            runtime.on_prefill_candidate_result(ordinary, admitted=True, result="CONTINUE")
        plan = runtime.plan_native_prefill([requests[0], second], running_batch=None, adder=None)
        assert plan.prioritized[0].invocation_id == "parent2"
        assert runtime.counts["join_only_priority_aged_head_bypass"] == 2
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


@pytest.mark.parametrize("mode", ("full", "scheduler_only"))
@pytest.mark.parametrize("before", (False, True))
def test_full_scheduler_promotes_join_parent_without_hbm_or_prediction(monkeypatch, mode, before):
    runtime, requests = join_fixture(monkeypatch, mode=mode, return_before_wait=before)
    try:
        runtime._visible_since[requests[0].rid] = time.monotonic() - 11.
        runtime._read_request_residency_hint = Mock(return_value=None)
        runtime._read_request_reentry = Mock(side_effect=AssertionError("no HBM requirement"))
        plan = runtime.plan_native_prefill(requests, running_batch=None, adder=None)
        assert plan.prioritized[0].invocation_id == "parent"
        assert runtime.counts["join_parent_priority_ordered"] == 1
        assert runtime.counts["join_parent_priority_aged_head_bypass"] == 1
        runtime.on_prefill_candidate_result(requests[1], admitted=False, result="NO_TOKEN")
        assert runtime._join_priority_pending == {"parent": "join"}
        runtime.on_prefill_candidate_result(requests[1], admitted=True, result="CONTINUE")
        assert not runtime._join_priority_pending
        assert runtime.counts["join_parent_priority_admitted"] == 1
        assert runtime._prefetch_priority_normal_admissions == 0
        assert runtime._join_priority_normal_admissions == 0
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


@pytest.mark.parametrize("restriction", (
    "join_quota", "shared_quota", "stale_epoch", "active_tool", "stale_session", "new_join",
))
def test_full_join_parent_priority_retains_live_guards_and_shared_quota(monkeypatch, restriction):
    runtime, requests = join_fixture(monkeypatch, mode="full")
    runtime.enable_resident_first = False
    try:
        if restriction == "join_quota":
            runtime._join_priority_normal_admissions = 3
        elif restriction == "shared_quota":
            runtime._prefetch_priority_normal_admissions = 0
        elif restriction == "stale_epoch":
            requests[1].beliefkv_metadata["context_epoch"] = 1
        elif restriction == "stale_session":
            requests[1].session_generation = 2
        elif restriction == "active_tool":
            runtime.graph.invocations["parent"].active_tool_calls["tool"] = ("execute", 0.)
        else:
            runtime.on_events((
                observed(20, Kind.INVOCATION_CREATE, invocation_id="child2", context_id="ctx-child2"),
                observed(21, Kind.JOIN_CREATE, join_id="join2", member_invocation_ids=("child2",)),
                observed(22, Kind.JOIN_WAIT, invocation_id="parent", join_id="join2"),
            ))
            assert "parent" not in runtime._join_priority_pending
        runtime.plan_native_prefill(requests, running_batch=None, adder=None)
        assert runtime._join_priority_promoted == (
            requests[1].rid if restriction == "shared_quota" else None
        )
        assert runtime.counts["join_parent_priority_ordered"] == int(restriction == "shared_quota")
    finally:
        runtime.close()


def last_child_fixture(
    monkeypatch, *, mode="full", remaining=1, scheduling=None, last_child_priority=False,
):
    configure(monkeypatch, mode)
    if scheduling is not None:
        monkeypatch.setenv("BELIEFKV_PREFILL_SCHEDULING_POLICY", scheduling)
    monkeypatch.setenv("BELIEFKV_ENABLE_JOIN_LAST_CHILD_PRIORITY", str(int(last_child_priority)))
    monkeypatch.setenv("BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH", "0")
    runtime = NativeAdmissionRuntime()
    runtime.on_events((
        observed(0, Kind.WORKFLOW_START),
        observed(1, Kind.INVOCATION_CREATE, invocation_id="normal", context_id="ctx-normal"),
        observed(2, Kind.INVOCATION_CREATE, invocation_id="parent", context_id="ctx-parent"),
        observed(3, Kind.INVOCATION_CREATE, invocation_id="child", context_id="ctx-child"),
        observed(4, Kind.INVOCATION_CREATE, invocation_id="sibling", context_id="ctx-sibling"),
        observed(5, Kind.JOIN_CREATE, join_id="join", member_invocation_ids=("child", "sibling")),
        observed(6, Kind.JOIN_WAIT, invocation_id="parent", join_id="join"),
    ))
    if remaining == 1:
        runtime.on_events((observed(7, Kind.RETURN, invocation_id="sibling"),))
    normal, child = request("normal"), request("child")
    for item in (normal, child):
        runtime.register_visible_request(item)
    runtime._visible_since[normal.rid] = time.monotonic() - 11.
    runtime._read_request_residency_hint = Mock(return_value=None)
    runtime._read_request_reentry = Mock(side_effect=AssertionError("no new cache scan"))
    return runtime, normal, child


@pytest.mark.parametrize("mode", ("full", "scheduler_only"))
@pytest.mark.parametrize("remaining", (1, 2))
def test_last_join_child_uses_observed_dependencies_without_report_notice(monkeypatch, mode, remaining):
    runtime, normal, child = last_child_fixture(monkeypatch, mode=mode, remaining=remaining)
    try:
        plan = runtime.plan_native_prefill([normal, child], running_batch=None, adder=None)
        assert plan.prioritized[0].invocation_id == ("child" if remaining == 1 else "normal")
        assert runtime.counts["join_last_child_priority_ordered"] == int(remaining == 1)
        assert not runtime._final_stages
        if remaining == 1:
            runtime.on_prefill_candidate_result(child, admitted=True, result="CONTINUE")
            assert runtime.counts["join_last_child_priority_admitted"] == 1
            assert runtime._prefetch_priority_normal_admissions == 0
            assert runtime._final_priority_normal_admissions == 0
            next_plan = runtime.plan_native_prefill([normal, child], running_batch=None, adder=None)
            assert next_plan.prioritized[0].invocation_id == "normal"
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


@pytest.mark.parametrize("restriction", (
    "shared_quota", "final_quota", "active_tool", "stale_epoch", "stale_session", "no_waiter",
    "join_timeout", "waiter_tools",
))
def test_last_join_child_priority_keeps_live_dependencies_and_fairness(monkeypatch, restriction):
    runtime, normal, child = last_child_fixture(monkeypatch)
    try:
        if restriction == "shared_quota":
            runtime._prefetch_priority_normal_admissions = 3
        elif restriction == "final_quota":
            runtime._final_priority_normal_admissions = 3
        elif restriction == "active_tool":
            runtime.on_events((observed(8, Kind.TOOL_START, invocation_id="child"),))
        elif restriction == "stale_epoch":
            child.beliefkv_metadata["context_epoch"] = 1
        elif restriction == "stale_session":
            child.session_generation = 2
        elif restriction == "join_timeout":
            runtime.on_events((observed(8, Kind.JOIN_TIMEOUT, invocation_id="parent", join_id="join"),))
        elif restriction == "waiter_tools":
            runtime.graph.invocations["parent"].active_tool_calls["tool"] = ("execute", 0.)
        else:
            runtime.graph.joins["join"].waiter_invocation_ids.clear()
        plan = runtime.plan_native_prefill([normal, child], running_batch=None, adder=None)
        assert plan.prioritized[0].invocation_id == "normal"
        assert runtime._final_priority_promoted is None
        assert runtime.counts["join_last_child_priority_ordered"] == 0
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


def test_parent_boost_cannot_be_followed_by_child_boost_before_four_ordinary_admissions(monkeypatch):
    runtime, normal, child = last_child_fixture(monkeypatch)
    try:
        runtime.on_events((
            observed(10, Kind.INVOCATION_CREATE, invocation_id="parent2", context_id="ctx-parent2"),
            observed(11, Kind.INVOCATION_CREATE, invocation_id="child2", context_id="ctx-child2"),
            observed(12, Kind.JOIN_CREATE, join_id="join2", member_invocation_ids=("child2",)),
            observed(13, Kind.JOIN_WAIT, invocation_id="parent2", join_id="join2"),
            observed(14, Kind.RETURN, invocation_id="child2"),
        ))
        parent = request("parent2")
        runtime.register_visible_request(parent)
        plan = runtime.plan_native_prefill([normal, child, parent], running_batch=None, adder=None)
        assert plan.prioritized[0].invocation_id == "parent2"
        runtime.on_prefill_candidate_result(parent, admitted=True, result="CONTINUE")
        for index in range(4):
            plan = runtime.plan_native_prefill([normal, child], running_batch=None, adder=None)
            assert plan.prioritized[0].invocation_id == "normal"
            runtime.on_prefill_candidate_result(normal, admitted=True, result="CONTINUE")
        plan = runtime.plan_native_prefill([normal, child], running_batch=None, adder=None)
        assert plan.prioritized[0].invocation_id == "child"
    finally:
        runtime.close()


def test_protected_restore_priority_keeps_precedence_over_join_parent(monkeypatch):
    from beliefkv.runtime.sglang_v0520_runtime import _PrefetchServiceLease

    runtime, requests = join_fixture(monkeypatch, mode="full")
    runtime.enable_resident_first = False
    try:
        normal, parent = requests
        runtime._visible_since[parent.rid] = time.monotonic() - 11.
        key = runtime.visible[normal.rid]
        runtime._prefetch_service_leases["restore"] = _PrefetchServiceLease(
            key=key, command_id="restore", node_id=1, creation_time=1,
            source="execution_handoff", wait_revision=None,
            acknowledged_at=time.monotonic(), expires_at=time.monotonic() + 10.,
            pool_bytes=(), demand_ready=True,
        )
        plan = runtime.plan_native_prefill([parent, normal], running_batch=None, adder=None)
        assert plan.prioritized[0].invocation_id == "parent"
        assert runtime._prefetch_priority_promoted is None
        assert runtime._join_priority_promoted is None
        assert runtime._join_priority_pending == {"parent": "join"}
    finally:
        runtime.close()


@pytest.mark.parametrize("mode", ("full", "scheduler_only", "join_priority_only", "kv_only"))
def test_join_parent_priority_mode_is_explicit(monkeypatch, mode):
    configure(monkeypatch, mode)
    monkeypatch.setenv("BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH", "0")
    runtime = NativeAdmissionRuntime()
    try:
        assert runtime.enable_join_parent_priority is (mode != "kv_only")
    finally:
        runtime.close()


@pytest.mark.parametrize("mode", tuple(SCHEDULING_ONLY))
def test_scheduling_only_tool_events_do_not_attempt_a_missing_predictor(monkeypatch, mode):
    configure(monkeypatch, mode)
    runtime = NativeAdmissionRuntime()
    try:
        runtime.on_events((
            observed(0, Kind.WORKFLOW_START),
            observed(1, Kind.INVOCATION_CREATE, invocation_id="agent", context_id="ctx-agent"),
        ))
        runtime.register_visible_request(request("agent"))
        runtime._submit_tool_wait = Mock(side_effect=AssertionError("no timing consumer"))
        runtime.on_events((
            observed(2, Kind.TOOL_START, invocation_id="agent"),
            observed(3, Kind.TOOL_END, invocation_id="agent"),
        ))
        runtime._submit_tool_wait.assert_not_called()
        assert runtime.graph.invocations["agent"].state.value == "ready"
    finally:
        runtime.close()


def test_join_only_discards_advisory_report_metadata_before_graph_maintenance(monkeypatch):
    runtime, _ = join_fixture(monkeypatch)
    try:
        version = runtime.graph.graph_version
        runtime.on_events((observed(
            40, Kind.STRUCTURED_ACTION, invocation_id="child", context_id="ctx-child",
            attributes={"beliefkv_child_completion_intent": True,
                        "child_completion_signal_kind": "stage",
                        "estimated_final_report_tokens": 128},
        ),))
        assert runtime.graph.graph_version == version
        assert not runtime._final_stages
        assert not runtime._child_report_notices
    finally:
        runtime.close()


@pytest.mark.parametrize("mode", tuple(SCHEDULING_ONLY))
def test_scheduling_only_skips_decode_bookkeeping_but_observes_completion(monkeypatch, mode):
    configure(monkeypatch, mode)
    runtime = NativeAdmissionRuntime()
    item = request("agent")
    item.origin_input_ids, item.output_ids = (1, 2), [3]
    item.finished = lambda: False
    try:
        runtime.register_visible_request(item)
        batch = NS(reqs=[item])
        assert runtime.on_batch_completed(batch) == ()
        assert not runtime._context_tokens
        assert item.rid in runtime.visible
        item.finished = lambda: True
        assert runtime.on_batch_completed(batch) == (item.rid,)
        assert runtime._context_tokens["ctx-agent"][:3] == (0, 2, 1)
        assert item.rid not in runtime.visible
    finally:
        runtime.close()


@pytest.mark.parametrize("mode", tuple(SCHEDULING_ONLY))
def test_scheduling_only_preserves_read_only_first_service_feedback(monkeypatch, mode):
    configure(monkeypatch, mode)
    runtime = NativeAdmissionRuntime()
    item = request("agent")
    item.origin_input_ids, item.output_ids = (1, 2), [3]
    item.finished = lambda: False
    item.cached_tokens_device = 16
    item._cache_breakdown_computed = True
    item.retracted_stain = False
    try:
        runtime.register_visible_request(item)
        runtime._residency_hint_admissions[item.rid] = (
            id(item), runtime.visible[item.rid], 16,
        )
        assert runtime.on_batch_completed(NS(reqs=[item])) == ()
        assert runtime.counts["residency_ordering_hint_native_observed"] == 1
        assert not runtime._residency_hint_admissions
        assert not runtime._context_tokens
        assert item.rid in runtime.visible
    finally:
        runtime.close()


def test_scheduler_only_final_child_ranking_reads_live_output_without_decode_bookkeeping(monkeypatch):
    configure(monkeypatch, "scheduler_only")
    runtime = NativeAdmissionRuntime()
    try:
        runtime.on_events((
            observed(0, Kind.WORKFLOW_START),
            observed(1, Kind.INVOCATION_CREATE, invocation_id="parent", context_id="ctx-parent"),
            observed(
                2, Kind.INVOCATION_CREATE, invocation_id="child", context_id="ctx-child",
                parent_invocation_id="parent", relation_type=RelationType.SPAWN,
            ),
            observed(3, Kind.JOIN_CREATE, join_id="join", member_invocation_ids=("child",)),
            observed(4, Kind.JOIN_WAIT, invocation_id="parent", join_id="join"),
        ))
        runtime.register_visible_request(request("parent"))
        child = request("child")
        child.beliefkv_metadata["context_epoch"] = 1
        child.origin_input_ids, child.output_ids = (1, 2), [3] * 40
        child.finished = lambda: False
        runtime.on_events((
            observed(
                5, Kind.STRUCTURED_ACTION, invocation_id="child", context_id="ctx-child",
                context_epoch=0, join_id="join",
                attributes={"beliefkv_child_completion_intent": True,
                            "child_completion_signal_kind": "stage",
                            "estimated_final_report_tokens": 128},
            ),
            observed(
                6, Kind.LLM_SUBMIT, invocation_id="child", context_id="ctx-child",
                context_epoch=1, attributes={"request_id": child.rid},
            ),
        ))
        runtime.register_visible_request(child)
        stage, key = runtime._final_stages["join"], runtime.visible[child.rid]
        assert runtime.on_batch_completed(NS(reqs=[child])) == ()
        assert not runtime._context_tokens
        assert runtime._final_admission_work(
            stage, key, child, now_ms=time.monotonic() * 1000.,
        ) == (88., "notice_budget")
        child.output_ids.extend([3] * 20)
        assert runtime._final_admission_work(
            stage, key, child, now_ms=time.monotonic() * 1000.,
        ) == (68., "notice_budget")
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


@pytest.mark.parametrize("mode", ("full", "native_matched"))
@pytest.mark.parametrize("setting", ("missing", False, True, None, "1"))
def test_performance_monitor_rejects_diagnostic_token_capture(
    tmp_path, monkeypatch, mode, setting,
):
    from scripts import run_internal_comparison as operator

    server = tmp_path / "server"
    server.mkdir()
    status = {"writer_error": None, "dropped_records": 0, "failed_records": 0}
    if setting != "missing":
        status["replay_token_capture"] = setting
    (server / "native_telemetry_status.json").write_text(json.dumps(status))
    monkeypatch.setattr(operator.qualified, "last_runtime_state", lambda _: None)
    if setting == "missing" or setting is False:
        operator.observe_arm(mode, tmp_path)
    else:
        with pytest.raises(ValueError, match="diagnostic token capture"):
            operator.observe_arm(mode, tmp_path)


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
