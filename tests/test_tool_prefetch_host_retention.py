"""Tool source copies follow the actual residency lease and exact receipts."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.runtime.sglang_v0520_physical import PrefetchLoadStep, PhysicalActionCompleted
from tests.test_tool_predictive_transfers import locked_runtime, submit_restored_request
from tests.test_prefetch_ack_prefix_control import ack_prefix_fixture, commit_prefix


@pytest.fixture
def retained_tool(monkeypatch):
    clock = [100.]
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    runtime, hint, node, action, device_receipt, observation = locked_runtime()
    runtime._release_prefetch_service_lease(action.command_id, "first_gpu_service")
    cache = runtime._native_cache
    cache.tree_core.dec_lock_ref.reset_mock()
    host_receipt = NS(node_id=node.id)
    cache.retain_beliefkv_prefetch_host = Mock(return_value=host_receipt)
    cache.tree_core.dec_host_lock_ref = Mock()
    cache.reclaim_beliefkv_expired_tool_prefetch = Mock(return_value={0: 1, 2: 1})
    step = PrefetchLoadStep(hint.key, node.id, node.creation_time, node.id, node.creation_time)
    runtime._prefetch_steps[action.command_id] = (step, "tool_wait", 2.)
    with patch(
        "beliefkv.runtime.sglang_v0520_observer.observe_unified_node_closure",
        return_value=observation,
    ):
        runtime._register_prefetch_service_lease(action)
    try:
        yield runtime, cache, node, action, device_receipt, host_receipt, observation, clock
    finally:
        runtime.close()


def test_host_copy_follows_the_same_lease_without_another_timer(retained_tool):
    runtime, cache, node, action, _, host_receipt, _, clock = retained_tool
    lease = runtime._prefetch_service_leases[action.command_id]
    assert lease.host_lock_params is host_receipt
    assert lease.expires_at == 102.
    cache.retain_beliefkv_prefetch_host.assert_called_once_with(
        node.id, node.creation_time, include_full=True, include_mamba=True,
    )
    for _ in range(10):
        clock[0] += .05
        runtime._refresh_prefetch_service_leases()
    cache.retain_beliefkv_prefetch_host.assert_called_once()
    cache.reclaim_beliefkv_expired_tool_prefetch.assert_not_called()
    cache.tree_core.dec_host_lock_ref.assert_not_called()


def test_expiry_reclaims_after_device_unlock_and_before_host_unlock(retained_tool):
    runtime, cache, node, action, device_receipt, host_receipt, _, clock = retained_tool
    def reclaim(*args, **kwargs):
        cache.tree_core.dec_lock_ref.assert_called_once_with(node.id, device_receipt)
        cache.tree_core.dec_host_lock_ref.assert_not_called()
        return {0: 64, 2: 1}
    cache.reclaim_beliefkv_expired_tool_prefetch.side_effect = reclaim
    clock[0] = 102.
    runtime._refresh_prefetch_service_leases()
    cache.reclaim_beliefkv_expired_tool_prefetch.assert_called_once_with(
        node.id, node.creation_time, session_id="s", session_generation=1,
        include_full=True, include_mamba=True,
    )
    cache.tree_core.dec_host_lock_ref.assert_called_once_with(node.id, host_receipt)
    assert not runtime._prefetch_service_leases
    assert runtime.counts["tool_prefetch_expiry_freed_full_tokens"] == 64
    assert runtime.counts["tool_prefetch_expiry_freed_mamba_slots"] == 1
    assert not runtime.physical_disabled


@pytest.mark.parametrize("reason", (
    "first_gpu_service", "terminal", "allocation_pressure", "session_changed", "close",
))
def test_other_release_paths_unpin_without_forcing_a_demotion(retained_tool, reason):
    runtime, cache, node, action, device_receipt, host_receipt, _, _ = retained_tool
    runtime._release_prefetch_service_lease(action.command_id, reason)
    runtime._release_prefetch_service_lease(action.command_id, reason)
    cache.tree_core.dec_lock_ref.assert_called_once_with(node.id, device_receipt)
    cache.tree_core.dec_host_lock_ref.assert_called_once_with(node.id, host_receipt)
    cache.reclaim_beliefkv_expired_tool_prefetch.assert_not_called()
    assert not runtime._prefetch_service_leases


def test_real_submitted_consumer_extends_host_and_device_protection_together(retained_tool):
    runtime, cache, node, action, _, host_receipt, _, clock = retained_tool
    submit_restored_request(runtime)
    lease = runtime._prefetch_service_leases[action.command_id]
    assert lease.demand_ready
    assert lease.expires_at == 110.
    assert lease.host_lock_params is host_receipt
    clock[0] = 102.1
    runtime._refresh_prefetch_service_leases()
    cache.tree_core.dec_host_lock_ref.assert_not_called()
    clock[0] = 110.
    runtime._refresh_prefetch_service_leases()
    cache.reclaim_beliefkv_expired_tool_prefetch.assert_not_called()
    cache.tree_core.dec_host_lock_ref.assert_called_once_with(node.id, host_receipt)
    assert runtime._prefetch_admission_hints


@pytest.mark.parametrize("change", ("revision", "key", "no_tools", "terminal"))
def test_expiry_cannot_demote_a_changed_or_finished_episode(retained_tool, change):
    runtime, cache, _, action, _, _, _, clock = retained_tool
    lease = runtime._prefetch_service_leases[action.command_id]
    invocation = runtime.graph.invocations[lease.key.invocation_id]
    if change == "revision":
        invocation.updated_ts_ms += 1.
    elif change == "key":
        runtime.context_sessions[lease.key.context_id] = replace(lease.key, session_generation=2)
    elif change == "no_tools":
        invocation.active_tool_calls.clear()
    else:
        runtime._terminal = lambda key: True
    clock[0] = 102.
    runtime._release_prefetch_service_lease(action.command_id, "service_window_expired")
    cache.reclaim_beliefkv_expired_tool_prefetch.assert_not_called()
    cache.tree_core.dec_host_lock_ref.assert_called_once()


def test_host_release_failure_keeps_receipt_without_double_device_unlock(retained_tool):
    runtime, cache, _, action, _, host_receipt, _, clock = retained_tool
    cache.tree_core.dec_host_lock_ref.side_effect = RuntimeError("unknown Host release")
    clock[0] = 102.
    runtime._refresh_prefetch_service_leases()
    lease = runtime._prefetch_service_leases[action.command_id]
    assert lease.lock_params is None
    assert lease.host_lock_params is host_receipt
    assert runtime.physical_disabled
    runtime._release_prefetch_service_lease(action.command_id, "close")
    cache.tree_core.dec_lock_ref.assert_called_once()
    cache.tree_core.dec_host_lock_ref.assert_called_once()


def test_device_release_failure_does_not_drop_host_protection(retained_tool):
    runtime, cache, _, action, _, host_receipt, _, _ = retained_tool
    cache.tree_core.dec_lock_ref.side_effect = RuntimeError("unknown device release")
    runtime._release_prefetch_service_lease(action.command_id, "terminal")
    cache.tree_core.dec_host_lock_ref.assert_not_called()
    assert runtime._prefetch_service_leases[action.command_id].host_lock_params is host_receipt
    assert runtime.physical_disabled


def test_each_acknowledged_node_keeps_its_own_host_source_receipt(retained_tool):
    runtime, cache, node, action, _, _, _, _ = retained_tool
    key = runtime._prefetch_service_leases[action.command_id].key
    later = PhysicalActionCompleted(
        "later", "PREFETCH_GPU", key.context_id, key.context_epoch,
        (node.id,), (("kv", 20),), 20,
    )
    runtime._prefetch_steps[later.command_id] = (
        PrefetchLoadStep(key, node.id, node.creation_time, node.id, node.creation_time),
        "tool_wait", 2.,
    )
    runtime._register_prefetch_service_lease(later)
    assert cache.retain_beliefkv_prefetch_host.call_count == 2
    assert runtime._prefetch_service_leases[later.command_id].lock_params is None
    assert runtime._prefetch_service_leases[later.command_id].host_lock_params is not None
    for command in (action.command_id, later.command_id):
        runtime._release_prefetch_service_lease(command, "first_gpu_service")
    assert cache.tree_core.dec_lock_ref.call_count == 1
    assert cache.tree_core.dec_host_lock_ref.call_count == 2


def test_merged_prefix_ack_pairs_every_source_without_another_ancestry_scan():
    runtime, key, nodes, _, locks, releases = ack_prefix_fixture(8)
    cache = runtime._native_cache
    held, host_releases = set(), []
    def retain(node_id, creation, **kwargs):
        assert creation == nodes[node_id].creation_time
        assert kwargs == {"include_full": True, "include_mamba": node_id == 8}
        held.add(node_id)
        return NS(node_id=node_id)
    def release(node_id, receipt):
        assert receipt.node_id == node_id
        held.remove(node_id)
        host_releases.append(node_id)
    cache.retain_beliefkv_prefetch_host = retain
    cache.tree_core.dec_host_lock_ref = release
    try:
        commit_prefix(runtime, key, nodes)
        assert held == set(range(1, 9))
        assert locks == [8]
        for command in tuple(runtime._prefetch_service_leases):
            runtime._release_prefetch_service_lease(command, "first_gpu_service")
        assert not held and len(host_releases) == 8
        assert releases == [8]
    finally:
        runtime.close()


def test_runtime_close_releases_host_and_device_receipts(retained_tool):
    runtime, cache, _, _, _, _, _, _ = retained_tool
    runtime.close()
    cache.tree_core.dec_lock_ref.assert_called_once()
    cache.tree_core.dec_host_lock_ref.assert_called_once()
    assert not runtime._prefetch_service_leases


@pytest.mark.parametrize("receipt_node", (None, 77))
def test_absent_host_copy_is_tolerated_but_wrong_receipt_disables_actions(
    retained_tool, receipt_node,
):
    runtime, cache, node, action, _, _, observation, _ = retained_tool
    runtime._release_prefetch_service_lease(action.command_id, "first_gpu_service")
    cache.retain_beliefkv_prefetch_host.return_value = (
        None if receipt_node is None else NS(node_id=receipt_node)
    )
    key = runtime.context_sessions["ctx-tool"]
    runtime._prefetch_steps[action.command_id] = (
        PrefetchLoadStep(key, node.id, node.creation_time, node.id, node.creation_time),
        "tool_wait", 2.,
    )
    with patch(
        "beliefkv.runtime.sglang_v0520_observer.observe_unified_node_closure",
        return_value=observation,
    ):
        runtime._register_prefetch_service_lease(action)
    assert runtime.physical_disabled is (receipt_node is not None)
    assert runtime.counts[
        "tool_prefetch_host_copy_unavailable" if receipt_node is None
        else "tool_prefetch_host_lock_acquire_failed"
    ] == 1
