"""Background copies can cover unstarted requests without giving eviction rights."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.control.causal_graph import InvocationState as State
from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from tests.test_queued_full_donor import queued_fixture
from tests.test_sglang_v0520_runtime import req
from tests.test_waiting_full_tail_backup import native_cache


def fixture():
    runtime, parent, key, request, cache = queued_fixture()
    runtime._critical_parent_demand = None
    runtime._residency_budget = PrefetchResidencyBudget(
        0, 0, source="legacy_capacity_unavailable",
    )
    cache.prepare_beliefkv_idle_full = Mock()
    runtime._long_tool_wait = Mock(side_effect=AssertionError("no forecast for background copies"))
    return runtime, parent, key, request, cache


def test_queued_owner_is_backup_eligible_only_during_the_native_idle_copy():
    runtime, _, key, request, cache = fixture()
    owners = ((key.session_id, key.session_generation),)

    def prepare():
        for _ in range(32):
            assert runtime._live_backup_full_owners(owners)
            assert not runtime._live_cold_full_owners(owners)
        return ((71, 1., 4),)

    cache.prepare_beliefkv_idle_full.side_effect = prepare
    try:
        assert not runtime._live_backup_full_owners(owners)
        runtime.dispatch_idle_prepare()
        assert not runtime._live_backup_full_owners(owners)
        assert runtime._cold_full_backups == {71: (1., "idle")}
        assert runtime.counts["idle_full_prepare_queued_index_builds"] == 1
        assert runtime._idle_backup_queued_requests is None
        assert not runtime._idle_backup_queue_scope
        assert request.kv.req_pool_idx is None
        assert not request.output_ids
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


@pytest.mark.parametrize("change", (
    "row", "allocated", "committed", "retracted", "retracted_stain", "output",
    "finished", "metadata_epoch", "generation", "visible", "context_epoch",
    "workflow_end", "ready", "done", "active_tool", "active_child", "running",
    "removed", "missing_kv", "missing_row", "missing_output",
))
def test_queued_backup_rechecks_identity_queue_and_native_allocation(change):
    runtime, _, key, request, cache = fixture()
    owners = ((key.session_id, key.session_generation),)

    def prepare():
        assert runtime._live_backup_full_owners(owners)
        if change == "row":
            request.kv.req_pool_idx = 0
        elif change == "allocated":
            request.kv.kv_allocated_len = 1
        elif change == "committed":
            request.kv.kv_committed_len = 1
        elif change in ("retracted", "retracted_stain"):
            setattr(request, "is_retracted" if change == "retracted" else change, True)
        elif change == "output":
            request.output_ids.append(42)
        elif change == "finished":
            request.finished = lambda: True
        elif change == "metadata_epoch":
            request.beliefkv_metadata["context_epoch"] += 1
        elif change == "generation":
            request.session_generation += 1
        elif change == "visible":
            runtime.visible.pop(request.rid)
        elif change == "context_epoch":
            runtime.graph.contexts[key.context_id].epoch += 1
        elif change == "workflow_end":
            runtime.graph.workflows[key.root_workflow_id].end_ts_ms = 1.
        elif change in ("ready", "done"):
            runtime.graph.invocations[key.invocation_id].state = getattr(State, change.upper())
        elif change == "active_tool":
            runtime.graph.invocations[key.invocation_id].active_tool_calls = {"tool"}
        elif change == "active_child":
            runtime.graph.invocations[key.invocation_id].blocking_child_ids = {"child"}
        elif change == "running":
            runtime._native_running_batch = NS(reqs=[request])
        elif change == "removed":
            runtime._capacity_waiting_queue = []
        elif change == "missing_kv":
            request.kv = None
        elif change == "missing_row":
            del request.kv.req_pool_idx
        else:
            del request.output_ids
        assert not runtime._live_backup_full_owners(owners)
        return ()

    cache.prepare_beliefkv_idle_full.side_effect = prepare
    try:
        runtime.dispatch_idle_prepare()
        assert not runtime._idle_backup_queue_scope
        assert runtime._idle_backup_queued_requests is None
        assert not runtime._cold_full_backups
    finally:
        runtime.close()


def test_shared_owner_proof_stays_exact_and_never_uses_a_duration_model():
    runtime, parent, key, _, cache = fixture()

    def prepare():
        owners = ((key.session_id, key.session_generation), (parent.session_id, parent.session_generation))
        assert runtime._live_backup_full_owners(owners)
        assert not runtime._live_backup_full_owners((*owners, ("unknown", 1)))
        assert not runtime._live_backup_full_owners(((key.session_id, key.session_generation + 1),))
        runtime.graph.invocations[parent.invocation_id].state = State.READY
        assert not runtime._live_backup_full_owners(owners)
        return ()

    cache.prepare_beliefkv_idle_full.side_effect = prepare
    try:
        runtime.dispatch_idle_prepare()
        assert runtime.counts["idle_full_prepare_queued_index_builds"] == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("waiting", (False, True))
def test_existing_waiting_and_empty_rounds_do_not_build_the_queue_index(waiting):
    runtime, _, key, _, cache = fixture()
    runtime.graph.invocations[key.invocation_id].state = State.WAIT_TOOL

    def prepare():
        if waiting:
            assert runtime._live_backup_full_owners(((key.session_id, key.session_generation),))
        return ()

    cache.prepare_beliefkv_idle_full.side_effect = prepare
    try:
        runtime.dispatch_idle_prepare()
        assert runtime.counts["idle_full_prepare_queued_index_builds"] == 0
    finally:
        runtime.close()


def test_queue_scope_is_bounded_and_released_when_native_backup_raises():
    runtime, _, key, request, cache = fixture()
    runtime._capacity_waiting_queue = [req("ordinary")] * 512 + [request]

    def prepare():
        assert not runtime._live_backup_full_owners(((key.session_id, key.session_generation),))
        raise RuntimeError("native copy failed")

    cache.prepare_beliefkv_idle_full.side_effect = prepare
    try:
        with pytest.raises(RuntimeError, match="native copy"):
            runtime.dispatch_idle_prepare()
        assert not runtime._idle_backup_queue_scope
        assert runtime._idle_backup_queued_requests is None
    finally:
        runtime.close()


@pytest.mark.parametrize("change", (
    "moved", "replaced_queue", "removed", "beyond_bound", "tuple_queue",
))
def test_queued_backup_position_hint_is_revalidated_with_live_membership(change):
    runtime, _, key, request, cache = fixture()
    other = req("ordinary")
    runtime._capacity_waiting_queue = [other, request]
    owners = ((key.session_id, key.session_generation),)

    def prepare():
        assert runtime._live_backup_full_owners(owners)
        if change == "moved":
            runtime._capacity_waiting_queue.reverse()
        elif change == "replaced_queue":
            runtime._capacity_waiting_queue = [request, other]
        elif change == "removed":
            runtime._capacity_waiting_queue = [other]
        elif change == "beyond_bound":
            runtime._capacity_waiting_queue = [other] * 512 + [request]
        else:
            runtime._capacity_waiting_queue = (other, request)
        assert runtime._live_backup_full_owners(owners) is (
            change in ("moved", "replaced_queue", "tuple_queue")
        )
        return ()

    cache.prepare_beliefkv_idle_full = prepare
    try:
        runtime.dispatch_idle_prepare()
        assert not runtime._idle_backup_queue_scope
        assert runtime._idle_backup_queued_requests is None
        assert not runtime._cold_full_backups
    finally:
        runtime.close()


@pytest.mark.parametrize("guard", ("none", "full_lock", "native_row", "running"))
def test_native_cpu_backup_keeps_device_pages_and_existing_reclaim_guards(native_cache, guard):
    from test_beliefkv_cold_full_prepare import fixture as native_fixture, install_cpu_enqueue
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, _, _, leaf, _ = native_fixture(native_cache)
    runtime, _, key, request, _ = fixture()
    _, generation = insert_session(cache, [1, 2, 3, 4, 5, 6], key.session_id)
    key = replace(key, session_generation=generation)
    request.session_generation = generation
    runtime.visible[request.rid] = runtime.context_sessions[key.context_id] = key
    runtime._context_session_changed(key.context_id)
    runtime.attach_native_cache(cache)
    locks = install_cpu_enqueue(cache)
    full, state = leaf.component_data[0], leaf.component_data[2]
    device = full.value
    free = allocator.available_size()
    refs = full.session_ref, state.session_ref
    if guard == "full_lock":
        full.lock_ref += 1
    elif guard == "native_row":
        request.kv.req_pool_idx = 0
    elif guard == "running":
        runtime._native_running_batch = NS(reqs=[request])
    try:
        assert cache.prepare_beliefkv_idle_full() == ()
        runtime.dispatch_idle_prepare()
        assert bool(locks) is (guard == "none")
        assert full.value is device
        assert allocator.available_size() == free
        assert (full.session_ref, state.session_ref) == refs
        assert state.host_value is None
        for node_id, lock in locks.items():
            cache.tree_core.finish_write_through([node_id], node_id)
            cache.ongoing_write_through.pop(node_id)
            cache.dec_lock_ref(node_id, lock)
        if guard == "full_lock":
            full.lock_ref -= 1
        assert cache.reclaim_beliefkv_handoff_capacity(full_tokens=free + 6, mamba_slots=0) == {}
        assert allocator.available_size() == free
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()
