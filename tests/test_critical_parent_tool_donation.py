"""Critical restore capacity can use tool-idle agents without a timing model."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind
from beliefkv.runtime.sglang_v0520_physical import (
    capture_action_local_shadow,
    PhysicalActionExpectation,
    PhysicalChildExpectation,
)
from tests.test_sglang_v0520_physical import prepare_prefix_fixture
from tests.test_sglang_v0520_runtime import final_stage_runtime, req
from tests.test_tool_predictive_transfers import event, tool_runtime


def fixture(*, distribution="cdf"):
    runtime, original = tool_runtime()
    runtime.enable_prepare_host = True
    now_ms = original.issued_monotonic_ms + 1500.
    hint = replace(
        original,
        release_cdf=((100., 1.),) if distribution == "cdf" else (),
    )
    runtime.tool_wait_hints[hint.key.context_id] = hint
    demand = (NS(context_id="critical-parent"), 100_000, 1, now_ms / 1000. + 1.)
    return runtime, hint, demand, now_ms


@pytest.mark.parametrize("distribution", ("cdf", "quantiles", "unhinted", "stale"))
@pytest.mark.parametrize("elapsed_ms", (0., 10., 500., 1500.))
def test_critical_tool_donation_needs_neither_prediction_nor_minimum_age(
    distribution, elapsed_ms,
):
    runtime, hint, demand, now_ms = fixture(distribution=distribution)
    try:
        runtime.graph.invocations[hint.key.invocation_id].updated_ts_ms = now_ms - elapsed_ms
        if distribution == "unhinted":
            runtime.tool_wait_hints.clear()
        elif distribution == "stale":
            runtime.tool_wait_hints[hint.key.context_id] = replace(
                hint, predictor_sha256="b" * 64,
            )
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
            return_value=now_ms / 1000.,
        ), patch.object(
            runtime, "_live_critical_parent_demand", return_value=demand,
        ), patch.object(
            runtime, "_live_tool_hint", side_effect=AssertionError("no donor forecast"),
        ):
            assert runtime._long_tool_wait(hint.key)
        assert not runtime.physical_ledger.pending_count
        assert not runtime.completed_physical_actions
    finally:
        runtime.close()


@pytest.mark.parametrize("excluded", (
    "no_demand", "same_parent", "active_llm", "tool_completed", "terminal",
    "stale_session",
))
def test_critical_donation_preserves_current_wait_and_session_safety(excluded):
    runtime, hint, demand, now_ms = fixture()
    try:
        invocation = runtime.graph.invocations[hint.key.invocation_id]
        if excluded == "no_demand":
            demand = None
        elif excluded == "same_parent":
            demand = (hint.key, *demand[1:])
        elif excluded == "active_llm":
            invocation.state = InvocationState.RUNNING_LLM
        elif excluded == "tool_completed":
            invocation.active_tool_calls.clear()
        elif excluded == "terminal":
            invocation.state = InvocationState.DONE
        else:
            runtime.context_sessions[hint.key.context_id] = replace(
                hint.key, session_generation=2,
            )
        runtime.tool_wait_hints[hint.key.context_id] = hint
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
            return_value=now_ms / 1000.,
        ), patch.object(runtime, "_live_critical_parent_demand", return_value=demand):
            assert not runtime._critical_tool_donor(hint.key)
            assert not runtime._long_tool_wait(hint.key)
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


@pytest.mark.parametrize("long_wait", (False, True))
def test_without_critical_parent_regular_prediction_policy_is_unchanged(long_wait):
    runtime, hint, _, now_ms = fixture()
    try:
        hint = replace(
            hint, issued_monotonic_ms=now_ms,
            release_cdf=((20_000. if long_wait else 100., 1.),),
        )
        runtime.tool_wait_hints[hint.key.context_id] = hint
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
            return_value=now_ms / 1000.,
        ), patch.object(runtime, "_live_critical_parent_demand", return_value=None):
            assert runtime._long_tool_wait(hint.key) is long_wait
    finally:
        runtime.close()


@pytest.mark.parametrize("blocked", (
    None, "device_lock", "host_lock", "shared", "foreign_owner", "write_inflight",
    "load_inflight",
))
@pytest.mark.parametrize("distribution", ("unhinted", "supported_short"))
def test_just_started_tool_dispatch_keeps_private_leaf_and_ack_contract(
    blocked, distribution,
):
    runtime, hint, demand, now_ms = fixture()
    cache, anchors, nodes = prepare_prefix_fixture()
    anchors = replace(anchors, key=hint.key)
    for node in nodes.values():
        for data in node.component_data.values():
            data.session_ids = {hint.key.session_id}
        node.backuped = False
    leaf = nodes[3]
    if blocked == "device_lock":
        leaf.component_data[0].lock_ref = 1
    elif blocked == "shared":
        leaf.component_data[0].session_ref = 2
    elif blocked == "foreign_owner":
        leaf.component_data[0].session_ids = {"other"}
    elif blocked == "host_lock":
        leaf.component_data[0].host_lock_ref = 1
    elif blocked == "write_inflight":
        leaf.write_through_pending_id = 1
    elif blocked == "load_inflight":
        leaf.load_back_pending_id = 1
    cache.tree_core.evictable_device_leaves = (leaf,)
    cache.supports_beliefkv_resident_parent_prepare = lambda: True
    runtime.attach_native_cache(cache)
    candidate = capture_action_local_shadow(cache, anchors, include_non_actionable=True)
    controller = cache.cache_controller
    controller.mem_pool_host = cache.host_pool_group
    controller._num_tokens_by_pool = lambda op: {"kv": len(op.device_indices)}
    unit = cache.host_pool_group.entry_map["kv"].host_pool.size_per_token
    controller._transfer_num_bytes = lambda op: len(op.device_indices) * unit
    cache.prepare_host_session_nodes = Mock(side_effect=AssertionError("single private leaf"))

    def prepare(**kwargs):
        assert kwargs["node_id"] == leaf.id
        assert kwargs["beliefkv_include_mamba"] is False
        assert kwargs["beliefkv_before_enqueue"](NS(
            beliefkv_command_id=kwargs["beliefkv_command_id"],
            node_ids=[leaf.id], device_indices=(0, 1, 2, 3), host_indices=(0, 1, 2, 3),
        ))
        return NS(issued=True, node_id=leaf.id)

    cache.prepare_host_shadow = Mock(side_effect=prepare)
    invocation = runtime.graph.invocations[hint.key.invocation_id]
    invocation.updated_ts_ms = now_ms
    if distribution == "unhinted":
        runtime.tool_wait_hints.clear()
    else:
        runtime.tool_wait_hints[hint.key.context_id] = replace(
            hint, invocation_revision_ts_ms=now_ms,
            issued_monotonic_ms=now_ms, release_cdf=((10., 1.),),
        )
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
            return_value=now_ms / 1000.,
        ), patch.object(
            runtime, "_live_critical_parent_demand", return_value=demand,
        ), patch.object(
            runtime, "capture_shadow_candidate", return_value=candidate,
        ), patch.object(
            runtime, "_estimate_d2h_service", side_effect=AssertionError("no timing gate"),
        ), patch.object(
            runtime, "_estimate_h2d_service", side_effect=AssertionError("no timing gate"),
        ), patch(
            "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
            return_value=NS(
                observable=True, device_full_free_tokens=0, device_mamba_free_slots=10,
                host_full_free_tokens=100, host_mamba_free_slots=10,
            ),
        ):
            runtime.dispatch_tool_prepare((NS(),), running_batch=NS(reqs=()))
        assert cache.prepare_host_shadow.call_count == int(blocked is None)
        assert runtime.physical_ledger.pending_count == int(blocked is None)
        assert not runtime.completed_physical_actions
        assert leaf.component_data[0].value is not None
        assert leaf.component_data[2].value is not None
    finally:
        runtime.close()


@pytest.mark.parametrize("final_state", ("waiting", "returned", "parallel_wait"))
def test_tool_boundary_rearms_only_a_still_waiting_donor(final_state):
    runtime, hint, demand, now_ms = fixture()
    runtime._prepare_probe_after_ms[hint.key] = now_ms + 1000.
    runtime._tool_prepare_next_ms = now_ms + 100.
    runtime.tool_wait_hints.clear()
    events = [
        event(3, RuntimeEventKind.TOOL_END, invocation_id="tool",
              attributes={"tool_run_id": "long"}),
        event(4, RuntimeEventKind.TOOL_START, invocation_id="tool",
              attributes={"tool_run_id": "new", "tool_family": "shell"}),
    ]
    if final_state == "returned":
        events.append(event(5, RuntimeEventKind.TOOL_END, invocation_id="tool",
                            attributes={"tool_run_id": "new"}))
    elif final_state == "parallel_wait":
        events.append(event(5, RuntimeEventKind.TOOL_START, invocation_id="tool",
                            attributes={"tool_run_id": "second", "tool_family": "shell"}))
    try:
        with patch.object(runtime, "_live_critical_parent_demand", return_value=demand):
            runtime.on_events(tuple(events))
        if final_state == "returned":
            assert runtime._prepare_probe_after_ms[hint.key] == now_ms + 1000.
            assert runtime._tool_prepare_next_ms == now_ms + 100.
        else:
            assert hint.key not in runtime._prepare_probe_after_ms
            assert runtime._tool_prepare_next_ms == 0.
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


def test_new_critical_demand_rearms_existing_waits_only_on_material_change():
    runtime = final_stage_runtime()
    donor = req("other")
    donor.session_id, donor.session_generation = "donor", 1
    runtime.register_visible_request(donor)
    runtime.on_events((event(7, RuntimeEventKind.TOOL_START, invocation_id="other",
                             attributes={"tool_run_id": "fresh"}),))
    donor_key = runtime.context_sessions["ctx-other"]
    key = runtime.context_sessions["ctx-parent"]
    now_ms = 10_000.
    expires = now_ms / 1000. + 1.
    runtime._prepare_probe_after_ms[donor_key] = now_ms + 1000.
    runtime._tool_prepare_next_ms = now_ms + 100.
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
            return_value=now_ms / 1000.,
        ):
            runtime._set_critical_parent_demand(key, 100_000, 1, expires)
            assert donor_key not in runtime._prepare_probe_after_ms
            assert runtime._tool_prepare_next_ms == 0.
            runtime._prepare_probe_after_ms[donor_key] = now_ms + 1000.
            runtime._set_critical_parent_demand(key, 100_001, 1, expires)
            assert donor_key not in runtime._prepare_probe_after_ms
            runtime._prepare_probe_after_ms[donor_key] = now_ms + 1000.
            runtime._tool_prepare_next_ms = now_ms + 100.
            runtime._set_critical_parent_demand(key, 100_001, 1, expires + 1.)
            assert runtime._prepare_probe_after_ms[donor_key] == now_ms + 1000.
            assert runtime._tool_prepare_next_ms == now_ms + 100.
            assert runtime._critical_tool_donor(donor_key)
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
            return_value=expires + 1.,
        ):
            assert not runtime._critical_tool_donor(donor_key)
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


@pytest.mark.parametrize("tool_returned", (False, True))
def test_prepare_ack_rearms_reclaim_only_while_the_donor_still_waits(tool_returned):
    runtime, hint, demand, now_ms = fixture()
    runtime.register_physical_action(PhysicalActionExpectation(
        "critical-prepare", "PREPARE_HOST", hint.key.context_id, hint.key.context_epoch,
        (PhysicalChildExpectation(11, (11,), (("kv", 20),), 20),),
        (("kv", 10),), session_id=hint.key.session_id,
        session_generation=hint.key.session_generation,
    ))
    runtime._critical_reclaim_next_ms = now_ms + 100.
    if tool_returned:
        runtime.on_events((event(3, RuntimeEventKind.TOOL_END, invocation_id="tool",
                                 attributes={"tool_run_id": "long"}),))
    try:
        with patch.object(runtime, "_live_critical_parent_demand", return_value=demand):
            completed = runtime.on_native_transfer_commit(NS(
                direction="d2h", status="completed", node_ids=(11,),
                num_tokens_by_pool=(("kv", 2),),
                child_commits=(NS(
                    command_id="critical-prepare", anchor_node_id=11,
                    published_node_ids=(11,), num_tokens_by_pool=(("kv", 2),),
                    num_bytes=20,
                ),),
            ))
            assert len(completed) == 1
            assert runtime._critical_reclaim_next_ms == (
                now_ms + 100. if tool_returned else 0.
            )
            assert runtime._live_parent_pressure_key(hint.key) is not tool_returned
    finally:
        runtime.close()


def test_acked_cold_capacity_is_reclaimed_before_planning_another_backup():
    runtime, _, demand, _ = fixture()
    runtime._critical_parent_demand = demand
    calls = []
    runtime._reclaim_critical_parent_headroom = lambda: calls.append("reclaim")
    runtime.dispatch_join_prepare = lambda *args, **kwargs: calls.append("join_prepare")
    runtime.dispatch_tool_prepare = lambda *args, **kwargs: calls.append("tool_prepare")
    runtime.enable_tool_prefetch = False
    try:
        runtime.dispatch_after_forward()
        assert calls == ["reclaim", "join_prepare", "tool_prepare"]
    finally:
        runtime.close()
