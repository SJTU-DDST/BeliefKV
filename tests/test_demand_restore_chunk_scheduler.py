"""D3 partial demand restore through the native scheduler and PrefillAdder."""

from array import array
from collections import Counter
from dataclasses import asdict
from types import SimpleNamespace as NS
from unittest.mock import Mock, call

import pytest

from beliefkv.runtime.demand_restore_capacity import ensure_device_capacity_preserving_live_kv
from beliefkv.runtime.sglang_v0520_admission import _request_key
from beliefkv.runtime.subagent_materialization import (
    SubagentMaterialization,
    observe_materialization_headroom,
)
from tests.test_demand_restore_capacity import _backup, bind_host_slots, constrained_cache
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


@pytest.mark.parametrize("blocker", (None, "session_ref", "prefetch_lease"))
def test_guarded_restore_can_use_unreferenced_host_leaf(constrained_cache, monkeypatch, blocker):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, host, _, _ = constrained_cache
    neighbor, _ = insert_session(cache, range(1, 5), "live-host")
    neighbor_slots = _backup(cache, neighbor, demote=True)
    cold, _ = insert_session(cache, range(11, 15), "cold-host")
    cold_slots = _backup(cache, cold, demote=True)
    if blocker != "session_ref":
        cache.release_radix_session("cold-host")
    protected = (cold.id,) if blocker == "prefetch_lease" else ()
    expected = blocker is None
    victim, _ = insert_session(cache, range(21, 25), "live-device")
    tree = cache.tree_core
    available = allocator.available_size()
    assert host.available_size() == 0
    assert observe_materialization_headroom(
        cache, protected_host_nodes=protected,
    ).reclaimable_host_tokens == 4 * expected

    losses = []

    def record_eviction(node, _component, tokens):
        if node.component_data[0].session_ref:
            losses.append(tokens)

    monkeypatch.setattr(tree, "_beliefkv_host_eviction_observer", record_eviction)
    runtime = NS(counts=Counter(), _native_chunk_input_reserve=4)
    gate = SubagentMaterialization(runtime, include_root_entries=True)
    gate.attach(cache)
    try:
        assert ensure_device_capacity_preserving_live_kv(
            cache, available + 4, protected_nodes=protected,
        ) is expected
        assert allocator.available_size() == available + 4 * expected
        assert neighbor.component_data[0].host_value is neighbor_slots
        assert host.slot_used[neighbor_slots].all() and not losses
        if expected:
            assert victim.component_data[0].value is None
            assert victim.component_data[0].host_value.tolist() == cold_slots.tolist()
            assert cold.id not in tree._node_arena
        else:
            assert victim.component_data[0].value is not None
            assert cold.component_data[0].host_value is cold_slots
        cache.sanity_check()
    finally:
        gate.close()


def _d3_scheduler(f, monkeypatch):
    runtime = f.runtime
    runtime._agent_admission.latest = {key.invocation_id: key for key in runtime.visible.values()}
    runtime._native_chunk_input_reserve = 2
    runtime._native_page_size = 1
    runtime._prefetch_service_leases = {}
    scheduler, construction, invoke = native_scheduler(f, enabled=True)
    scheduler.chunked_prefill_size = 2
    scheduler.max_prefill_tokens = 4
    gate = SubagentMaterialization(runtime, include_root_entries=True)
    gate.materialized.add(gate._context(f.key))
    gate.attach(f.cache)
    assert gate._demand_restore_chunk is not None

    def choose(running, adder):
        gate.prepare(tuple(scheduler.waiting_queue), running_batch=running, adder=adder)
        return scheduler.waiting_queue

    scheduler._beliefkv_prefill_candidates = choose
    runtime.defer_prefill_for_prefetch = gate.defer_start

    # The test controller's start method returns zero for each ring position.
    controller = f.cache.cache_controller
    start = controller.start_loading
    monkeypatch.setattr(controller, "start_loading", lambda **kwargs: (
        controller.layer_done_counter.producer_index if start(**kwargs) >= 0 else -1
    ))
    return gate, scheduler, construction, invoke


@pytest.mark.parametrize("staged_native", ({"members": 1, "select": False},), indirect=True)
@pytest.mark.parametrize("outcome", ("resume", "abort", "new_epoch", "control", "decline"))
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
    gate, scheduler, construction, invoke = _d3_scheduler(f, monkeypatch)
    if outcome in ("control", "decline"):
        if outcome == "control":
            monkeypatch.setattr(gate, "_stage_demand_restore_chunk", lambda *_args: False)
        else:
            monkeypatch.setattr(
                "beliefkv.runtime.demand_restore_chunk.ensure_device_capacity_preserving_live_kv",
                lambda *_args, **_kwargs: False,
            )
        f.complete.ready = True
        with pytest.raises(BatchConstructionReached):
            invoke()
        assert [len(load.host_indices) for load in f.loads] == [8]
        assert drops
        if outcome == "decline":
            declined, = (
                call.args[0] for call in runtime._opportunity_writer.record.call_args_list
                if call.args[0]["event"] == "subagent_materialization_demand_restore_chunk_declined"
            )
            assert declined["key"]["request_id"] == req.rid
            assert declined["reason"] == "device_capacity_not_funded"
            assert declined["proposed_tokens"] == 4
            assert gate._demand_restore_chunk.pending is None
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


@pytest.fixture
def pending_write_restore(staged_native, full_native, monkeypatch):
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.mem_cache.unified_cache.cache_action import BackupKV
    from sglang.srt.sampling.sampling_params import SamplingParams
    from test_beliefkv_terminal_capacity import insert_session

    f = staged_native
    cache, allocator, writes, _, _ = full_native
    controller = cache.cache_controller
    host = cache.host_pool_group.anchor_entry.host_pool
    source_slots = f.leaf.component_data[0].host_value
    held_host = host.alloc(host.size - 21)
    neighbor, _ = insert_session(cache, range(101, 105), "neighbor")
    neighbor_slots = _backup(cache, neighbor, demote=True)
    donor, _ = insert_session(cache, range(111, 115), "pending-donor")
    assert cache._execute_and_commit_kv_backup(
        BackupKV([donor.id]), write_back=False, no_host_reclaim=True,
    ) == 4
    write_ack, = controller.ack_write_queue
    assert write_ack.node_ids == [donor.id] and donor.write_through_pending_id == donor.id
    _, generation = insert_session(cache, range(121, 125), "resident")
    insert_session(cache, range(201, 233), "device-victim")
    held_device = allocator.alloc(allocator.available_size() - 3)
    # Four restored pages plus three reserve pages fit after the donor demotes.
    # Native fallback can use the five free Host slots to write another victim.
    assert (allocator.available_size(), host.available_size()) == (3, 5)
    f.req.origin_input_ids = array("q", range(1, 15))
    resident = Req(
        rid="resident-peer", origin_input_text="", origin_input_ids=array("q", range(121, 126)),
        sampling_params=SamplingParams(max_new_tokens=2), session_id="resident",
        beliefkv_metadata={
            "root_workflow_id": "peer", "invocation_id": "resident",
            "context_id": "resident", "context_epoch": 0,
        },
    )
    resident.session_generation = generation
    f.waiting.append(resident)
    visible = f.runtime.visible
    visible[resident.rid] = _request_key(resident)
    f.runtime._agent_admission.allows = lambda key: visible.get(key.request_id) == key

    query = Mock(return_value=False)
    monkeypatch.setattr(write_ack.finish_event, "query", query, raising=False)
    evict = Mock(wraps=cache.evict_for_alloc)
    native_load = Mock(wraps=cache.init_load_back)
    writing_check = Mock(wraps=cache.writing_check)
    monkeypatch.setattr(cache, "evict_for_alloc", evict)
    monkeypatch.setattr(cache, "init_load_back", native_load)
    monkeypatch.setattr(cache, "writing_check", writing_check)

    def synchronize_write():
        assert query.return_value or (native_load.called and evict.called)
        query.return_value = True

    write_ack.finish_event.synchronize.side_effect = synchronize_write
    gate, scheduler, construction, invoke = _d3_scheduler(f, monkeypatch)
    try:
        yield NS(
            f=f, gate=gate, scheduler=scheduler, construction=construction, invoke=invoke,
            resident=resident, host=host, writes=writes, source_slots=source_slots,
            neighbor=neighbor, neighbor_slots=neighbor_slots, donor=donor,
            write_ack=write_ack, query=query, evict=evict, native_load=native_load,
            writing_check=writing_check,
        )
    finally:
        gate.close()
        allocator.free(held_device)
        host.free(held_host)


def _restore_declines(runtime):
    return [
        row for event in runtime._opportunity_writer.record.call_args_list
        if (row := event.args[0])["event"] == "subagent_materialization_demand_restore_chunk_declined"
    ]


def _assert_write_ack_defers(case, key):
    f = case.f
    cache, req = f.cache, f.req
    controller = cache.cache_controller

    def nodes():
        return {
            node.id: (
                node.parent.id if node.parent is not None else None,
                id(data.value), id(data.host_value), data.lock_ref, data.host_lock_ref,
                node.write_through_pending_id, node.load_back_pending_id,
            )
            for node in cache.tree_core._node_arena.values() for data in (node.component_data[0],)
        }

    before = nodes()
    pending = dict(cache.ongoing_write_through)
    running = tuple(f.running.reqs)
    callbacks = f.runtime.on_prefill_candidate_result
    counts = case.query.call_count, callbacks.call_count, len(case.writes), len(f.commits)
    assert case.invoke() == (None, f.running)
    assert not case.construction and tuple(f.running.reqs) == running and not f.running.batch_is_full
    assert case.scheduler.waiting_queue == [req, case.resident]
    assert case.scheduler.chunked_req is None and req.kv.req_pool_idx is None
    assert not case.gate._adder.can_run_list
    assert f.pipeline.pending is None and case.gate._demand_restore_chunk.pending is None
    assert callbacks.call_count == counts[1] + 1
    assert callbacks.call_args == call(req, admitted=False, result="OTHER")
    decline = _restore_declines(f.runtime)[-1]
    assert decline["key"] == asdict(key)
    assert decline["reason"] == "write_ack_retry_next_pass" and decline["proposed_tokens"] == 4
    assert case.query.call_count == counts[0] + 1
    case.write_ack.finish_event.synchronize.assert_not_called()
    case.writing_check.assert_not_called()
    case.evict.assert_not_called()
    case.native_load.assert_not_called()
    assert controller.ack_write_queue == [case.write_ack] and cache.ongoing_write_through == pending
    assert not controller.write_queue and not controller.load_queue
    assert not f.loads and not controller.ack_load_queue and not cache.ongoing_load_back
    assert (len(case.writes), len(f.commits)) == counts[2:]
    assert (f.allocator.available_size(), case.host.available_size()) == (3, 5)
    assert nodes() == before
    cache.sanity_check()


def _assert_native_batch(case, members, restore_sizes):
    f = case.f
    cache = f.cache
    prior_batches = case.construction.copy()
    waiting = case.scheduler.waiting_queue.copy()
    f.complete.ready = True
    with pytest.raises(BatchConstructionReached):
        case.invoke()
    assert case.construction == [*prior_batches, members]
    assert case.scheduler.waiting_queue == [req for req in waiting if req not in members]
    assert case.scheduler.chunked_req is f.req and f.req.extend_range.start == 8
    assert all(req.kv.req_pool_idx is None and req.lock_receipt.node_id is not None for req in members)
    assert [len(load.host_indices) for load in f.loads] == restore_sizes
    assert f.loads[-1].host_indices.tolist() == case.source_slots[-restore_sizes[-1]:].tolist()
    assert case.gate._demand_restore_chunk.pending is None and f.pipeline.pending is None
    controller = cache.cache_controller
    ack, = controller.ack_load_queue
    assert ack.node_ids == [f.leaf.id] and ack.num_tokens_by_pool == {"kv": restore_sizes[-1]}
    assert not controller.ack_write_queue and not cache.ongoing_write_through
    case.write_ack.finish_event.synchronize.assert_called_once_with()
    cache.loading_check(finish_count=1)
    assert not controller.ack_load_queue and not cache.ongoing_load_back
    for req in members:
        cache.dec_lock_ref(req.last_node, req.lock_receipt)
    assert f.leaf.component_data[0].lock_ref == f.leaf.component_data[0].host_lock_ref == 0
    assert case.neighbor.component_data[0].host_value is case.neighbor_slots
    assert case.host.slot_used[case.neighbor_slots].all()
    cache.sanity_check()


@pytest.mark.parametrize("staged_native", ({"members": 1, "select": False},), indirect=True)
@pytest.mark.parametrize("next_pass", ("ready", "still-pending", "new-key"))
def test_d3_write_ack_retry_is_bounded_by_request_key(pending_write_restore, next_pass):
    case = pending_write_restore
    f = case.f
    _assert_write_ack_defers(case, f.key)
    if next_pass == "new-key":
        f.req.beliefkv_metadata["context_epoch"] += 1
        key = _request_key(f.req)
        assert key != f.key
        f.runtime.visible[f.req.rid] = key
        f.runtime._agent_admission.latest[key.invocation_id] = key
        case.gate.materialized.add(case.gate._context(key))
        _assert_write_ack_defers(case, key)
    if next_pass != "ready":
        _assert_native_batch(case, (f.req,), [8])
        assert case.query.call_count == 2 + int(next_pass == "new-key")
        assert case.writing_check.call_args_list[:1] == [call(write_back=True)]
        case.native_load.assert_called_once()
        assert _restore_declines(f.runtime)[-1]["reason"] == "device_capacity_not_funded"
        return

    case.query.return_value = True
    assert case.invoke() == (None, f.running)
    assert case.query.call_count == 2
    assert case.writing_check.call_args_list[:1] == [call(finish_count=1)]
    case.native_load.assert_not_called()
    assert not case.construction and case.scheduler.waiting_queue == [f.req, case.resident]
    assert case.scheduler.chunked_req is None
    pending = case.gate._demand_restore_chunk.pending
    assert pending.request is f.req and pending.key == f.key and pending.suffix_node == f.leaf.id
    controller = f.cache.cache_controller
    ack, = controller.ack_load_queue
    assert ack.node_ids == [pending.prefix_node] and ack.num_tokens_by_pool == {"kv": 4}
    assert not controller.ack_write_queue and not f.cache.ongoing_write_through
    assert case.donor.component_data[0].value is None and case.donor.write_through_pending_id is None
    commit = f.commits[-1]
    assert (commit.direction, commit.status, commit.node_ids) == ("d2h", "completed", (case.donor.id,))
    load, = f.loads
    assert load.host_indices.tolist() == case.source_slots[:4].tolist()
    prefix = f.cache.tree_core.node_by_id(pending.prefix_node).component_data[0]
    assert (prefix.lock_ref, prefix.host_lock_ref, f.leaf.component_data[0].host_lock_ref) == (2, 1, 1)

    with pytest.raises(BatchConstructionReached):
        case.invoke()
    assert case.construction == [(case.resident,)]
    assert case.scheduler.waiting_queue == [f.req] and case.scheduler.chunked_req is None
    assert f.loads == [load] and controller.ack_load_queue == [ack]
    assert case.gate._demand_restore_chunk.pending is pending and prefix.lock_ref == 2
    f.cache.dec_lock_ref(case.resident.last_node, case.resident.lock_receipt)

    f.complete.ready = True
    f.cache.loading_check(finish_count=1)
    _assert_native_batch(case, (f.req,), [4, 4])
    assert prefix.lock_ref == prefix.host_lock_ref == 0


@pytest.mark.parametrize("staged_native", ({"members": 1, "select": False},), indirect=True)
@pytest.mark.parametrize("guard", ("prefill_batch_already_constrained", "protected_service"))
def test_d3_write_ack_retry_preserves_native_guards(pending_write_restore, guard):
    case = pending_write_restore
    f = case.f
    if guard == "prefill_batch_already_constrained":
        case.scheduler.waiting_queue[:] = [case.resident, f.req]
        members = (case.resident, f.req)
    else:
        f.runtime._join_priority_promoted = f.req.rid
        members = (f.req,)
    _assert_native_batch(case, members, [8])
    case.query.assert_not_called()
    case.native_load.assert_called_once()
    decline, = _restore_declines(f.runtime)
    assert decline["reason"] == guard and decline["key"] == asdict(f.key)
