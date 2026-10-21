"""Expired physical protection may leave one advisory turn for its live consumer."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind
from beliefkv.runtime.sglang_v0520_runtime import _PrefetchServiceLease
from tests.test_sglang_v0520_runtime import req, select
from tests.test_tool_predictive_transfers import event, locked_runtime, submit_restored_request


@pytest.fixture
def expired_consumer(monkeypatch):
    clock = [100.]
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    runtime, _, _, action, receipt, _ = locked_runtime()
    clock[0] = 100.434
    restored = submit_restored_request(runtime)
    restored.origin_input_ids = range(8321)
    ordinary = req("ordinary")
    runtime.register_visible_request(ordinary)
    runtime._visible_since[ordinary.rid] = 80.
    runtime.enable_resident_first = False
    monkeypatch.setattr(runtime, "_causal_rank", lambda key, index, ranks: (0, 0, index))
    cache = runtime._native_cache
    cache.inspect_beliefkv_reentry = Mock(return_value={
        "checkpoint_tokens": 8192, "device_checkpoint_tokens": 8192,
        "missing_full_tokens": 0, "missing_mamba_slots": 0,
    })
    clock[0] = 110.317
    runtime._refresh_prefetch_service_leases()
    cache.tree_core.dec_lock_ref.assert_called_once_with(1, receipt)
    assert not runtime._prefetch_service_leases
    assert runtime._prefetch_admission_hints == {
        restored.rid: runtime.visible[restored.rid],
    }
    clock[0] = 110.847
    try:
        yield runtime, restored, ordinary, cache, action
    finally:
        runtime.close()


@pytest.mark.parametrize("quota", (0, 3, 4))
def test_expired_consumer_gets_only_a_bounded_turn_without_relocking(expired_consumer, quota):
    runtime, restored, ordinary, cache, _ = expired_consumer
    runtime._prefetch_priority_normal_admissions = quota
    selected = select(runtime, [ordinary, restored]).candidates[0]
    assert selected is (restored if quota == 4 else ordinary)
    assert cache.inspect_beliefkv_reentry.call_count == int(quota == 4)
    cache.tree_core.inc_lock_ref.assert_called_once_with(1)
    assert cache.tree_core.dec_lock_ref.call_count == 1
    assert not runtime._prefetch_service_leases
    assert runtime.physical_ledger.pending_count == 0
    assert runtime.counts["prefetch_native_issued"] == 0


@pytest.mark.parametrize("observation", (
    None,
    {},
    {"device_checkpoint_tokens": 0, "missing_full_tokens": 0, "missing_mamba_slots": 0},
    {"device_checkpoint_tokens": 8192, "missing_full_tokens": 64, "missing_mamba_slots": 0},
    {"device_checkpoint_tokens": 8192, "missing_full_tokens": 0, "missing_mamba_slots": 1},
    {"device_checkpoint_tokens": 8192, "missing_full_tokens": False, "missing_mamba_slots": 0},
))
def test_expired_hint_requires_fresh_complete_residency(expired_consumer, observation):
    runtime, restored, ordinary, cache, _ = expired_consumer
    cache.inspect_beliefkv_reentry.return_value = observation
    assert select(runtime, [ordinary, restored]).candidates[0] is ordinary
    assert not runtime._prefetch_admission_hints
    assert runtime.counts["prefetch_admission_hint_not_ready"] == 1
    select(runtime, [ordinary, restored])
    cache.inspect_beliefkv_reentry.assert_called_once_with(restored)


@pytest.mark.parametrize("invalid", (
    "session", "generation", "attempt", "epoch", "workflow", "wait_tool",
))
def test_expired_hint_rechecks_consumer_identity_and_executability(expired_consumer, invalid):
    runtime, restored, ordinary, cache, _ = expired_consumer
    key = runtime.visible[restored.rid]
    changes = {
        "session": {"session_id": "different"},
        "generation": {"session_generation": 2},
        "attempt": {"attempt_id": key.attempt_id + 1},
        "epoch": {"context_epoch": key.context_epoch + 1},
        "workflow": {"root_workflow_id": "different"},
    }
    if invalid == "wait_tool":
        runtime.graph.invocations[key.invocation_id].state = InvocationState.WAIT_TOOL
    else:
        runtime._prefetch_admission_hints[restored.rid] = replace(key, **changes[invalid])
    assert select(runtime, [ordinary, restored]).candidates[0] is ordinary
    assert not runtime._prefetch_admission_hints
    cache.inspect_beliefkv_reentry.assert_not_called()


@pytest.mark.parametrize("admitted", (False, True))
def test_one_native_attempt_consumes_the_hint_even_if_admission_declines(
    expired_consumer, admitted,
):
    runtime, restored, ordinary, cache, _ = expired_consumer
    assert select(runtime, [ordinary, restored]).candidates[0] is restored
    runtime.on_prefill_candidate_result(restored, admitted=admitted, result="NO_TOKEN")
    assert not runtime._prefetch_admission_hints
    assert runtime.counts[
        "prefetch_admission_hint_admitted" if admitted else "prefetch_admission_hint_declined"
    ] == 1
    assert select(runtime, [ordinary, restored]).candidates[0] is ordinary
    cache.inspect_beliefkv_reentry.assert_called_once_with(restored)
    assert not runtime._prefetch_service_leases


@pytest.mark.parametrize("cleanup", ("abort", "retire", "finish", "close"))
def test_consumer_lifecycle_cleans_the_unlocked_hint(expired_consumer, cleanup):
    runtime, restored, _, _, _ = expired_consumer
    if cleanup == "abort":
        runtime.on_abort_request(NS(rid=restored.rid, abort_all=False))
    elif cleanup == "retire":
        runtime.retire_terminal_request(restored.rid)
    elif cleanup == "finish":
        restored.finished = lambda: True
        runtime.on_batch_completed(NS(reqs=[restored]))
    else:
        runtime.close()
    assert not runtime._prefetch_admission_hints


@pytest.mark.parametrize("source,reason,locked,demand_ready", (
    ("tool_wait", "allocation_pressure", True, True),
    ("tool_wait", "native_residency_lost", True, True),
    ("execution_handoff", "service_window_expired", True, True),
    ("tool_wait", "service_window_expired", False, True),
    ("tool_wait", "service_window_expired", True, False),
))
def test_other_release_paths_do_not_create_an_advisory_hint(
    monkeypatch, source, reason, locked, demand_ready,
):
    monkeypatch.setattr("time.monotonic", lambda: 100.)
    runtime, _, _, action, _, _ = locked_runtime()
    try:
        submit_restored_request(runtime)
        lease = runtime._prefetch_service_leases[action.command_id]
        runtime._prefetch_service_leases[action.command_id] = replace(
            lease, source=source, demand_ready=demand_ready,
            lock_params=lease.lock_params if locked else None,
            protected_bytes=lease.protected_bytes if locked else 0,
        )
        runtime._release_prefetch_service_lease(action.command_id, reason)
        assert not runtime._prefetch_admission_hints
        assert not runtime._prefetch_service_leases
    finally:
        runtime.close()


@pytest.mark.parametrize("client_bound", (False, True))
def test_join_receipt_keeps_only_the_exact_submitted_consumer(monkeypatch, client_bound):
    monkeypatch.setattr("time.monotonic", lambda: 100.)
    runtime, _, _, action, receipt, _ = locked_runtime()
    try:
        restored = submit_restored_request(runtime)
        consumer = runtime.visible[restored.rid]
        lease = runtime._prefetch_service_leases[action.command_id]
        runtime._prefetch_service_leases[action.command_id] = replace(
            lease, source="join_ticket", key=consumer if client_bound else lease.key,
        )
        monkeypatch.setattr("time.monotonic", lambda: 111.)
        runtime._release_prefetch_service_lease(action.command_id, "service_window_expired")
        assert runtime._prefetch_admission_hints == {consumer.request_id: consumer}
        runtime._native_cache.tree_core.dec_lock_ref.assert_called_once_with(1, receipt)
        assert not runtime._prefetch_service_leases
    finally:
        runtime.close()


def test_live_restore_priority_skips_unused_expired_hint_inspection(expired_consumer):
    runtime, restored, ordinary, cache, _ = expired_consumer
    key = runtime.visible[ordinary.rid]
    runtime._prefetch_service_leases["live-restore"] = _PrefetchServiceLease(
        key, "live-restore", 1, 2., "execution_handoff", None,
        110., 120., (("kv", 20),), demand_ready=True,
    )
    assert select(runtime, [ordinary, restored]).candidates[0] is ordinary
    cache.inspect_beliefkv_reentry.assert_not_called()
    assert restored.rid in runtime._prefetch_admission_hints


def test_first_ready_hint_avoids_inspecting_later_equal_priority_hints(expired_consumer):
    runtime, restored, ordinary, cache, _ = expired_consumer
    runtime.on_events((event(
        5, RuntimeEventKind.INVOCATION_CREATE, invocation_id="other",
        context_id="ctx-other", agent_definition_id="child", agent_instance_id="other",
    ),))
    other = req("other")
    other.session_id, other.session_generation = "other-session", 1
    other.origin_input_ids = range(8321)
    runtime.register_visible_request(other)
    runtime._prefetch_admission_hints[other.rid] = runtime.visible[other.rid]
    assert select(runtime, [ordinary, restored, other]).candidates[0] is restored
    cache.inspect_beliefkv_reentry.assert_called_once_with(restored)
