import time
from unittest.mock import Mock

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import MODES, arm_environment, validate_runtime
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_internal_comparison import last_child_fixture, observed, request


@pytest.mark.parametrize("mode", ("full", "scheduler_only", "join_priority_only"))
@pytest.mark.parametrize("remaining", (1, 2))
@pytest.mark.parametrize("quota", (0, 3, 4))
def test_simple_order_promotes_only_a_live_sole_child_after_the_shared_quota(
    monkeypatch, mode, remaining, quota,
):
    runtime, normal, child = last_child_fixture(
        monkeypatch, mode=mode, remaining=remaining, scheduling="join_priority",
        last_child_priority=True,
    )
    runtime._join_priority_normal_admissions = quota
    runtime._prefill_causal_ranks = Mock(side_effect=AssertionError("no ranking pass"))
    try:
        plan = runtime.plan_native_prefill([normal, child], running_batch=None, adder=None)
        promoted = remaining == 1 and quota == 4
        assert [key.invocation_id for key in plan.prioritized] == (
            ["child", "normal"] if promoted else ["normal", "child"]
        )
        runtime.on_prefill_candidate_result(child, admitted=False, result="NO_TOKEN")
        assert runtime._join_priority_normal_admissions == quota
        assert not runtime.physical_ledger.pending_count
        if promoted:
            runtime.on_prefill_candidate_result(child, admitted=True, result="CONTINUE")
            assert runtime._join_priority_normal_admissions == 0
            assert runtime.counts["join_last_child_priority_admitted"] == 1
            assert runtime.counts["join_parent_priority_admitted"] == 0
            assert runtime.counts["join_only_priority_admitted"] == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("restriction", (
    "active_child_tool", "stale_epoch", "stale_generation", "no_waiter",
    "parent_tool", "timeout", "child_terminal",
))
def test_simple_last_child_order_rechecks_actual_execution_dependencies(monkeypatch, restriction):
    runtime, normal, child = last_child_fixture(
        monkeypatch, scheduling="join_priority", last_child_priority=True,
    )
    if restriction == "active_child_tool":
        runtime.on_events((observed(8, Kind.TOOL_START, invocation_id="child"),))
    elif restriction == "stale_epoch":
        child.beliefkv_metadata["context_epoch"] = 1
    elif restriction == "stale_generation":
        child.session_generation = 2
    elif restriction == "no_waiter":
        runtime.graph.joins["join"].waiter_invocation_ids.clear()
    elif restriction == "parent_tool":
        runtime.graph.invocations["parent"].active_tool_calls["tool"] = ("execute", 0.)
    elif restriction == "timeout":
        runtime.on_events((observed(8, Kind.JOIN_TIMEOUT, join_id="join"),))
    else:
        runtime.on_events((observed(8, Kind.RETURN, invocation_id="child"),))
    try:
        plan = runtime.plan_native_prefill([normal, child], running_batch=None, adder=None)
        assert plan.prioritized[0].invocation_id == "normal"
        assert runtime._join_last_child_priority_promoted is None
    finally:
        runtime.close()


@pytest.mark.parametrize("mode", ("full", "scheduler_only", "join_priority_only"))
def test_parent_and_last_child_cannot_obtain_separate_priority_turns(monkeypatch, mode):
    runtime, normal, child = last_child_fixture(
        monkeypatch, mode=mode, scheduling="join_priority", last_child_priority=True,
    )
    runtime.on_events((
        observed(10, Kind.INVOCATION_CREATE, invocation_id="parent2", context_id="ctx-parent2"),
        observed(11, Kind.INVOCATION_CREATE, invocation_id="child2", context_id="ctx-child2"),
        observed(12, Kind.JOIN_CREATE, join_id="join2", member_invocation_ids=("child2",)),
        observed(13, Kind.JOIN_WAIT, invocation_id="parent2", join_id="join2"),
        observed(14, Kind.RETURN, invocation_id="child2"),
    ))
    parent = request("parent2")
    runtime.register_visible_request(parent)
    try:
        plan = runtime.plan_native_prefill([normal, child, parent], running_batch=None, adder=None)
        assert plan.prioritized[0].invocation_id == "parent2"
        runtime.on_prefill_candidate_result(parent, admitted=True, result="CONTINUE")
        for index in range(4):
            plan = runtime.plan_native_prefill([normal, child], running_batch=None, adder=None)
            assert plan.prioritized[0].invocation_id == "normal"
            name = f"ordinary-{index}"
            runtime.on_events((observed(
                20 + index, Kind.INVOCATION_CREATE,
                invocation_id=name, context_id=f"ctx-{name}",
            ),))
            ordinary = request(name)
            runtime.register_visible_request(ordinary)
            runtime.on_prefill_candidate_result(ordinary, admitted=True, result="CONTINUE")
        plan = runtime.plan_native_prefill([normal, child], running_batch=None, adder=None)
        assert plan.prioritized[0].invocation_id == "child"
        runtime.on_prefill_candidate_result(child, admitted=True, result="CONTINUE")
        assert runtime._join_priority_normal_admissions == 0
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


def test_child_already_at_the_head_needs_no_priority_turn(monkeypatch):
    runtime, normal, child = last_child_fixture(
        monkeypatch, scheduling="join_priority", last_child_priority=True,
    )
    runtime._visible_since[normal.rid] = time.monotonic()
    try:
        plan = runtime.plan_native_prefill([child, normal], running_batch=None, adder=None)
        assert plan.prioritized[0].invocation_id == "child"
        assert runtime._join_last_child_priority_promoted is None
        runtime.on_prefill_candidate_result(child, admitted=True, result="CONTINUE")
        assert runtime._join_priority_normal_admissions == 4
    finally:
        runtime.close()


@pytest.mark.parametrize("mode", ("full", "scheduler_only", "join_priority_only"))
def test_default_simple_order_does_not_inspect_last_children(monkeypatch, mode):
    runtime, normal, child = last_child_fixture(
        monkeypatch, mode=mode, scheduling="join_priority",
    )
    runtime._last_join_child_has_waiting_parent = Mock(
        side_effect=AssertionError("disabled candidate must be free"),
    )
    try:
        plan = runtime.plan_native_prefill([normal, child], running_batch=None, adder=None)
        assert plan.prioritized[0].invocation_id == "normal"
        assert not runtime.enable_join_last_child_priority
    finally:
        runtime.close()


def test_direct_kv_only_runtime_cannot_enable_last_child_ordering(monkeypatch):
    monkeypatch.setenv("BELIEFKV_INTERNAL_COMPARISON_MODE", "kv_only")
    monkeypatch.setenv("BELIEFKV_PREFILL_SCHEDULING_POLICY", "join_priority")
    monkeypatch.setenv("BELIEFKV_ENABLE_JOIN_LAST_CHILD_PRIORITY", "1")
    runtime = NativeAdmissionRuntime()
    try:
        assert not runtime.enable_join_last_child_priority
    finally:
        runtime.close()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("setting", ("0", "1"))
def test_last_child_ablation_is_explicit_in_environment_and_runtime(
    monkeypatch, tmp_path, mode, setting,
):
    environment = arm_environment({
        "PREFILL_SCHEDULING_POLICY": "join_priority",
        "JOIN_LAST_CHILD_PRIORITY": setting,
    }, mode)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    for name in ("ONLINE_TOOL_TIMING", "BORROWABLE_PARENT_HEADROOM"):
        monkeypatch.setenv("BELIEFKV_" + name, environment[name])
    if mode == "native_matched":
        assert environment["BELIEFKV_ENABLE_JOIN_LAST_CHILD_PRIORITY"] == "0"
        return
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "runtime.sock"))
    runtime._opportunity_writer = Mock()
    try:
        runtime._record_runtime_state()
        state = runtime._opportunity_writer.record.call_args.args[0]
        enabled = setting == "1" and mode != "kv_only"
        assert state["join_last_child_priority"] is enabled
        validate_runtime(mode, state, join_last_child_priority=enabled)
        if mode != "kv_only":
            with pytest.raises(ValueError, match="join_last_child_priority"):
                validate_runtime(mode, state, join_last_child_priority=not enabled)
    finally:
        runtime._opportunity_writer = None
        runtime.close()


@pytest.mark.parametrize("setting", ("", "yes", "2"))
def test_invalid_last_child_control_is_rejected(monkeypatch, setting):
    with pytest.raises(ValueError, match="JOIN last child"):
        arm_environment({"JOIN_LAST_CHILD_PRIORITY": setting}, "full")
    monkeypatch.setenv("BELIEFKV_ENABLE_JOIN_LAST_CHILD_PRIORITY", setting)
    with pytest.raises(ValueError, match="JOIN last child"):
        NativeAdmissionRuntime()
