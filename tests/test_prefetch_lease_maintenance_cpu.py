from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.control.causal_graph import InvocationState
from tests.test_tool_predictive_transfers import locked_runtime


def lease_burst():
    runtime, hint, _, action, receipt, _ = locked_runtime()
    original = runtime._prefetch_service_leases[action.command_id]
    nodes = {
        index: NS(
            id=index, creation_time=float(index),
            component_data={0: NS(value=object()), 2: NS(value=object())},
        )
        for index in range(1, 5)
    }
    runtime._native_cache.tree_core.node_by_id = nodes.__getitem__
    runtime._prefetch_service_leases = {
        str(index): replace(
            original, command_id=str(index), node_id=index, creation_time=float(index),
            pool_bytes=(("kv", 20), ("mamba", 30)) if index == 4 else (("kv", 20),),
            lock_params=receipt if index == 1 else None,
            protected_bytes=50 if index == 1 else 0,
        )
        for index in nodes
    }
    now = original.acknowledged_at + .1
    invocation = runtime.graph.invocations[hint.key.invocation_id]
    invocation.state = InvocationState.RUNNING_LLM
    invocation.active_tool_calls.clear()
    runtime.graph.contexts[hint.key.context_id].epoch = hint.key.context_epoch + 1
    consumer = replace(hint.key, request_id="consumer", context_epoch=hint.key.context_epoch + 1)
    runtime.context_sessions[hint.key.context_id] = consumer
    runtime.visible["consumer"] = consumer
    return runtime, hint.key, consumer, nodes, now


@pytest.mark.parametrize("mutation,node_id,reason", (
    ("full_lost", 2, "native_residency_lost"),
    ("mamba_lost", 4, "native_residency_lost"),
    ("node_replaced", 3, "node_generation_changed"),
))
def test_same_consumer_match_never_substitutes_for_each_receipts_native_validation(
    mutation, node_id, reason,
):
    runtime, _, _, nodes, now = lease_burst()
    if mutation == "node_replaced":
        nodes[node_id].creation_time += 1.
    else:
        nodes[node_id].component_data[0 if mutation == "full_lost" else 2].value = None
    try:
        with patch("time.monotonic", lambda: now):
            runtime._refresh_prefetch_service_leases()
        assert str(node_id) not in runtime._prefetch_service_leases
        assert len(runtime._prefetch_service_leases) == 3
        assert all(lease.demand_ready for lease in runtime._prefetch_service_leases.values())
        assert runtime.counts[f"prefetch_residency_released:{reason}"] == 1
        assert not runtime.physical_disabled
    finally:
        runtime.close()


def test_same_contexts_current_request_is_not_its_own_next_consumer():
    runtime, _, consumer, _, now = lease_burst()
    runtime._prefetch_service_leases["2"] = replace(
        runtime._prefetch_service_leases["2"], key=consumer,
    )
    try:
        with patch("time.monotonic", lambda: now):
            runtime._refresh_prefetch_service_leases()
        assert runtime._prefetch_service_leases["1"].demand_ready
        assert not runtime._prefetch_service_leases["2"].demand_ready
        assert runtime._prefetch_service_leases["3"].demand_ready
        assert runtime.counts["prefetch_residency_demand_ready"] == 3
    finally:
        runtime.close()


def test_consumer_matches_are_rebuilt_for_each_maintenance_call():
    runtime, key, _, _, now = lease_burst()
    original = dict(runtime._prefetch_service_leases)
    try:
        with patch("time.monotonic", lambda: now):
            runtime._refresh_prefetch_service_leases()
            assert runtime.counts["prefetch_residency_demand_ready"] == 4
            runtime._prefetch_service_leases = original
            runtime.visible = {key.request_id: key}
            runtime._lease_refresh_signature = None
            runtime._refresh_prefetch_service_leases()
        assert not any(lease.demand_ready for lease in runtime._prefetch_service_leases.values())
        assert runtime.counts["prefetch_residency_demand_ready"] == 4
    finally:
        runtime.close()


def test_shared_consumer_match_does_not_hide_expiry_crossed_between_receipts():
    runtime, _, _, nodes, now = lease_burst()
    clock = [now]
    end = max(lease.acknowledged_at for lease in runtime._prefetch_service_leases.values()) + 11.

    def node_by_id(node_id):
        clock[0] = end
        return nodes[node_id]

    runtime._native_cache.tree_core.node_by_id = node_by_id
    try:
        with patch("time.monotonic", lambda: clock[0]):
            runtime._refresh_prefetch_service_leases()
        assert set(runtime._prefetch_service_leases) == {"1"}
        assert runtime.counts["prefetch_residency_released:service_window_expired"] == 3
    finally:
        runtime.close()


@pytest.mark.parametrize("generation,remaining", ((1, 4), (2, 0)))
def test_epoch_handoff_skips_redundant_ticket_but_still_proves_native_generation(
    generation, remaining,
):
    runtime, key, _, _, now = lease_burst()
    runtime.context_sessions.pop(key.context_id)
    runtime.visible.clear()
    runtime.graph.invocations[key.invocation_id].state = InvocationState.READY
    runtime._native_cache.session_refs._session_generations[key.session_id] = generation
    runtime._prefetch_service_leases = {
        command: replace(lease, source="join_ticket")
        for command, lease in runtime._prefetch_service_leases.items()
    }
    try:
        with patch("time.monotonic", lambda: now), patch.object(
            runtime, "_pending_confirmed_join_submit",
            side_effect=AssertionError("epoch handoff already supplies this alternative"),
        ):
            runtime._refresh_prefetch_service_leases()
        assert len(runtime._prefetch_service_leases) == remaining
        assert runtime.counts["prefetch_residency_released:session_handoff_unproven"] == 4 - remaining
    finally:
        runtime.close()
