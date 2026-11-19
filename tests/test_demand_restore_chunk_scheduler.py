"""D3 partial demand restore through the native scheduler and PrefillAdder."""

from array import array
from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.subagent_materialization import SubagentMaterialization
from tests.test_demand_restore_capacity import _backup, bind_host_slots
from tests.test_demand_prefill_pipeline_native import staged_native
from tests.test_demand_prefill_pipeline_scheduler import (
    BatchConstructionReached,
    native_scheduler,
)
from tests.test_full_only_native_lifecycle import full_native as base_full_native


@pytest.fixture
def full_native(base_full_native, monkeypatch):
    cache, _, writes, _, _ = base_full_native
    unit = cache.host_pool_group.size_per_token
    spawn_reserve = (64 * 1024**2 + unit - 1) // unit
    bind_host_slots(cache, writes, spawn_reserve + 16, monkeypatch)
    monkeypatch.setenv("SGLANG_HICACHE_SKIP_HOST_DUPLICATE_RECLAIM", "0")
    return base_full_native


@pytest.mark.parametrize("staged_native", ({"members": 1, "select": False},), indirect=True)
@pytest.mark.parametrize("outcome", ("resume", "abort", "new_epoch", "control"))
def test_d3_stages_before_batch_construction(staged_native, monkeypatch, outcome):
    from sglang.srt.managers.schedule_policy import AddReqResult
    from test_beliefkv_terminal_capacity import insert_session

    f = staged_native
    cache, req = f.cache, f.req
    tree, host = cache.tree_core, cache.host_pool_group.anchor_entry.host_pool
    held_host = host.alloc(host.size - 16)
    neighbor, _ = insert_session(cache, range(101, 105), "neighbor")
    neighbor_slots = _backup(cache, neighbor, demote=True)
    mirror, _ = insert_session(cache, range(111, 115), "mirror")
    mirror_slots = _backup(cache, mirror)
    mirror_hold = cache.inc_lock_ref(mirror.id).to_dec_params()
    for start in (121, 131, 201):
        insert_session(cache, range(start, start + (32 if start == 201 else 4)), str(start))
    held = f.allocator.alloc(f.allocator.available_size() - 3)
    assert (f.allocator.available_size(), host.available_size()) == (3, 0)
    drops = []

    def record_eviction(node, _component, tokens):
        full = node.component_data[0]
        if full.value is None and full.session_ref > 0:
            drops.append(tokens)

    monkeypatch.setattr(tree, "_beliefkv_host_eviction_observer", record_eviction)
    req.origin_input_ids = array("q", range(1, 15))
    runtime = f.runtime
    runtime._agent_admission.latest = {f.key.invocation_id: f.key}
    runtime._native_chunk_input_reserve = 2
    runtime._native_page_size = 1
    runtime._prefetch_service_leases = {}
    scheduler, construction, invoke = native_scheduler(f, enabled=True)
    scheduler.chunked_prefill_size = 2
    scheduler.max_prefill_tokens = 4
    gate = SubagentMaterialization(runtime, include_root_entries=True)
    gate.materialized.add(gate._context(f.key))
    gate.attach(cache)
    assert gate._demand_restore_chunk is not None

    def choose(running, adder):
        gate.prepare(tuple(scheduler.waiting_queue), running_batch=running, adder=adder)
        return scheduler.waiting_queue

    scheduler._beliefkv_prefill_candidates = choose
    runtime.defer_prefill_for_prefetch = gate.defer_start

    # The test controller's start method returns zero for each ring position.
    controller = cache.cache_controller
    start = controller.start_loading
    monkeypatch.setattr(controller, "start_loading", lambda **kwargs: (
        controller.layer_done_counter.producer_index if start(**kwargs) >= 0 else -1
    ))
    if outcome == "control":
        monkeypatch.setattr(gate, "_stage_demand_restore_chunk", lambda *_args: False)
        f.complete.ready = True
        with pytest.raises(BatchConstructionReached):
            invoke()
        assert [len(load.host_indices) for load in f.loads] == [8]
        assert drops
        cache.loading_check(finish_count=1)
        cache.dec_lock_ref(req.last_node, req.lock_receipt)
        cache.dec_lock_ref(mirror.id, mirror_hold)
        f.allocator.free(held)
        host.free(held_host)
        cache.sanity_check()
        return

    assert invoke() == (None, f.running)
    assert not construction and scheduler.waiting_queue == [req]
    assert scheduler.chunked_req is None and req.kv.req_pool_idx is None
    assert runtime.counts["subagent_materialization_demand_restore_chunk_tokens"] == 4
    assert runtime.on_prefill_candidate_result.call_args.kwargs == {
        "admitted": False, "result": AddReqResult.OTHER.name,
    }
    assert [len(load.host_indices) for load in f.loads] == [4]
    pending = gate._demand_restore_chunk.pending
    prefix = tree.node_by_id(pending.prefix_node)
    prefix_data, suffix_data = prefix.component_data[0], f.leaf.component_data[0]
    assert (prefix_data.lock_ref, prefix_data.host_lock_ref) == (2, 1)
    assert suffix_data.host_lock_ref == 1
    assert (f.allocator.available_size(), host.available_size()) == (3, 0)
    assert mirror.component_data[0].host_value is None
    assert host.slot_used[mirror_slots].all()

    assert invoke() == (None, f.running)
    assert not construction and len(f.loads) == 1
    assert runtime.on_prefill_candidate_result.call_count == 1
    if outcome == "abort":
        gate.abort_demand_restore(NS(rid=req.rid, abort_all=False))
    elif outcome == "new_epoch":
        runtime._agent_admission.latest.clear()
        gate.prepare((), running_batch=f.running, adder=f.first)
    if outcome != "resume":
        assert gate._demand_restore_chunk.pending is None
        assert (prefix_data.lock_ref, prefix_data.host_lock_ref) == (1, 1)
        assert suffix_data.host_lock_ref == 0 and suffix_data.value is None

    f.complete.ready = True
    cache.loading_check(finish_count=1)
    if outcome == "resume":
        # The remaining native demand restores the entire promised suffix.
        # Its fixture ACK is now immediately observed, so it can build a batch.
        with pytest.raises(BatchConstructionReached):
            invoke()
        assert [len(load.host_indices) for load in f.loads] == [4, 4]
        assert construction == [(req,)]
        assert scheduler.chunked_req is req and not scheduler.waiting_queue
        assert req.extend_range.start == 8 and req.extend_range.length == 2
        assert prefix_data.host_value is None
        cache.loading_check(finish_count=1)
        cache.dec_lock_ref(req.last_node, req.lock_receipt)
    else:
        assert len(f.loads) == 1
    cache.dec_lock_ref(mirror.id, mirror_hold)
    f.allocator.free(held)
    host.free(held_host)
    assert gate._demand_restore_chunk.pending is None
    assert (prefix_data.lock_ref, prefix_data.host_lock_ref) == (0, 0)
    assert suffix_data.host_lock_ref == 0
    assert neighbor.component_data[0].host_value is neighbor_slots
    assert host.slot_used[neighbor_slots].all() and not drops
    cache.sanity_check()
