"""All current session owners must remain causally cold at actual reclaim."""

from dataclasses import replace
import time
from unittest.mock import Mock

import pytest

from beliefkv.control.causal_graph import InvocationState
from tests.test_sglang_v0520_runtime import final_stage_runtime


def fixture():
    runtime = final_stage_runtime()
    runtime.enable_prepare_host = True
    first = runtime.context_sessions["ctx-parent"]
    second = replace(
        first, context_id="ctx-other", invocation_id="other",
        request_id="other-request", session_id="other-session", session_generation=2,
    )
    runtime.context_sessions[second.context_id] = second
    runtime._context_session_changed(second.context_id)
    other = runtime.graph.invocations["other"]
    other.state = InvocationState.WAIT_JOIN
    other.join_id = "other-join"
    runtime.graph.joins["other-join"] = replace(
        runtime.graph.joins["join"], join_id="other-join", waiter_invocation_ids={"other"},
    )
    return runtime, first, second


def test_all_waiting_owners_are_accepted_without_per_node_owner_index_rebuild():
    runtime, first, second = fixture()
    try:
        owners = ((first.session_id, first.session_generation), (second.session_id, second.session_generation))
        for _ in range(32):
            assert runtime._live_cold_full_owners(owners)
        assert runtime.counts["cold_full_owner_index_built"] == 1
        assert not runtime._parent_pressure_candidates
    finally:
        runtime.close()


@pytest.mark.parametrize("state", (
    InvocationState.READY, InvocationState.RUNNING_LLM, InvocationState.DONE,
))
def test_one_runnable_or_terminal_owner_blocks_shared_reclaim_without_index_rebuild(state):
    runtime, first, second = fixture()
    try:
        owners = ((first.session_id, first.session_generation), (second.session_id, second.session_generation))
        assert runtime._live_cold_full_owners(owners)
        runtime.graph.invocations["other"].state = state
        assert not runtime._live_cold_full_owners(owners)
        assert runtime.counts["cold_full_owner_index_built"] == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("owners", (
    (), (("unknown", 1),), (("s", 99),), (("s", None),),
))
def test_unknown_missing_and_stale_native_owner_identity_is_rejected(owners):
    runtime, _, _ = fixture()
    try:
        assert not runtime._live_cold_full_owners(owners)
    finally:
        runtime.close()


def test_restore_beneficiary_is_protected_and_unhinted_tool_donor_requires_live_demand():
    runtime, parent, donor = fixture()
    try:
        other = runtime.graph.invocations["other"]
        other.state = InvocationState.WAIT_TOOL
        other.active_tool_calls = {"tool": ("shell", 0.)}
        owners = ((donor.session_id, donor.session_generation),)
        assert not runtime._live_cold_full_owners(owners)
        runtime._set_critical_parent_demand(parent, 4000, 1, time.monotonic() + 10.)
        assert runtime._live_cold_full_owners(owners)
        assert not runtime._live_cold_full_owners(((parent.session_id, parent.session_generation),))
        other.active_tool_calls.clear()
        assert not runtime._live_cold_full_owners(owners)
    finally:
        runtime.close()


def test_session_rebinding_rebuilds_once_and_rejects_the_previous_generation():
    runtime, _, second = fixture()
    try:
        assert runtime._live_cold_full_owners(((second.session_id, 2),))
        runtime.context_sessions[second.context_id] = replace(second, session_generation=3)
        runtime._context_session_changed(second.context_id)
        assert not runtime._live_cold_full_owners(((second.session_id, 2),))
        assert runtime._live_cold_full_owners(((second.session_id, 3),))
        assert runtime.counts["cold_full_owner_index_built"] == 2
    finally:
        runtime.close()


@pytest.mark.parametrize("capacity_changed", (False, True))
def test_new_eos_capacity_is_consumed_in_the_same_safe_point_only_after_real_progress(capacity_changed):
    runtime, parent, _ = fixture()
    calls = []
    runtime._eos_prefetch_keys["eos"] = object()
    runtime.enable_tool_prefetch = False

    def join():
        calls.append("join")
        runtime._critical_parent_demand = (parent, 4000, 1, time.monotonic() + 10.)

    def reclaim():
        calls.append("reclaim")
        return capacity_changed

    runtime.dispatch_join_prefetch = join
    runtime._reclaim_critical_parent_headroom = reclaim
    runtime.dispatch_join_prepare = Mock()
    runtime.dispatch_tool_prepare = Mock()
    runtime.dispatch_idle_prepare = Mock()
    try:
        runtime.dispatch_after_forward()
        assert calls == (["join", "reclaim", "join"] if capacity_changed else ["join", "reclaim"])
        assert runtime.counts["critical_capacity_immediate_join_replans"] == int(capacity_changed)
        runtime.dispatch_join_prepare.assert_called_once()
    finally:
        runtime._eos_prefetch_keys.clear()
        runtime.close()
