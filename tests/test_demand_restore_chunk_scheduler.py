"""D3 partial demand restore through the native scheduler and PrefillAdder."""

from array import array
from collections import Counter
from dataclasses import asdict, replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, call

import pytest

from beliefkv.runtime.demand_restore_capacity import ensure_device_capacity_preserving_live_kv
from beliefkv.runtime.demand_restore_chunk import DEFER_WRITE_ACK, DemandRestoreChunk
from beliefkv.runtime.ready_service_guard import ReadyServiceGuard
from beliefkv.runtime.sglang_v0520_admission import (
    _request_key,
    compile_native_prefill_plan,
    select_native_prefill_candidates,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.sglang_v0520_physical import PrefetchLoadStep
from beliefkv.runtime.subagent_materialization import (
    NODE_CENSUS_BOUND,
    MaterializationHeadroom,
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
def full_native(base_full_native, monkeypatch, request):
    cache, _, writes, _, _ = base_full_native
    unit = cache.host_pool_group.size_per_token
    spawn_reserve = (64 * 1024**2 + unit - 1) // unit
    bind_host_slots(cache, writes, spawn_reserve + getattr(request, "param", 16), monkeypatch)
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


def _add_resident_prefill(f, *, start=121):
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.sampling.sampling_params import SamplingParams
    from test_beliefkv_terminal_capacity import insert_session

    _, generation = insert_session(f.cache, range(start, start + 4), "resident")
    resident = Req(
        rid="resident-peer", origin_input_text="",
        origin_input_ids=array("q", range(start, start + 5)),
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
    return resident


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


@pytest.mark.parametrize(
    ("staged_native", "full_native", "base_full_native"),
    (({"members": 3, "select": False, "prefix_sizes": (16, 1, 24)}, 56, 96),),
    indirect=True,
)
@pytest.mark.parametrize("case", (
    "fit", "original_order", "native_refusal", "stale_session", "different_receipt",
    "join_priority", "eos_priority", "started_chunk",
))
def test_d3_peer_ack_retries_selected_waiter_before_fresh_restore(
    staged_native, monkeypatch, case,
):
    from test_beliefkv_terminal_capacity import insert_session

    f = staged_native
    cache, allocator, runtime = f.cache, f.allocator, f.runtime
    tree, controller = cache.tree_core, cache.cache_controller
    host = cache.host_pool_group.anchor_entry.host_pool
    producer, fresh, consumer = f.members
    producer_leaf, fresh_leaf, consumer_leaf = f.leaves
    key = runtime.visible[consumer.rid]
    suffix = consumer_leaf.component_data[0].host_value
    held_host = host.alloc(host.available_size() - 8)
    neighbor, _ = insert_session(cache, range(100, 108), "waiting-parent")
    neighbor_slots = _backup(cache, neighbor, demote=True)
    for start in range(200, 360, 10):
        insert_session(cache, range(start, start + 4), str(start))
    held_device = allocator.alloc(allocator.available_size() - 16)
    gate, scheduler, construction, invoke = _d3_scheduler(f, monkeypatch)
    gate.max_nodes = 1
    gate.materialized.add(gate._context(key))
    runtime._native_chunk_input_reserve = scheduler.chunked_prefill_size = 6
    scheduler.max_prefill_tokens = 32
    f.running.is_empty = lambda: False

    # Only the upstream order is supplied by the test. Apply the runtime's
    # retry and native selection before running the bound scheduler/adder.
    orders, losses = [], []
    runtime._subagent_materialization = gate
    runtime.semantic_revision = 0
    runtime._executable_waiting_key = lambda req, **kw: _request_key(req)
    runtime._join_priority_promoted = runtime._join_last_child_priority_promoted = None
    runtime._final_priority_promoted = runtime._prefetch_priority_promoted = None
    runtime._parent_handoff_priority_promoted = None
    runtime._join_priority_pending = {}
    runtime._last_join_child_has_waiting_parent = lambda _: False
    runtime._final_request_stages = {}
    runtime._live_final_stage = lambda _: True

    def normal_plan(order, *, running_batch, adder, _queue_inspection=None):
        gate.prepare(tuple(order), running_batch=running_batch, adder=adder)
        return compile_native_prefill_plan(
            [runtime.visible[req.rid] for req in order], semantic_revision=0,
        )

    runtime._plan_native_prefill = normal_plan

    def select(order, running, adder):
        plan = (
            normal_plan(order, running_batch=running, adder=adder)
            if case == "original_order" else
            NativeAdmissionRuntime.plan_native_prefill.__wrapped__(
                runtime, order, running_batch=running, adder=adder,
            )
        )
        candidates = select_native_prefill_candidates(
            order, plan=plan, current_semantic_revision=0,
        ).candidates
        orders.append(candidates)
        return candidates

    scheduler._beliefkv_prefill_candidates = lambda running, adder: select(
        scheduler.waiting_queue, running, adder,
    )

    def record_eviction(node, _component, tokens):
        data = node.component_data[0]
        if data.value is None and data.session_ref > 0:
            losses.append((node.id, tokens))

    monkeypatch.setattr(tree, "_beliefkv_host_eviction_observer", record_eviction)
    try:
        scheduler.waiting_queue[:] = [producer, consumer]
        assert invoke() == (None, f.running)
        assert not losses and not construction
        assert tuple(member.request for member in f.pipeline.pending.members) == (producer,)
        ack, = controller.ack_load_queue
        assert not ack.child_receipts and ack.node_ids == [producer_leaf.id]
        assert ack.num_tokens_by_pool == {"kv": 16}
        deferred, = (
            call.args[0] for call in runtime._opportunity_writer.record.call_args_list
            if call.args[0]["event"] == "subagent_materialization_restore_pressure_deferred"
        )
        assert deferred["key"] == asdict(key) and not deferred["already_submitted"]
        assert deferred["reclaimable_after_ack_tokens"] == 16 < deferred["unfunded_restore_tokens"]
        assert allocator.available_size() == host.available_size() == 0

        scheduler.waiting_queue[:] = [producer, fresh, consumer]
        assert invoke() == (None, f.running)
        assert orders == [(producer, consumer)] and not ack.finish_event.synchronize.called
        if case == "different_receipt":
            receipt = cache.ongoing_load_back[producer_leaf.id]
            cache.ongoing_load_back[producer_leaf.id] = type(receipt)(*receipt)
        f.complete.ready = True
        cache.loading_check()
        ack.finish_event.synchronize.assert_called_once_with()
        assert not controller.ack_load_queue
        assert not f.pipeline.defer(scheduler.waiting_queue, f.running)
        f.complete.ready = False

        if case in {"stale_session", "different_receipt", "join_priority", "eos_priority", "started_chunk"}:
            if case == "stale_session":
                consumer.session_generation += 1
            elif case == "join_priority":
                runtime._join_priority_pending[runtime.visible[producer.rid].invocation_id] = "join"
            elif case == "eos_priority":
                runtime._final_request_stages[producer.rid] = NS(semantic_only=False)
            adder = f.adder()
            if case == "started_chunk":
                producer.init_next_round_input()
                adder.add_chunked_req(producer)
                assert adder.can_run_list == [producer]
            expected = (producer, fresh) if case == "stale_session" else (producer, fresh, consumer)
            assert select(scheduler.waiting_queue, f.running, adder) == expected
            assert gate._peer_restore_retry is None
            assert len(f.loads) == 1 and not losses
            f.pipeline.cancel("test_complete")
            cache.sanity_check()
            return

        if case == "native_refusal":
            consumer.origin_input_ids.extend(range(400, 500))
            assert invoke() == (None, f.running)
            assert orders[-1] == (consumer, producer, fresh)
            assert not construction and not losses and len(f.loads) == 1
            runtime.on_prefill_candidate_result.assert_called_with(
                consumer, admitted=False, result="NO_TOKEN",
            )
            assert select(scheduler.waiting_queue, f.running, f.adder()) == (producer, fresh, consumer)
        elif case == "fit":
            assert invoke() == (None, f.running)
            assert orders[-1] == (consumer, producer, fresh)
            pending = gate._demand_restore_chunk.pending
            assert pending.request is consumer and pending.tokens == 9
            assert len(f.loads) == 2 and f.loads[-1].node_id != fresh_leaf.id
            assert f.loads[-1].host_indices.tolist() == suffix[:9].tolist()
            assert neighbor.component_data[0].host_value is neighbor_slots and not losses
            assert consumer.kv.req_pool_idx is None and not construction
            assert select(scheduler.waiting_queue, f.running, f.adder()) == (producer, fresh, consumer)
        else:
            assert invoke() == (None, f.running)
            assert orders[-1] == (producer, fresh, consumer)
            assert f.loads[1].node_id == fresh_leaf.id
            assert _restore_declines(runtime)[-1]["reason"] == "prefill_batch_already_constrained"
            assert any(node == neighbor.id for node, _ in losses)
        assert runtime.counts["subagent_materialization_peer_ack_retry_ordered"] == int(case != "original_order")
        if case != "original_order":
            assert gate._peer_restore_retry is None
        f.complete.ready = True
        cache.loading_check()
        f.pipeline.cancel("test_complete")
        cache.sanity_check()
    finally:
        gate.close()
        allocator.free(held_device)
        host.free(held_host)


@pytest.mark.parametrize(
    ("staged_native", "full_native"),
    (({"members": 1, "select": False, "prefix_sizes": (18,)}, 18),),
    indirect=True,
)
def test_d3_recycles_acked_slices_until_native_suffix_is_funded(staged_native, monkeypatch):
    _run_repeated_restore(staged_native, monkeypatch, selected_write_ack=False)


@pytest.mark.parametrize(
    ("staged_native", "full_native"),
    (({"members": 1, "select": False, "prefix_sizes": (19,)}, 19),),
    indirect=True,
)
def test_d3_suffix_write_ack_retry_preserves_selected_resident_prefill(staged_native, monkeypatch):
    _run_repeated_restore(staged_native, monkeypatch, selected_write_ack=True)


def _run_repeated_restore(staged_native, monkeypatch, *, selected_write_ack):
    from sglang.srt.mem_cache.unified_cache.cache_action import BackupKV
    from test_beliefkv_terminal_capacity import insert_session

    f = staged_native
    cache, req, allocator = f.cache, f.req, f.allocator
    tree, controller = cache.tree_core, cache.cache_controller
    host = cache.host_pool_group.anchor_entry.host_pool
    source_slots = f.leaf.component_data[0].host_value.tolist()
    held_host = host.alloc(host.available_size() - 14)
    neighbor, _ = insert_session(cache, range(101, 109), "neighbor")
    neighbor_slots = _backup(cache, neighbor, demote=True)
    for start, size in ((111, 6), (121, 6), (131, 4), (141, 4), (201, 32)):
        insert_session(cache, range(start, start + size), str(start))
    if selected_write_ack:
        resident = _add_resident_prefill(f, start=161)
        f.waiting.remove(resident)
        donor, _ = insert_session(cache, (151,), "write-donor")
    held_device = allocator.alloc(allocator.available_size() - 3)
    assert (allocator.available_size(), host.available_size()) == (3, 6)
    req.origin_input_ids = array("q", range(1, 25))
    losses = []

    def record_eviction(node, _component, tokens):
        full = node.component_data[0]
        if full.value is None and full.session_ref > 0:
            losses.append(tokens)

    monkeypatch.setattr(tree, "_beliefkv_host_eviction_observer", record_eviction)
    native_load = Mock(wraps=cache.init_load_back)
    monkeypatch.setattr(cache, "init_load_back", native_load)
    gate, scheduler, construction, invoke = _d3_scheduler(f, monkeypatch)
    decode_slots = None
    prefix_nodes = []
    expected_batches = []
    staged_sizes = (6, 4, 5) if selected_write_ack else (6, 4, 4)
    try:
        offset = 0
        for tokens in staged_sizes:
            f.complete.ready = False
            assert invoke() == (None, f.running)
            assert not losses, f"restore evicted {sum(losses)} live Host-only tokens"
            assert construction == expected_batches and scheduler.waiting_queue == [req]
            assert scheduler.chunked_req is None and req.kv.req_pool_idx is None
            assert f.pipeline.pending is None
            native_load.assert_not_called()
            pending = gate._demand_restore_chunk.pending
            assert pending.request is req and pending.tokens == tokens
            assert pending.key == f.key and pending.suffix_node == f.leaf.id
            prefix_nodes.append(tree.node_by_id(pending.prefix_node))
            assert f.loads[-1].host_indices.tolist() == source_slots[offset:offset + tokens]
            ack, = controller.ack_load_queue
            assert ack.node_ids == [pending.prefix_node] and ack.num_tokens_by_pool == {"kv": tokens}
            assert (allocator.available_size(), host.available_size()) == (3, 0)
            if selected_write_ack and offset == 6:
                write_ack.finish_event.synchronize.assert_called_once_with()
                assert not controller.ack_write_queue and not cache.ongoing_write_through
                assert donor.write_through_pending_id is None
            if offset == 0:
                # Decode consumes two reserve slots before the first H2D ACK.
                decode_slots = allocator.alloc(2)
                assert allocator.available_size() == 1

            assert invoke() == (None, f.running)
            assert len(f.loads) == len(prefix_nodes) and controller.ack_load_queue == [ack]
            assert gate._demand_restore_chunk.pending is pending
            ack.finish_event.synchronize.assert_not_called()
            f.complete.ready = True
            cache.loading_check()
            ack.finish_event.synchronize.assert_called_once_with()
            assert not controller.ack_load_queue and not cache.ongoing_load_back
            offset += tokens
            if selected_write_ack and offset == 6:
                host.free(held_host[-1:])
                held_host = held_host[:-1]
                assert cache._execute_and_commit_kv_backup(
                    BackupKV([donor.id]), write_back=False, no_host_reclaim=True,
                ) == 1
                write_ack, = controller.ack_write_queue
                assert write_ack.node_ids == [donor.id] and not controller.write_queue
                query_write = Mock(return_value=False)
                monkeypatch.setattr(write_ack.finish_event, "query", query_write, raising=False)

                scheduler.waiting_queue.insert(0, resident)
                with pytest.raises(BatchConstructionReached):
                    invoke()
                assert not losses, f"suffix retry evicted {sum(losses)} live Host-only tokens"
                expected_batches.append((resident,))
                assert construction == expected_batches and scheduler.waiting_queue == [req]
                assert resident.extend_range.length == 1 and resident.host_hit_length == 0
                assert req.host_hit_length == len(source_slots) - offset
                assert scheduler.chunked_req is None
                assert gate._demand_restore_chunk.pending is None and f.pipeline.pending is None
                assert len(f.loads) == 1 and not controller.ack_load_queue
                assert controller.ack_write_queue == [write_ack]
                assert donor.write_through_pending_id == donor.id
                native_load.assert_not_called()
                query_write.assert_called_once_with()
                write_ack.finish_event.synchronize.assert_not_called()
                decline, = _restore_declines(f.runtime)
                assert decline["key"] == asdict(f.key)
                assert decline["reason"] == "write_ack_retry_next_pass"
                assert decline["queued_write_acks_at_trial"] == 1
                assert f.runtime.counts["subagent_materialization_demand_restore_ack_retries"] == 1
                cache.dec_lock_ref(resident.last_node, resident.lock_receipt)
                query_write.return_value = True

        with pytest.raises(BatchConstructionReached):
            invoke()
        native_load.assert_called_once()
        assert native_load.call_args.args[0].host_hit_length == 4
        assert construction == [*expected_batches, (req,)] and not scheduler.waiting_queue
        assert scheduler.chunked_req is req and req.extend_range.start == len(source_slots)
        assert [len(load.host_indices) for load in f.loads] == [*staged_sizes, 4]
        assert f.loads[-1].host_indices.tolist() == source_slots[offset:]
        assert allocator.available_size() == 3
        assert f.runtime.counts["subagent_materialization_demand_restore_chunk_tokens"] == offset
        assert f.runtime.counts["subagent_materialization_demand_restore_suffix_funded"] == 1
        cache.loading_check()
        cache.dec_lock_ref(req.last_node, req.lock_receipt)
        assert gate._demand_restore_chunk.pending is None and f.pipeline.pending is None
        assert not controller.ack_load_queue and not cache.ongoing_load_back
        for node in (*prefix_nodes, f.leaf):
            full = node.component_data[0]
            assert (full.lock_ref, full.host_lock_ref) == (0, 0)
        assert neighbor.component_data[0].host_value is neighbor_slots
        assert host.slot_used[neighbor_slots].all() and not losses
        cache.sanity_check()
    finally:
        gate.close()
        if decode_slots is not None:
            allocator.free(decode_slots)
        allocator.free(held_device)
        host.free(held_host)


@pytest.fixture
def resident_restore_pressure(staged_native, full_native, monkeypatch, request):
    from sglang.srt.mem_cache.unified_cache.cache_action import BackupKV
    from test_beliefkv_terminal_capacity import insert_session

    f = staged_native
    cache, allocator, writes, _, _ = full_native
    controller = cache.cache_controller
    host = cache.host_pool_group.anchor_entry.host_pool
    source_slots = f.leaf.component_data[0].host_value
    mode = getattr(request, "param", None)
    idle = mode == "idle"
    host_slots = {"idle": 19, "two-write-acks": 22}.get(mode, 21)
    held_host = host.alloc(host.size - host_slots)
    neighbor, _ = insert_session(cache, range(101, 105), "neighbor")
    neighbor_slots = _backup(cache, neighbor, demote=True)
    donor, _ = insert_session(cache, range(111, 118 if idle else 115), "donor")
    write_ack = None
    if not idle:
        assert cache._execute_and_commit_kv_backup(
            BackupKV([donor.id]), write_back=False, no_host_reclaim=True,
        ) == 4
        write_ack, = controller.ack_write_queue
        assert write_ack.node_ids == [donor.id] and donor.write_through_pending_id == donor.id
    if mode == "two-write-acks":
        second_donor, _ = insert_session(cache, (131,), "second-write-donor")
        assert cache._execute_and_commit_kv_backup(
            BackupKV([second_donor.id]), write_back=False, no_host_reclaim=True,
        ) == 1
        first, second = controller.ack_write_queue
        assert first is write_ack and second.node_ids == [second_donor.id]
    resident = _add_resident_prefill(f)
    insert_session(cache, range(201, 233), "device-victim")
    held_device = allocator.alloc(allocator.available_size() - (0 if idle else 3))
    # Both paths can fund four restored pages plus three reserve pages.
    # The idle path needs a new backup of the seven-page donor.
    assert (allocator.available_size(), host.available_size()) == ((0, 7) if idle else (3, 5))
    f.req.origin_input_ids = array("q", range(1, 15))

    evict = Mock(wraps=cache.evict_for_alloc)
    native_load = Mock(wraps=cache.init_load_back)
    writing_check = Mock(wraps=cache.writing_check)
    monkeypatch.setattr(cache, "evict_for_alloc", evict)
    monkeypatch.setattr(cache, "init_load_back", native_load)
    monkeypatch.setattr(cache, "writing_check", writing_check)

    query = None
    if write_ack is not None:
        query = Mock(return_value=False)
        monkeypatch.setattr(write_ack.finish_event, "query", query, raising=False)

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


@pytest.mark.parametrize("staged_native", ({"members": 1, "select": False},), indirect=True)
@pytest.mark.parametrize(
    ("free_before", "reclaimed", "threshold", "staged_tokens"),
    (
        pytest.param(3, 2, 1, 2, id="shortened"),
        pytest.param(5, 0, 1, 2, id="no-progress"),
        pytest.param(3, 1, 1, 0, id="below-chunk"),
        pytest.param(3, 2, 3, 0, id="below-load-threshold"),
    ),
)
def test_d3_uses_partial_capacity_cleanup(
    staged_native, monkeypatch, free_before, reclaimed, threshold, staged_tokens,
):
    from test_beliefkv_terminal_capacity import insert_session

    f = staged_native
    cache, req, allocator, runtime = f.cache, f.req, f.allocator, f.runtime
    tree, controller = cache.tree_core, cache.cache_controller
    host = cache.host_pool_group.anchor_entry.host_pool
    source_slots = f.leaf.component_data[0].host_value.tolist()
    # Four tokens would end in the leaf, but a two-token load ends inside its parent.
    parent_id, actions = tree.split_host_only_full_prefix(f.leaf.id, f.leaf.creation_time, 3)
    cache._apply_cache_actions(actions)
    parent = tree.node_by_id(parent_id)
    assert f.leaf.parent is parent

    # Leave enough combined headroom for four restore and three reserve slots.
    host_free = 7 - free_before
    held_host = host.alloc(host.available_size() - 4 - host_free)
    neighbor, _ = insert_session(cache, range(101, 105), "neighbor")
    neighbor_slots = _backup(cache, neighbor, demote=True)
    neighbor_data = neighbor.component_data[0]
    assert neighbor_data.session_ref > 0 and neighbor_data.value is None
    if reclaimed:
        insert_session(cache, range(111, 111 + reclaimed), "partial-donor")
    insert_session(cache, range(201, 233), "device-victim")
    held_device = allocator.alloc(allocator.available_size() - free_before)
    assert (allocator.available_size(), host.available_size()) == (free_before, host_free)
    cache.load_back_threshold = threshold
    req.origin_input_ids = array("q", range(1, 15))

    def partial_cleanup(native, target, *, minimum_tokens=None, **kwargs):
        assert native is cache
        if target != 7:
            assert not reclaimed and target == free_before == allocator.available_size()
            assert minimum_tokens is None
            return ensure_device_capacity_preserving_live_kv(native, target, **kwargs)
        assert minimum_tokens == 3 + max(2, threshold)
        assert allocator.available_size() == free_before
        if reclaimed:
            assert ensure_device_capacity_preserving_live_kv(native, free_before + reclaimed, **kwargs)
        assert allocator.available_size() == free_before + reclaimed
        assert neighbor_data.host_value is neighbor_slots and host.slot_used[neighbor_slots].all()
        return False

    funding = Mock(side_effect=partial_cleanup)
    monkeypatch.setattr(
        "beliefkv.runtime.demand_restore_chunk.ensure_device_capacity_preserving_live_kv", funding,
    )
    native_load = Mock(wraps=cache.init_load_back)
    monkeypatch.setattr(cache, "init_load_back", native_load)
    gate, scheduler, construction, invoke = _d3_scheduler(f, monkeypatch)
    funding_calls = 2 if staged_tokens and not reclaimed else 1
    try:
        if staged_tokens:
            assert invoke() == (None, f.running)
            assert not construction and scheduler.waiting_queue == [req]
            assert scheduler.chunked_req is None and req.kv.req_pool_idx is None
            native_load.assert_not_called()
            pending = gate._demand_restore_chunk.pending
            assert pending.request is req and pending.tokens == staged_tokens
            prefix = tree.node_by_id(pending.prefix_node)
            assert tuple(prefix.key) == (1, 2) and tuple(parent.key) == (3,)
            assert parent.parent is prefix and f.leaf.parent is parent
            assert f.leaf.component_data[0].value is None
            load, = f.loads
            ack, = controller.ack_load_queue
            assert load.host_indices.tolist() == source_slots[:staged_tokens]
            assert ack.node_ids == [prefix.id] and ack.num_tokens_by_pool == {"kv": staged_tokens}
            assert allocator.available_size() == 3
            assert neighbor_data.host_value is neighbor_slots and host.slot_used[neighbor_slots].all()
            stage, = (
                event.args[0] for event in runtime._opportunity_writer.record.call_args_list
                if event.args[0]["event"] == "subagent_materialization_demand_restore_chunk"
            )
            assert stage["key"] == asdict(f.key) and stage["prefix_node_id"] == prefix.id
            assert (stage["proposed_tokens"], stage["staged_tokens"]) == (4, staged_tokens)
            assert not _restore_declines(runtime)

            cache.loading_check()
            assert invoke() == (None, f.running)
            assert f.loads == [load] and controller.ack_load_queue == [ack]
            ack.finish_event.synchronize.assert_not_called()
            f.complete.ready = True
            cache.loading_check()
            ack.finish_event.synchronize.assert_called_once_with()
            assert not controller.ack_load_queue and not cache.ongoing_load_back
            assert funding.call_count == funding_calls
            monkeypatch.setattr(
                "beliefkv.runtime.demand_restore_chunk.ensure_device_capacity_preserving_live_kv",
                ensure_device_capacity_preserving_live_kv,
            )
            assert invoke() == (None, f.running)
            assert not construction and scheduler.waiting_queue == [req]
            cache.loading_check()
        else:
            f.complete.ready = True

        with pytest.raises(BatchConstructionReached):
            invoke()
        assert funding.call_count == funding_calls
        native_load.assert_called_once()
        assert native_load.call_args.args[0].host_hit_length == (2 if staged_tokens else 8)
        assert construction == [(req,)] and not scheduler.waiting_queue
        assert scheduler.chunked_req is req and (req.extend_range.start, req.extend_range.length) == (8, 2)
        assert gate._demand_restore_chunk.pending is None and f.pipeline.pending is None
        assert runtime.counts["subagent_materialization_demand_restore_chunks"] == (2 if staged_tokens else 0)
        assert runtime.counts["subagent_materialization_demand_restore_chunk_tokens"] == (6 if staged_tokens else 0)
        pieces = [load.host_indices.tolist() for load in f.loads]
        assert [len(piece) for piece in pieces] == ([2, 4, 2] if staged_tokens else [8])
        assert [slot for piece in pieces for slot in piece] == source_slots
        cache.loading_check()
        cache.dec_lock_ref(req.last_node, req.lock_receipt)
        assert not controller.ack_load_queue and not cache.ongoing_load_back
        if staged_tokens:
            assert neighbor_data.value is None and neighbor_data.host_value is neighbor_slots
            assert host.slot_used[neighbor_slots].all()
        else:
            assert runtime.counts["subagent_materialization_demand_restore_chunk_trials"] == 1
            decline, = _restore_declines(runtime)
            assert decline["reason"] == "device_capacity_not_funded" and decline["proposed_tokens"] == 4
        cache.sanity_check()
    finally:
        gate.close()
        allocator.free(held_device)
        host.free(held_host)


@pytest.mark.parametrize("staged_native", ({"members": 1, "select": False},), indirect=True)
@pytest.mark.parametrize("case", ("raw-slots", "shortage", "unsupported", "pending-write"))
def test_d3_validates_zero_gain_slice_with_native_capacity(staged_native, monkeypatch, case):
    from sglang.srt.mem_cache.base_prefix_cache import InitLoadBackParams
    from sglang.srt.mem_cache.unified_cache.cache_action import BackupKV
    from test_beliefkv_terminal_capacity import insert_session

    f = staged_native
    cache, allocator, req = f.cache, f.allocator, f.req
    tree, controller = cache.tree_core, cache.cache_controller
    host = cache.host_pool_group.anchor_entry.host_pool
    suffix = f.leaf.component_data[0]
    source_slots = suffix.host_value
    neighbor, _ = insert_session(cache, range(101, 105), "neighbor")
    neighbor_slots = _backup(cache, neighbor, demote=True)
    write_ack = None
    if case == "pending-write":
        donor, _ = insert_session(cache, range(111, 115), "pending-backup")
        assert cache._execute_and_commit_kv_backup(
            BackupKV([donor.id]), write_back=False, no_host_reclaim=True,
        ) == 4
        write_ack, = controller.ack_write_queue
        monkeypatch.setattr(write_ack.finish_event, "query", Mock(return_value=False), raising=False)
    reserve, minimum, proposed = 3, 2, 4
    free = reserve + minimum - int(case == "shortage")
    held = allocator.alloc(allocator.available_size() - free)
    assert held is not None and allocator.available_size() == free
    if write_ack is None:
        assert cache.full_evictable_size() == 0
    req.init_next_round_input(cache)
    params = InitLoadBackParams(
        best_match_node=req.best_match_node, host_hit_length=req.host_hit_length, req=req,
    )
    assert params.best_match_node == f.leaf.id and params.host_hit_length == len(source_slots)
    node_ids, commit_count = tuple(tree._node_arena), len(f.commits)
    host_free = host.available_size()
    evict = Mock(wraps=cache.evict_for_alloc)
    write = Mock(wraps=controller.write)
    funding = Mock(wraps=ensure_device_capacity_preserving_live_kv)
    monkeypatch.setattr(cache, "evict_for_alloc", evict)
    monkeypatch.setattr(controller, "write", write)
    monkeypatch.setattr(
        "beliefkv.runtime.demand_restore_chunk.ensure_device_capacity_preserving_live_kv", funding,
    )
    restore = DemandRestoreChunk(cache, max_source_nodes=8)
    options = dict(
        device_reserve=reserve, protected_nodes=(f.leaf.id,), minimum_tokens=minimum,
    )

    try:
        with monkeypatch.context() as geometry:
            if case == "unsupported":
                geometry.setattr(cache, "pp_size", 2)
            result = restore.start(params, f.key, proposed, **options)
        if write_ack is not None:
            assert result == DEFER_WRITE_ACK and restore.pending is None
            assert allocator.available_size() == free and not f.loads
            result = restore.start(params, f.key, proposed, **options)
            assert controller.ack_write_queue == [write_ack]
            write_ack.finish_event.query.assert_has_calls([call(), call()])
            write_ack.finish_event.synchronize.assert_not_called()

        evict.assert_not_called()
        write.assert_not_called()
        assert len(f.commits) == commit_count and host.available_size() == host_free
        assert neighbor.component_data[0].host_value is neighbor_slots
        assert host.slot_used[neighbor_slots].all() and host.slot_used[source_slots].all()
        targets = [entry.args[1] for entry in funding.call_args_list]
        if case != "raw-slots":
            assert result == "device_capacity_not_funded" and restore.pending is None
            assert targets == {
                "shortage": [proposed + reserve],
                "unsupported": [proposed + reserve, free],
                "pending-write": [proposed + reserve, proposed + reserve],
            }[case]
            assert allocator.available_size() == free and tuple(tree._node_arena) == node_ids
            assert suffix.host_value is source_slots and suffix.value is None
            assert (suffix.lock_ref, suffix.host_lock_ref) == (0, 0)
            assert not f.loads and not controller.ack_load_queue and not cache.ongoing_load_back
            return

        assert result is None
        assert targets == [proposed + reserve, minimum + reserve]
        pending = restore.pending
        assert (pending.tokens, pending.remaining_tokens) == (minimum, params.host_hit_length - minimum)
        prefix = tree.node_by_id(pending.prefix_node)
        full = prefix.component_data[0]
        load, = f.loads
        ack, = controller.ack_load_queue
        assert f.leaf.parent is prefix and pending.suffix_node == f.leaf.id
        assert load.node_id == prefix.id and load.host_indices.tolist() == source_slots[:minimum].tolist()
        assert suffix.host_value.tolist() == source_slots[minimum:].tolist()
        assert allocator.available_size() == reserve
        assert ack.node_ids == [prefix.id] and ack.num_tokens_by_pool == {"kv": minimum}
        assert (full.lock_ref, full.host_lock_ref, suffix.host_lock_ref) == (2, 1, 1)

        f.complete.ready = True
        assert restore.defer_until_ack(req)
        ack.finish_event.synchronize.assert_not_called()
        cache.loading_check(finish_count=1)
        ack.finish_event.synchronize.assert_called_once_with()
        assert (full.lock_ref, full.host_lock_ref, suffix.host_lock_ref) == (1, 0, 1)
        assert not restore.defer_until_ack(req)
        assert restore.pending is None and not controller.ack_load_queue and not cache.ongoing_load_back
        assert (full.lock_ref, full.host_lock_ref, suffix.host_lock_ref) == (0, 0, 0)
        commit, = f.commits[commit_count:]
        assert commit.direction == "h2d" and commit.node_ids == (prefix.id,)
    finally:
        if controller.ack_load_queue:
            cache.loading_check(finish_count=1)
        restore.release()
        if write_ack is not None:
            cache.writing_check(finish_count=1)
        allocator.free(held)
        cache.sanity_check()


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
def test_d3_write_ack_retry_is_bounded_by_request_key(resident_restore_pressure, next_pass):
    case = resident_restore_pressure
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
@pytest.mark.parametrize(
    ("guard", "resident_restore_pressure"),
    (
        pytest.param(
            "prefill_batch_already_constrained", "two-write-acks", id="prefill_batch_already_constrained",
        ),
        pytest.param("protected_service", "write-ack", id="protected_service"),
    ),
    indirect=("resident_restore_pressure",),
)
def test_d3_write_ack_retry_preserves_native_guards(resident_restore_pressure, guard):
    case = resident_restore_pressure
    f = case.f
    if guard == "prefill_batch_already_constrained":
        case.scheduler.waiting_queue[:] = [case.resident, f.req]
        members = (case.resident, f.req)
    else:
        f.runtime._join_priority_promoted = f.req.rid
        members = (f.req,)
    write_acks = len(f.cache.cache_controller.ack_write_queue)
    _assert_native_batch(case, members, [8])
    case.query.assert_not_called()
    case.native_load.assert_called_once()
    decline, = _restore_declines(f.runtime)
    assert decline["reason"] == guard and decline["key"] == asdict(f.key)
    assert decline["queued_write_acks_at_trial"] == write_acks


@pytest.mark.parametrize("staged_native", ({"members": 1, "select": False},), indirect=True)
@pytest.mark.parametrize("resident_restore_pressure", ("idle",), indirect=True)
def test_d3_stages_promoted_join_parent_without_pending_write(resident_restore_pressure, monkeypatch):
    case = resident_restore_pressure
    f = case.f
    cache, req = f.cache, f.req
    controller = cache.cache_controller
    f.runtime._join_priority_promoted = req.rid
    case.scheduler.waiting_queue.remove(case.resident)
    assert not controller.write_queue and not controller.ack_write_queue
    assert not cache.ongoing_write_through and not controller.ack_load_queue
    assert f.allocator.available_size() == 0 and len(case.source_slots) == 8
    assert ensure_device_capacity_preserving_live_kv(
        cache, 8, protected_nodes=(f.leaf.id,),
    ) is False
    neighbor = case.neighbor.component_data[0]
    assert neighbor.value is None and neighbor.session_ref > 0
    losses = []

    def record_eviction(node, _component, tokens):
        full = node.component_data[0]
        if full.value is None and full.session_ref > 0:
            losses.append((node.id, tokens))

    monkeypatch.setattr(cache.tree_core, "_beliefkv_host_eviction_observer", record_eviction)
    assert case.invoke() == (None, f.running)
    assert not losses, f"promoted JOIN restore evicted referenced Host-only KV: {losses}"
    case.native_load.assert_not_called()
    assert not case.construction and case.scheduler.waiting_queue == [req]
    pending = case.gate._demand_restore_chunk.pending
    assert pending.request is req and pending.key == f.key and pending.tokens == 4
    ack, = controller.ack_load_queue
    assert ack.node_ids == [pending.prefix_node] and ack.num_tokens_by_pool == {"kv": 4}

    assert case.invoke() == (None, f.running)
    assert controller.ack_load_queue == [ack]
    ack.finish_event.synchronize.assert_not_called()
    f.complete.ready = True
    cache.loading_check()

    case.scheduler.waiting_queue.append(case.resident)
    with pytest.raises(BatchConstructionReached):
        case.invoke()
    assert case.construction == [(req,)] and case.scheduler.waiting_queue == [case.resident]
    assert case.scheduler.chunked_req is req and req.extend_range.start == 8
    assert [len(load.host_indices) for load in f.loads] == [4, 4]
    assert [slot for load in f.loads for slot in load.host_indices.tolist()] == case.source_slots.tolist()
    assert neighbor.host_value is case.neighbor_slots and case.host.slot_used[case.neighbor_slots].all()
    assert not losses and not _restore_declines(f.runtime)
    assert case.gate._demand_restore_chunk.pending is None and f.pipeline.pending is None
    cache.loading_check()
    cache.dec_lock_ref(req.last_node, req.lock_receipt)
    cache.sanity_check()


@pytest.mark.parametrize("staged_native", ({"members": 1, "select": False},), indirect=True)
@pytest.mark.parametrize("resident_restore_pressure", ("idle",), indirect=True)
@pytest.mark.parametrize("blocker", ("no_safe_slice", "final", "last_child", "timeout"))
def test_d3_promoted_join_parent_preserves_native_fallback(resident_restore_pressure, blocker):
    case = resident_restore_pressure
    f = case.f
    req = f.req
    f.runtime._join_priority_promoted = req.rid
    case.scheduler.waiting_queue.remove(case.resident)
    held_host = None
    if blocker == "no_safe_slice":
        held_host = case.host.alloc(3)
    elif blocker == "final":
        f.runtime._final_priority_promoted = req.rid
    elif blocker == "last_child":
        f.runtime._join_last_child_priority_promoted = req.rid
    else:
        guard = ReadyServiceGuard(f.runtime._agent_admission)
        guard.since[f.key.invocation_id] = f.key, -guard.timeout_s
        f.runtime._agent_admission._ready_service_guard = guard
    try:
        f.complete.ready = True
        with pytest.raises(BatchConstructionReached):
            case.invoke()
        assert case.construction == [(req,)] and case.scheduler.chunked_req is req
        assert case.gate._demand_restore_chunk.pending is None
        case.native_load.assert_called_once()
        assert [len(load.host_indices) for load in f.loads] == [8]
        decline, = _restore_declines(f.runtime)
        assert decline["reason"] == ("no_safe_slice" if held_host is not None else "protected_service")
        assert decline["queued_write_acks_at_trial"] == 0
        assert f.runtime.counts["subagent_materialization_demand_restore_ack_retries"] == 0
        f.cache.loading_check()
        f.cache.dec_lock_ref(req.last_node, req.lock_receipt)
        f.cache.sanity_check()
    finally:
        if held_host is not None:
            case.host.free(held_host)


@pytest.mark.parametrize("staged_native", ({"members": 1, "select": False},), indirect=True)
@pytest.mark.parametrize("resident_restore_pressure", ("idle", "first-slice-write-ack"), indirect=True)
def test_d3_stages_with_a_selected_resident_prefill(resident_restore_pressure, monkeypatch):
    case = resident_restore_pressure
    f = case.f
    cache, req, resident = f.cache, f.req, case.resident
    tree, controller = cache.tree_core, cache.cache_controller
    write_ack = case.write_ack
    neighbor = case.neighbor.component_data[0]
    assert neighbor.session_ref > 0 and neighbor.value is None
    assert not controller.write_queue
    assert controller.ack_write_queue == ([write_ack] if write_ack is not None else [])
    assert len(cache.ongoing_write_through) == len(controller.ack_write_queue)
    assert not controller.load_queue and not controller.ack_load_queue
    assert not f.loads and not cache.ongoing_load_back
    drops = []

    def record_eviction(node, _component, tokens):
        full = node.component_data[0]
        if full.value is None and full.session_ref > 0:
            drops.append(tokens)

    monkeypatch.setattr(tree, "_beliefkv_host_eviction_observer", record_eviction)
    case.scheduler.waiting_queue[:] = [resident, req]

    with pytest.raises(BatchConstructionReached):
        case.invoke()
    if write_ack is not None:
        decline, = _restore_declines(f.runtime)
        assert decline["reason"] == "write_ack_retry_next_pass"
        assert decline["key"] == asdict(f.key) and decline["queued_write_acks_at_trial"] == 1
        assert f.runtime.counts["subagent_materialization_demand_restore_ack_retries"] == 1
    else:
        assert not _restore_declines(f.runtime)
    assert case.construction == [(resident,)]
    assert case.scheduler.waiting_queue == [req] and case.scheduler.chunked_req is None
    assert resident.extend_range.length == 1 and resident.host_hit_length == 0
    assert req.host_hit_length == 8 and req.lock_receipt.node_id is None
    assert req.kv.req_pool_idx is None and f.pipeline.pending is None
    case.native_load.assert_not_called()
    if write_ack is not None:
        assert case.gate._demand_restore_chunk.pending is None
        assert not f.loads and not controller.ack_load_queue
        assert controller.ack_write_queue == [write_ack]
        assert not drops and neighbor.host_value is case.neighbor_slots
        case.query.assert_called_once_with()
        write_ack.finish_event.synchronize.assert_not_called()
        case.writing_check.assert_not_called()

        case.query.return_value = True
        assert case.invoke() == (None, f.running)
        assert case.construction == [(resident,)] and case.scheduler.waiting_queue == [req]
        assert not controller.ack_write_queue and not cache.ongoing_write_through
        assert case.writing_check.call_args_list[:1] == [call(finish_count=1)]
        write_ack.finish_event.synchronize.assert_called_once_with()
        case.native_load.assert_not_called()
    pending = case.gate._demand_restore_chunk.pending
    assert pending.request is req and pending.key == f.key and pending.suffix_node == f.leaf.id
    load, = f.loads
    ack, = controller.ack_load_queue
    assert ack.node_ids == [pending.prefix_node] and ack.num_tokens_by_pool == {"kv": 4}
    assert load.host_indices.tolist() == case.source_slots[:4].tolist()
    prefix = tree.node_by_id(pending.prefix_node).component_data[0]
    suffix = f.leaf.component_data[0]
    resident_data = tree.node_by_id(resident.last_node).component_data[0]
    assert resident_data.lock_ref == 1 and resident_data.value is not None
    assert (prefix.lock_ref, prefix.host_lock_ref) == (2, 1)
    assert suffix.value is None and suffix.host_lock_ref == 1
    assert (f.allocator.available_size(), case.host.available_size()) == (
        3, 5 if write_ack is not None else 0,
    )
    assert neighbor.host_value is case.neighbor_slots and not drops
    cache.dec_lock_ref(resident.last_node, resident.lock_receipt)
    assert resident_data.lock_ref == 0

    cache.loading_check()
    assert case.invoke() == (None, f.running)
    assert case.construction == [(resident,)] and case.scheduler.waiting_queue == [req]
    assert controller.ack_load_queue == [ack] and len(f.loads) == 1
    assert case.gate._demand_restore_chunk.pending is pending
    ack.finish_event.synchronize.assert_not_called()

    f.complete.ready = True
    cache.loading_check()
    ack.finish_event.synchronize.assert_called_once_with()
    assert not cache.ongoing_load_back and not controller.ack_load_queue
    assert (prefix.lock_ref, prefix.host_lock_ref, suffix.host_lock_ref) == (1, 0, 1)
    with pytest.raises(BatchConstructionReached):
        case.invoke()
    assert case.construction == [(resident,), (req,)]
    assert not case.scheduler.waiting_queue and case.scheduler.chunked_req is req
    assert (req.extend_range.start, req.extend_range.length) == (8, 2)
    assert req.kv.req_pool_idx is None
    case.native_load.assert_called_once()
    assert [len(load.host_indices) for load in f.loads] == [4, 4]
    assert f.loads[-1].host_indices.tolist() == case.source_slots[4:].tolist()
    assert case.gate._demand_restore_chunk.pending is None and f.pipeline.pending is None

    cache.loading_check()
    cache.dec_lock_ref(req.last_node, req.lock_receipt)
    assert (prefix.lock_ref, prefix.host_lock_ref, suffix.lock_ref, suffix.host_lock_ref) == (0, 0, 0, 0)
    assert not controller.ack_load_queue and not cache.ongoing_load_back
    assert not controller.ack_write_queue and not cache.ongoing_write_through
    assert neighbor.host_value is case.neighbor_slots
    assert case.host.slot_used[case.neighbor_slots].all() and not drops
    cache.sanity_check()


@pytest.mark.parametrize(
    ("staged_native", "full_native", "wait_for_ack", "join_parent", "peer_split"),
    (
        pytest.param(
            {"members": 2, "select": False, "prefix_sizes": (8, 8)}, 16, True, False, 0,
            id="full-credit",
        ),
        pytest.param(
            {"members": 2, "select": False, "prefix_sizes": (18, 14)}, 32, True, False, 8,
            id="bounded-partial-credit",
        ),
        pytest.param(
            {"members": 2, "select": False, "prefix_sizes": (4, 8)}, 16, False, False, 0,
            id="insufficient-credit",
        ),
        pytest.param(
            {"members": 2, "select": False, "prefix_sizes": (8, 8)}, 16, False, True, 0,
            id="promoted-join-parent",
        ),
    ),
    indirect=("staged_native", "full_native"),
)
def test_d3_waits_for_unrelated_submitted_restore_ack(
    staged_native, monkeypatch, wait_for_ack, join_parent, peer_split,
):
    from test_beliefkv_terminal_capacity import insert_session

    f = staged_native
    cache, allocator, runtime = f.cache, f.allocator, f.runtime
    tree, controller = cache.tree_core, cache.cache_controller
    host = cache.host_pool_group.anchor_entry.host_pool
    peer, consumer = f.members
    peer_leaf, consumer_leaf = f.leaves
    key = runtime.visible[consumer.rid]
    peer_tokens, hit = len(peer_leaf.key), len(consumer_leaf.key)
    source_slots = consumer_leaf.component_data[0].host_value

    if peer_split:
        parent_id, actions = tree.split_host_only_full_prefix(
            peer_leaf.id, peer_leaf.creation_time, peer_split,
        )
        cache._apply_cache_actions(actions)
        assert peer_leaf.parent is tree.node_by_id(parent_id)
    credit = len(peer_leaf.key)

    held_host = host.alloc(host.available_size() - 8)
    neighbor, _ = insert_session(cache, range(100, 108), "waiting-parent")
    neighbor_slots = _backup(cache, neighbor, demote=True)
    neighbor_data = neighbor.component_data[0]
    assert neighbor_data.session_ref > 0 and neighbor_data.value is None
    for start in (200, 210, 220):
        insert_session(cache, range(start, start + 8), str(start))
    held_device = allocator.alloc(allocator.available_size() - peer_tokens)

    losses = []

    def record_eviction(node, _component, count):
        full = node.component_data[0]
        if full.value is None and full.session_ref > 0:
            losses.append((node.id, count))

    monkeypatch.setattr(tree, "_beliefkv_host_eviction_observer", record_eviction)
    f.waiting[:] = [peer]
    gate, scheduler, construction, invoke = _d3_scheduler(f, monkeypatch)
    runtime._native_chunk_input_reserve = scheduler.chunked_prefill_size = 4
    gate.max_nodes = 2 if peer_split else 1
    if peer_split:
        # The submitted transfer covers both extents, but the credit walk can see only its anchor.
        gate.max_candidates = 1
    gate.materialized.add(gate._context(key))
    try:
        # The consumer arrives after the peer takes the ordinary native submission path.
        with monkeypatch.context() as peer_batch:
            peer_batch.setattr(f.pipeline, "stage", lambda *_args, **_kwargs: False)
            with pytest.raises(BatchConstructionReached):
                invoke()
        assert construction == [(peer,)] and not scheduler.waiting_queue
        peer_load, = f.loads
        assert controller.load_queue == [peer_load] and peer_load.beliefkv_command_id is None
        assert cache.ready_to_load_host_cache() >= 0
        ack, = controller.ack_load_queue
        receipt = cache.ongoing_load_back[peer_leaf.id]
        assert ack.node_ids == [peer_leaf.id] and ack.num_tokens_by_pool == {"kv": peer_tokens}
        assert not ack.child_receipts
        assert not controller.load_queue and not controller.write_queue and not controller.ack_write_queue
        assert f.pipeline.pending is None and not getattr(consumer, "_beliefkv_d3_restore_credit", None)
        assert (allocator.available_size(), host.available_size(), cache.full_evictable_size()) == (0, 0, 24)
        headroom = observe_materialization_headroom(
            cache, max_nodes=gate.max_nodes, required_free_tokens=hit,
            protected_host_nodes=(consumer_leaf.id,),
        )
        assert headroom.reason == NODE_CENSUS_BOUND and headroom.available_tokens == 0
        assert not ack.finish_event.query()
        if peer_split:
            chunk = scheduler.chunked_prefill_size
            reserve = chunk + scheduler.page_size * len(f.running.reqs)
            required = max(chunk, cache.load_back_threshold) + reserve
            assert required <= credit < hit < peer_tokens
            assert allocator.available_size() + cache.full_evictable_size() >= required

        if join_parent:
            runtime._join_priority_promoted = consumer.rid
        scheduler.waiting_queue.append(consumer)
        assert invoke() == (None, f.running)
        if wait_for_ack:
            assert not losses, f"unpublished peer H2D led to live Host-only evictions: {losses}"
            wait = consumer._beliefkv_d3_restore_credit
            wait_key, wait_hit, receipts = wait
            assert wait_key == key and wait_hit == hit
            assert tuple(receipts) == (peer_leaf.id,) and receipts[peer_leaf.id] is receipt
            assert consumer._beliefkv_d3_indexed_restore_defer_key == key
            deferred, = (
                row for event in runtime._opportunity_writer.record.call_args_list
                if (row := event.args[0])["event"] == "subagent_materialization_restore_pressure_deferred"
            )
            assert deferred["key"] == asdict(key) and deferred["already_submitted"]
            assert deferred["native_node_ids"] == [peer_leaf.id]
            assert (deferred["unfunded_restore_tokens"], deferred["reclaimable_after_ack_tokens"]) == (
                hit, credit,
            )
            for ready in (False, True):
                f.complete.ready = ready
                assert ack.finish_event.query() is ready
                assert invoke() == (None, f.running)
                assert construction == [(peer,)] and scheduler.waiting_queue == [consumer]
                assert f.loads == [peer_load] and controller.ack_load_queue == [ack]
                assert cache.ongoing_load_back[peer_leaf.id] is receipt
                assert consumer._beliefkv_d3_restore_credit is wait
                assert consumer.kv.req_pool_idx is None and consumer_leaf.load_back_pending_id is None
                assert f.pipeline.pending is None and gate._demand_restore_chunk.pending is None
                assert neighbor_data.host_value is neighbor_slots and not losses
            ack.finish_event.synchronize.assert_not_called()
        else:
            assert (join_parent or credit < hit) and losses == [(neighbor.id, 8)]
            decline, = _restore_declines(runtime)
            assert decline["key"] == asdict(key) and decline["reason"] == "restore_in_flight"
            assert not getattr(consumer, "_beliefkv_d3_restore_credit", None)
            assert controller.ack_load_queue[0] is ack
            assert [load.node_id for load in f.loads] == [peer_leaf.id, consumer_leaf.id]
            ack.finish_event.synchronize.assert_not_called()

        f.complete.ready = True
        cache.loading_check()
        ack.finish_event.synchronize.assert_called_once_with()
        assert not controller.ack_load_queue and not cache.ongoing_load_back
        for _ in range(hit + 1):
            try:
                assert invoke() == (None, f.running)
            except BatchConstructionReached:
                break
            assert peer_split and not losses
            assert f.pipeline.pending is not None or gate._demand_restore_chunk.pending is not None
            target_ack, = controller.ack_load_queue
            assert target_ack is not ack and target_ack.node_ids == [f.loads[-1].node_id]
            cache.loading_check()
            target_ack.finish_event.synchronize.assert_called_once_with()
        else:
            pytest.fail("consumer did not reach native batch construction")
        assert construction == [(peer,), (consumer,)] and not scheduler.waiting_queue
        assert consumer.extend_range.start == hit
        assert f.loads[-1].node_id == consumer_leaf.id
        assert [
            slot for load in f.loads[1:] for slot in load.host_indices.tolist()
        ] == source_slots.tolist()
        assert not getattr(consumer, "_beliefkv_d3_restore_credit", None)
        assert runtime.counts["subagent_materialization_restore_pressure_deferrals"] == int(wait_for_ack)
        cache.loading_check()
        for req in (peer, consumer):
            cache.dec_lock_ref(req.last_node, req.lock_receipt)
        assert not controller.ack_load_queue and not cache.ongoing_load_back
        assert f.pipeline.pending is None and gate._demand_restore_chunk.pending is None
        if wait_for_ack:
            assert neighbor_data.value is None and neighbor_data.host_value is neighbor_slots
            assert host.slot_used[neighbor_slots].all() and not losses
        cache.sanity_check()
    finally:
        gate.close()
        allocator.free(held_device)
        host.free(held_host)


@pytest.mark.parametrize(
    ("staged_native", "full_native"),
    (({"members": 2, "select": False, "prefix_sizes": (18, 8)}, 26),),
    indirect=True,
)
@pytest.mark.parametrize(
    ("consumer_queued", "producer_protection", "decode_growth"),
    (
        pytest.param(True, None, 0, id="queued"),
        pytest.param(False, None, 0, id="removed"),
        pytest.param(True, "promoted", 0, id="promoted-join-producer"),
        pytest.param(True, None, 2, id="partial-credit"),
        pytest.param(True, None, 3, id="insufficient-partial-credit"),
        pytest.param(True, "promoted", 2, id="promoted-producer-partial-credit"),
        pytest.param(True, "promoted-after-retry", 2, id="promoted-producer-after-peer-retry"),
        pytest.param(True, "pending_join", 2, id="pending-join-producer-partial-credit"),
        pytest.param(True, "fit_target", 2, id="fit-target-producer-partial-credit"),
    ),
)
def test_d3_staged_producer_yields_ack_credit_to_live_consumer(
    staged_native, monkeypatch, consumer_queued, producer_protection, decode_growth,
):
    from sglang.srt.managers.schedule_policy import AddReqResult
    from test_beliefkv_terminal_capacity import insert_session

    f = staged_native
    cache, allocator, runtime = f.cache, f.allocator, f.runtime
    tree, controller = cache.tree_core, cache.cache_controller
    host = cache.host_pool_group.anchor_entry.host_pool
    producer, consumer = f.members
    producer_leaf, consumer_leaf = f.leaves
    consumer_key = runtime.visible[consumer.rid]
    producer_slots = producer_leaf.component_data[0].host_value.tolist()
    consumer_slots = consumer_leaf.component_data[0].host_value.tolist()

    held_host = host.alloc(host.available_size() - 14)
    neighbor, _ = insert_session(cache, range(101, 109), "waiting-parent")
    neighbor_slots = _backup(cache, neighbor, demote=True)
    neighbor_data = neighbor.component_data[0]
    for start, size in ((111, 6), (121, 6), (131, 8), (201, 32)):
        insert_session(cache, range(start, start + size), str(start))
    held_device = allocator.alloc(allocator.available_size() - 3)
    decode_slots = None
    if decode_growth:
        # A four-token minimum plus the three-slot reserve needs seven slots.
        monkeypatch.setattr(cache, "load_back_threshold", 4)

    losses = []

    def record_eviction(node, _component, count):
        full = node.component_data[0]
        if full.value is None and full.session_ref > 0:
            losses.append((node.id, count))

    monkeypatch.setattr(tree, "_beliefkv_host_eviction_observer", record_eviction)
    f.waiting[:] = [producer]
    resumed_peer = producer_protection == "promoted-after-retry"
    producer_promoted = producer_protection in {"promoted", "promoted-after-retry"}
    if producer_promoted:
        runtime._join_priority_promoted = producer.rid
    gate, scheduler, construction, invoke = _d3_scheduler(f, monkeypatch)
    restore = gate._demand_restore_chunk
    gate.materialized.add(gate._context(consumer_key))
    start_loading = controller.start_loading

    def complete_consumer_load(**kwargs):
        consumer_load = [op.node_id for op in controller.load_queue] == [consumer_leaf.id]
        result = start_loading(**kwargs)
        if result >= 0 and decode_growth:
            # Preserve completed events across ring reuse while controlling new ACKs.
            event = controller.ack_load_queue[-1].finish_event

            def synchronize():
                assert event.query()
                event.query = lambda: True

            event.synchronize.side_effect = synchronize
            controller.layer_done_counter.events[result].finish_event = event
        elif consumer_load:
            # Keep a new producer slice in flight while allowing the consumer's own ACK.
            controller.ack_load_queue[-1].finish_event.query = lambda: True
        return result

    monkeypatch.setattr(controller, "start_loading", complete_consumer_load)
    try:
        assert neighbor_data.session_ref > 0 and neighbor_data.value is None
        assert (allocator.available_size(), host.available_size()) == (3, 6)
        assert invoke() == (None, f.running)
        staged = restore.pending
        assert staged.request is producer and staged.tokens == 6 and staged.remaining_tokens == 12
        first_load, = f.loads
        first_ack, = controller.ack_load_queue
        receipt = cache.ongoing_load_back[staged.prefix_node]
        assert first_ack.node_ids == [staged.prefix_node] and not first_ack.child_receipts
        assert first_load.host_indices.tolist() == producer_slots[:6]
        prefix = tree.node_by_id(staged.prefix_node).component_data[0]
        suffix = producer_leaf.component_data[0]
        assert (prefix.lock_ref, prefix.host_lock_ref, suffix.host_lock_ref) == (2, 1, 1)
        assert (allocator.available_size(), host.available_size()) == (3, 0)

        if decode_growth:
            decode_slots = allocator.alloc(decode_growth)
            device_free = allocator.available_size()
            assert decode_slots is not None and device_free == 3 - decode_growth
            assert 6 < 8 - device_free
            assert (device_free + 6 >= 4 + 3) == (decode_growth == 2)

        if producer_protection == "pending_join":
            runtime._join_priority_pending = {staged.key.invocation_id: "join"}
            assert getattr(runtime, "_join_priority_promoted", None) is None
        elif producer_protection == "fit_target":
            guard = ReadyServiceGuard(runtime._agent_admission, fit_after_s=600)
            guard.fit_target = staged.key
            runtime._agent_admission._ready_service_guard = guard
        if resumed_peer:
            # Its prior ordinary ACK retry yielded to the promoted JOIN parent.
            consumer._beliefkv_d3_indexed_restore_defer_key = consumer_key

        scheduler.waiting_queue.append(consumer)
        assert invoke() == (None, f.running)
        if resumed_peer:
            assert not losses
            assert f.loads == [first_load] and controller.ack_load_queue == [first_ack]
            assert scheduler.waiting_queue == [producer, consumer] and not construction
            assert restore.pending is staged and restore.parked is None
            key, hit, receipts = consumer._beliefkv_d3_restore_credit
            assert key == consumer_key and hit == len(consumer_slots)
            assert receipts == {staged.prefix_node: receipt}
            assert gate._peer_restore_retry is None and staged.credit_waiter is None
            first_ack.finish_event.synchronize.assert_not_called()
            assert invoke() == (None, f.running)
            assert runtime.counts["subagent_materialization_protected_stage_waits"] == 1

            f.complete.ready = True
            cache.loading_check()
            f.complete.ready = False
            assert invoke() == (None, f.running)
            assert restore.pending.request is producer
            assert restore.pending.remaining_tokens < staged.remaining_tokens
            assert not losses and len(f.loads) == 2 and not construction
            assert neighbor_data.host_value is neighbor_slots
            f.complete.ready = True
            cache.loading_check()
            cache.sanity_check()
            return
        if decode_growth and (producer_protection is not None or decode_growth == 3):
            runtime.on_prefill_candidate_result.assert_called_with(
                consumer, admitted=False, result="RESTORE_STAGED",
            )
            assert not getattr(consumer, "_beliefkv_d3_restore_credit", None)
            assert gate._peer_restore_retry is None
            assert restore.pending is staged and restore.parked is None and not construction
            assert len(f.loads) == 2
            assert f.loads[-1].node_id == consumer_leaf.id
            assert f.loads[-1].host_indices.tolist() == consumer_slots
            assert sum(count for _, count in losses) == 5 + decode_growth
            producer_ack, consumer_ack = controller.ack_load_queue
            assert producer_ack is first_ack and consumer_ack.node_ids == [consumer_leaf.id]
            assert consumer_ack.num_tokens_by_pool == {"kv": 8}
            first_ack.finish_event.synchronize.assert_not_called()
            f.complete.ready = True
            cache.loading_check()
            f.pipeline.cancel("test_complete")
            cache.sanity_check()
            return

        assert not losses, f"consumer evicted referenced Host-only KV before producer ACK: {losses}"
        runtime.on_prefill_candidate_result.assert_called_with(
            consumer, admitted=False, result=AddReqResult.OTHER.name,
        )
        assert scheduler.waiting_queue == [producer, consumer] and not construction
        assert f.loads == [first_load] and controller.ack_load_queue == [first_ack]
        assert not losses and f.pipeline.pending is None
        key, hit, receipts = consumer._beliefkv_d3_restore_credit
        assert key == consumer_key and hit == 8
        assert tuple(receipts) == (staged.prefix_node,) and receipts[staged.prefix_node] is receipt
        deferred, = (
            row for event in runtime._opportunity_writer.record.call_args_list
            if (row := event.args[0])["event"] == "subagent_materialization_restore_pressure_deferred"
        )
        assert deferred["key"] == asdict(consumer_key) and deferred["already_submitted"]
        assert deferred["native_node_ids"] == first_ack.node_ids
        assert (deferred["reclaimable_after_ack_tokens"], deferred["unfunded_restore_tokens"]) == (
            6, 5 + decode_growth,
        )
        if decode_growth:
            cache.loading_check()
            assert invoke() == (None, f.running)
            assert scheduler.waiting_queue == [producer, consumer] and not construction
            assert f.loads == [first_load] and controller.ack_load_queue == [first_ack]
            assert cache.ongoing_load_back[staged.prefix_node] is receipt and not losses
        first_ack.finish_event.synchronize.assert_not_called()

        f.complete.ready = True
        cache.loading_check()
        f.complete.ready = False
        first_ack.finish_event.synchronize.assert_called_once_with()
        assert not controller.ack_load_queue and not cache.ongoing_load_back
        assert restore.pending is staged
        assert (prefix.lock_ref, prefix.host_lock_ref, suffix.host_lock_ref) == (1, 0, 1)
        assert tree._can_reclaim_full_host_duplicate(tree.node_by_id(staged.prefix_node))

        if decode_growth:
            pins_at_borrower_lock = []
            acquire = cache.inc_lock_ref

            def record_borrower_lock(node_id, *args, **kwargs):
                acquired = acquire(node_id, *args, **kwargs)
                if len(f.loads) == 2 and node_id == f.loads[-1].node_id:
                    pins_at_borrower_lock.append((prefix.lock_ref, suffix.host_lock_ref))
                return acquired

            with monkeypatch.context() as track:
                track.setattr(cache, "inc_lock_ref", record_borrower_lock)
                assert invoke() == (None, f.running)

            assert pins_at_borrower_lock == [(1, 1), (1, 1)]
            assert not construction and scheduler.waiting_queue == [producer, consumer]
            assert f.pipeline.pending is None and scheduler.chunked_req is None
            assert gate._peer_restore_retry is None
            borrower = restore.pending
            assert borrower.request is consumer and (borrower.tokens, borrower.remaining_tokens) == (4, 4)
            assert borrower.suffix_node == consumer_leaf.id
            assert len(f.loads) == 2 and f.loads[-1].node_id == borrower.prefix_node
            assert f.loads[-1].host_indices.tolist() == consumer_slots[:4]
            borrower_ack, = controller.ack_load_queue
            assert borrower_ack.node_ids == [borrower.prefix_node]
            assert borrower_ack.num_tokens_by_pool == {"kv": 4}
            assert restore.parked == (consumer, staged)
            assert gate.has_staged_restore(consumer, consumer_key)
            assert gate.has_staged_restore(producer, staged.key)
            assert not gate.has_staged_restore(producer, consumer_key)
            assert producer._beliefkv_d3_committed_restore is None
            assert (prefix.lock_ref, prefix.host_lock_ref, suffix.host_lock_ref) == (1, 0, 1)
            assert allocator.available_size() == 3 and prefix.host_value is None
            assert not losses and neighbor_data.host_value is neighbor_slots
            assert host.slot_used[neighbor_slots].all()

            # The producer remains first in the queue throughout the borrower's chain.
            assert invoke() == (None, f.running)
            assert len(f.loads) == 2 and not losses
            assert not construction and scheduler.waiting_queue == [producer, consumer]
            assert restore.pending is borrower and controller.ack_load_queue == [borrower_ack]
            borrower_ack.finish_event.synchronize.assert_not_called()
            f.complete.ready = True
            cache.loading_check()
            borrower_ack.finish_event.synchronize.assert_called_once_with()

            with pytest.raises(BatchConstructionReached):
                invoke()
            assert construction == [(consumer,)] and scheduler.waiting_queue == [producer]
            assert consumer.extend_range.start == len(consumer_slots)
            assert len(f.loads) == 3 and f.loads[2].node_id == consumer_leaf.id
            assert restore.pending is None and restore.parked == (consumer, staged)
            assert gate.has_staged_restore(producer, staged.key)
            assert not gate.has_staged_restore(consumer, consumer_key)
            assert producer._beliefkv_d3_committed_restore is None
            assert (prefix.lock_ref, prefix.host_lock_ref, suffix.host_lock_ref) == (1, 0, 1)
            assert not losses
            cache.loading_check()

            for remaining in (8, 4):
                f.complete.ready = False
                assert invoke() == (None, f.running)
                resumed = restore.pending
                assert resumed.request is producer and resumed.suffix_node == producer_leaf.id
                assert (resumed.tokens, resumed.remaining_tokens) == (4, remaining)
                assert restore.parked is None
                assert (prefix.lock_ref, prefix.host_lock_ref, suffix.host_lock_ref) == (2, 0, 1)
                assert not losses
                next_ack, = controller.ack_load_queue
                assert next_ack.node_ids == [resumed.prefix_node]
                f.complete.ready = True
                cache.loading_check()
                next_ack.finish_event.synchronize.assert_called_once_with()

            with pytest.raises(BatchConstructionReached):
                invoke()
            assert construction == [(consumer,), (producer,)] and not scheduler.waiting_queue
            assert producer.extend_range.start == len(producer_slots)
            assert [len(load.host_indices) for load in f.loads] == [6, 4, 4, 4, 4, 4]
            assert [
                slot for load in f.loads[1:3] for slot in load.host_indices.tolist()
            ] == consumer_slots
            assert [
                slot for load in [first_load, *f.loads[3:]] for slot in load.host_indices.tolist()
            ] == producer_slots
            assert not losses and neighbor_data.host_value is neighbor_slots
            assert host.slot_used[neighbor_slots].all()

            cache.loading_check()
            for req in (consumer, producer):
                cache.dec_lock_ref(req.last_node, req.lock_receipt)
            gate.close()
            assert restore.pending is None and not cache.ongoing_load_back
            for load in f.loads:
                full = tree.node_by_id(load.node_id).component_data[0]
                assert (full.lock_ref, full.host_lock_ref) == (0, 0)
            cache.sanity_check()
            return

        expect_yield = consumer_queued and not producer_promoted
        if expect_yield:
            # A producer that restages ends this pass with OTHER. Also observe
            # the consumer's next pass so the old path exposes its KV eviction.
            for _ in range(2):
                try:
                    assert invoke() == (None, f.running)
                except BatchConstructionReached:
                    break
            assert not losses, (
                f"peer ACK credit was recycled before the consumer, losing Host-only KV {losses}. "
                f"H2D sequence {[(load.node_id, len(load.host_indices)) for load in f.loads]}"
            )
            assert construction == [(consumer,)] and scheduler.waiting_queue == [producer]
            assert restore.pending is staged and f.pipeline.pending is None
            assert (prefix.lock_ref, prefix.host_lock_ref, suffix.host_lock_ref) == (1, 0, 1)
            assert prefix.value is not None and prefix.host_value is None and suffix.value is None
            assert consumer.extend_range.start == hit and consumer.host_loaded_length == hit
            assert [load.node_id for load in f.loads] == [staged.prefix_node, consumer_leaf.id]
            assert f.loads[-1].host_indices.tolist() == consumer_slots
            assert consumer._beliefkv_d3_restore_credit is None
            cache.loading_check()
            assert not controller.ack_load_queue and not cache.ongoing_load_back
        elif not consumer_queued:
            scheduler.waiting_queue.remove(consumer)
            assert runtime.visible[consumer.rid] == consumer_key

        # Resume the producer once its ACK has been published.
        assert invoke() == (None, f.running)
        resumed = restore.pending
        assert resumed.request is producer and resumed is not staged
        assert resumed.suffix_node == staged.suffix_node
        offset = staged.tokens
        assert f.loads[-1].host_indices.tolist() == producer_slots[offset:offset + resumed.tokens]
        next_ack, = controller.ack_load_queue
        assert next_ack.node_ids == [resumed.prefix_node]
        assert construction == ([(consumer,)] if expect_yield else [])
        expected_waiting = [producer, consumer] if producer_promoted else [producer]
        assert scheduler.waiting_queue == expected_waiting and scheduler.chunked_req is None
        assert runtime.counts["subagent_materialization_restore_credit_yields"] == int(expect_yield)
        assert neighbor_data.host_value is neighbor_slots and host.slot_used[neighbor_slots].all()
        assert not losses

        f.complete.ready = True
        cache.loading_check()
        gate.close()
        if expect_yield:
            cache.dec_lock_ref(consumer.last_node, consumer.lock_receipt)
        assert (prefix.lock_ref, prefix.host_lock_ref, suffix.host_lock_ref) == (0, 0, 0)
        assert restore.pending is None and not cache.ongoing_load_back
        cache.sanity_check()
    finally:
        gate.close()
        if decode_slots is not None:
            allocator.free(decode_slots)
        allocator.free(held_device)
        host.free(held_host)


@pytest.mark.parametrize(
    ("staged_native", "full_native"),
    (({"members": 3, "select": False, "prefix_sizes": (18, 1, 8)}, 27),),
    indirect=True,
)
def test_d3_waits_for_protected_stage_with_ordinary_loads_selected(staged_native, monkeypatch):
    from sglang.srt.managers.schedule_policy import AddReqResult
    from test_beliefkv_terminal_capacity import insert_session

    f = staged_native
    cache, allocator, runtime = f.cache, f.allocator, f.runtime
    tree, controller = cache.tree_core, cache.cache_controller
    host = cache.host_pool_group.anchor_entry.host_pool
    producer, selected, consumer = f.members
    key = runtime.visible[consumer.rid]
    held_host = host.alloc(host.available_size() - 14)
    neighbor, _ = insert_session(cache, range(101, 109), "waiting-parent")
    neighbor_slots = _backup(cache, neighbor, demote=True)
    for start, size in ((111, 6), (121, 6), (131, 8), (201, 32)):
        insert_session(cache, range(start, start + size), str(start))
    held_device = allocator.alloc(allocator.available_size() - 3)
    f.waiting[:] = [producer]
    runtime._join_priority_promoted = producer.rid
    gate, scheduler, construction, invoke = _d3_scheduler(f, monkeypatch)
    gate.materialized.add(gate._context(key))

    try:
        assert invoke() == (None, f.running)
        restore = gate._demand_restore_chunk
        staged = restore.pending
        existing_ack, = controller.ack_load_queue
        assert staged.request is producer and not construction
        assert existing_ack.node_ids == [staged.prefix_node]
        prior_receipt = cache.ongoing_load_back[staged.prefix_node]
        consumer._beliefkv_d3_indexed_restore_defer_key = key

        adder = f.adder()
        scheduler.waiting_queue[:] = [producer, selected, consumer]
        gate.prepare(tuple(scheduler.waiting_queue), running_batch=f.running, adder=adder)
        selected.init_next_round_input(cache)
        assert selected.host_hit_length == 1
        adder.add_one_req(selected, has_chunked_req=False, truncation_align_size=None)
        assert adder.can_run_list == [selected]
        ordinary, = controller.load_queue
        assert ordinary.node_ids == [selected.last_node]
        assert len(f.loads) == 2

        consumer.init_next_round_input(cache)
        assert consumer.host_hit_length == 8
        assert adder.add_one_req(
            consumer, has_chunked_req=False, truncation_align_size=None,
        ) is AddReqResult.OTHER
        assert controller.load_queue == [ordinary] and len(f.loads) == 2
        assert controller.ack_load_queue == [existing_ack]
        assert adder.can_run_list == [selected] and restore.pending is staged
        assert consumer._beliefkv_d3_restore_credit == (key, 8, {staged.prefix_node: prior_receipt})
        assert neighbor.component_data[0].host_value is neighbor_slots
        assert restore.parked is None and staged.credit_waiter is None

        assert cache.ready_to_load_host_cache() >= 0
        f.complete.ready = True
        cache.loading_check()
        cache.dec_lock_ref(selected.last_node, selected.lock_receipt)
        cache.sanity_check()
    finally:
        gate.close()
        allocator.free(held_device)
        host.free(held_host)


@pytest.mark.parametrize(
    ("staged_native", "full_native"),
    (({"members": 2, "select": False, "prefix_sizes": (18, 8)}, 27),),
    indirect=True,
)
def test_d3_uses_leading_staged_ack_ahead_of_tagged_join(staged_native, monkeypatch):
    from test_beliefkv_terminal_capacity import insert_session

    f = staged_native
    cache, allocator, runtime = f.cache, f.allocator, f.runtime
    tree, controller = cache.tree_core, cache.cache_controller
    host = cache.host_pool_group.anchor_entry.host_pool
    producer, consumer = f.members
    consumer_key = runtime.visible[consumer.rid]
    consumer_leaf = f.leaves[1]

    held_host = host.alloc(host.available_size() - 15)
    neighbor, _ = insert_session(cache, range(101, 109), "waiting-parent")
    neighbor_slots = _backup(cache, neighbor, demote=True)
    join, generation = insert_session(cache, (91,), "join-parent")
    join_slots = _backup(cache, join, demote=True)
    for start, size in ((111, 6), (121, 6), (201, 32)):
        insert_session(cache, range(start, start + size), str(start))
    held_device = allocator.alloc(allocator.available_size() - 3)
    losses = []

    def record_eviction(node, _component, count):
        full = node.component_data[0]
        if full.value is None and full.session_ref > 0:
            losses.append((node.id, count))

    monkeypatch.setattr(tree, "_beliefkv_host_eviction_observer", record_eviction)
    f.waiting[:] = [producer]
    gate, scheduler, construction, invoke = _d3_scheduler(f, monkeypatch)
    gate.materialized.add(gate._context(consumer_key))
    try:
        assert invoke() == (None, f.running)
        staged = gate._demand_restore_chunk.pending
        lead, = controller.ack_load_queue
        lead_receipt = cache.ongoing_load_back[staged.prefix_node]
        assert lead.node_ids == [staged.prefix_node] and not lead.child_receipts
        assert lead.num_tokens_by_pool == {"kv": staged.tokens} == {"kv": 6}
        prefetched = cache.prefetch_gpu_session_node(
            session_id="join-parent", session_generation=generation,
            leaf_node_id=join.id, leaf_creation_time=float(join.creation_time),
            node_id=join.id, node_creation_time=float(join.creation_time),
            beliefkv_command_id="join-prefetch", beliefkv_before_enqueue=lambda _: True,
            beliefkv_include_mamba=False,
        )
        assert prefetched.issued and prefetched.load_started
        first, tagged = controller.ack_load_queue
        assert first is lead and tagged.node_ids == [join.id]
        join_child, = tagged.child_receipts
        assert join_child.node_id == join.id and join_child.command_id == "join-prefetch"
        join_receipt = cache.ongoing_load_back[join.id]
        join_ready = NS(value=False)
        monkeypatch.setattr(tagged.finish_event, "query", lambda: join_ready.value)
        assert (allocator.available_size(), host.available_size()) == (2, 0)

        scheduler.waiting_queue.append(consumer)
        assert invoke() == (None, f.running)
        assert not losses, f"ordinary ACK ahead of tagged JOIN led to Host-only eviction: {losses}"
        wait = consumer._beliefkv_d3_restore_credit
        key, hit, receipts = wait
        assert key == consumer_key and hit == 8
        assert tuple(receipts) == (staged.prefix_node,)
        assert receipts[staged.prefix_node] is lead_receipt
        assert consumer._beliefkv_d3_indexed_restore_defer_key == consumer_key
        assert staged.credit_waiter == (consumer, wait)
        assert invoke() == (None, f.running)
        assert controller.ack_load_queue == [lead, tagged] and len(f.loads) == 2

        f.complete.ready = True
        cache.loading_check()
        f.complete.ready = False
        assert controller.ack_load_queue == [tagged]
        lead.finish_event.synchronize.assert_called_once_with()
        tagged.finish_event.synchronize.assert_not_called()
        assert cache.ongoing_load_back[join.id] is join_receipt

        # The lead's mirrors fund this load while JOIN retains its own native ACK.
        assert invoke() == (None, f.running)
        still_tagged, demand = controller.ack_load_queue
        assert still_tagged is tagged and demand.node_ids == [consumer_leaf.id]
        assert [load.node_id for load in f.loads] == [staged.prefix_node, join.id, consumer_leaf.id]
        assert not demand.child_receipts and consumer._beliefkv_d3_restore_credit is None
        assert cache.ongoing_load_back[join.id] is join_receipt
        assert join.component_data[0].host_value is join_slots
        assert gate._demand_restore_chunk.pending is staged and not construction
        assert runtime.counts["subagent_materialization_restore_pressure_deferrals"] == 1
        assert runtime.counts["subagent_materialization_restore_credit_yields"] == 1
        tagged.finish_event.synchronize.assert_not_called()
        assert not losses

        join_ready.value = True
        cache.loading_check()
        tagged.finish_event.synchronize.assert_called_once_with()
        assert controller.ack_load_queue == [demand]
        child_commit, = f.commits[-1].child_commits
        assert child_commit.command_id == "join-prefetch"
        f.complete.ready = True
        cache.loading_check()
        scheduler.waiting_queue.remove(producer)
        with pytest.raises(BatchConstructionReached):
            invoke()
        assert construction == [(consumer,)] and consumer.extend_range.start == 8
        assert neighbor.component_data[0].host_value is neighbor_slots and not losses
        assert host.slot_used[neighbor_slots].all() and host.slot_used[join_slots].all()
        assert not cache.ongoing_load_back and not controller.ack_load_queue
        cache.dec_lock_ref(consumer.last_node, consumer.lock_receipt)
        cache.sanity_check()
    finally:
        gate.close()
        allocator.free(held_device)
        host.free(held_host)


@pytest.mark.parametrize(
    ("staged_native", "full_native"),
    (({"members": 1, "select": False, "prefix_sizes": (18,)}, 32),),
    indirect=True,
)
@pytest.mark.parametrize(
    ("case", "lead_tokens", "device_free"),
    (
        pytest.param("own", 0, 3, id="own"),
        pytest.param("own", 6, 3, id="own-behind-ordinary"),
        pytest.param("unfunded", 2, 2, id="own-ack-cannot-fund"),
        pytest.param("unfunded", 1, 4, id="subchunk-lead-cannot-fund"),
        pytest.param("foreign", 0, 3, id="foreign"),
        pytest.param("stale-attempt", 0, 3, id="stale-attempt"),
        pytest.param("leased-only", 0, 3, id="leased-only"),
        pytest.param("promoted", 0, 3, id="promoted"),
    ),
)
def test_d3_retries_own_tagged_join_ack_with_independent_capacity(
    staged_native, monkeypatch, case, lead_tokens, device_free,
):
    from test_beliefkv_terminal_capacity import insert_session

    f = staged_native
    cache, allocator, runtime, req = f.cache, f.allocator, f.runtime, f.req
    tree, controller = cache.tree_core, cache.cache_controller
    host = cache.host_pool_group.anchor_entry.host_pool
    source_slots = f.leaf.component_data[0].host_value.tolist()
    prefix_id, actions = tree.split_host_only_full_prefix(f.leaf.id, f.leaf.creation_time, 4)
    cache._apply_cache_actions(actions)
    second = tree.node_by_id(prefix_id)
    prefix_id, actions = tree.split_host_only_full_prefix(second.id, second.creation_time, 2)
    cache._apply_cache_actions(actions)
    first = tree.node_by_id(prefix_id)
    commands = {"join-first": first, "join-second": second}
    own_tokens = sum(len(node.key) for node in commands.values())

    neighbor_tokens = 8
    lease_tokens = 6 if case == "leased-only" else 0
    if lead_tokens:
        lease_tokens = 2
    free_host = 0 if lease_tokens else 6
    host_budget = neighbor_tokens + lead_tokens + lease_tokens + free_host
    held_host = host.alloc(host.available_size() - host_budget)
    neighbor, _ = insert_session(cache, range(101, 101 + neighbor_tokens), "waiting-parent")
    neighbor_slots = _backup(cache, neighbor, demote=True)
    for start, size in ((111, 6), (121, 6), (131, 4), (141, 4), (201, 24)):
        insert_session(cache, range(start, start + size), str(start))
    locks = []
    if lease_tokens:
        mirror, _ = insert_session(cache, range(151, 151 + lease_tokens), "leased")
        _backup(cache, mirror)
        locks.append((mirror.id, tree.inc_lock_ref(mirror.id).to_dec_params()))
    if lead_tokens:
        lead_node, _ = insert_session(cache, range(161, 161 + lead_tokens), "ordinary-peer")
        lead_slots = _backup(cache, lead_node, demote=True)
    held_device = allocator.alloc(allocator.available_size() - device_free - own_tokens - lead_tokens)
    assert held_host is not None and held_device is not None

    losses = []

    def record_eviction(node, _component, count):
        full = node.component_data[0]
        if full.value is None and full.session_ref > 0:
            losses.append((node.id, count))

    monkeypatch.setattr(tree, "_beliefkv_host_eviction_observer", record_eviction)
    gate, scheduler, construction, invoke = _d3_scheduler(f, monkeypatch)
    runtime.context_sessions = {f.key.context_id: f.key}
    runtime._prefetch_steps = {}
    runtime.physical_ledger = NS(is_pending=runtime._prefetch_steps.__contains__)
    if lease_tokens:
        runtime._prefetch_service_leases["existing"] = NS(node_id=mirror.id)
    if case == "promoted":
        runtime._join_priority_promoted = req.rid

    def register(operation):
        command = operation.beliefkv_command_id
        node = commands[command]
        assert operation.node_ids == [node.id]
        key = f.key
        if node is second:
            if case == "foreign":
                key = replace(key, request_id="foreign-consumer")
            elif case == "stale-attempt":
                key = replace(key, attempt_id=key.attempt_id + 1)
        runtime._prefetch_steps[command] = (
            PrefetchLoadStep(
                key, f.leaf.id, float(f.leaf.creation_time), node.id, float(node.creation_time), False,
            ),
            "join_ticket", None,
        )
        return True

    tagged_ready = NS(value=False)
    try:
        leading_acks, leading_ids = [], []
        if lead_tokens:
            assert cache.load_back(lead_node.id)
            assert cache.ready_to_load_host_cache() >= 0
            lead, = controller.ack_load_queue
            assert lead.node_ids == [lead_node.id] and not lead.child_receipts
            assert lead.num_tokens_by_pool == {"kv": lead_tokens}
            assert not lead.finish_event.query()
            leading_acks.append(lead)
            leading_ids.append(lead_node.id)

        issued = cache.prefetch_gpu_session_nodes(
            session_id=f.key.session_id, session_generation=f.key.session_generation,
            leaf_node_id=f.leaf.id, leaf_creation_time=float(f.leaf.creation_time),
            nodes=tuple((node.id, float(node.creation_time), False, cmd) for cmd, node in commands.items()),
            beliefkv_before_enqueue=register,
        )
        assert len(issued) == 2 and all(item.issued and item.load_started for item in issued)
        tagged, = controller.ack_load_queue[len(leading_acks):]
        monkeypatch.setattr(tagged.finish_event, "query", lambda: tagged_ready.value)
        initial_acks = [*leading_acks, tagged]
        assert controller.ack_load_queue == initial_acks
        anchors = [first.id, second.id]
        initial_load_ids = [*leading_ids, *anchors]
        hit = len(source_slots) - own_tokens
        receipts = {node: cache.ongoing_load_back[node] for node in anchors}
        assert tagged.node_ids == anchors and tagged.num_tokens_by_pool == {"kv": own_tokens}
        assert [child.command_id for child in tagged.child_receipts] == list(commands)
        assert (allocator.available_size(), host.available_size()) == (device_free, free_host)
        assert not tagged.finish_event.query() and not controller.load_queue
        if case == "leased-only":
            assert observe_materialization_headroom(cache).reclaimable_host_tokens == 6
        if lead_tokens:
            protected = (f.leaf.id, *anchors, mirror.id)
            headroom = observe_materialization_headroom(cache, protected_host_nodes=protected)
            assert headroom.census_complete and headroom.available_tokens == device_free
            assert observe_materialization_headroom(cache).reclaimable_host_tokens == lease_tokens
            chunk = runtime._native_chunk_input_reserve
            reserve = chunk + cache.page_size * len(f.running.reqs)
            minimum = max(chunk, cache.load_back_threshold) + reserve
            funding_credit = lead_tokens if lead_tokens >= chunk else 0
            assert device_free + cache.full_evictable_size() >= minimum
            assert device_free + lease_tokens + lead_tokens < hit
            assert (device_free + funding_credit >= minimum) is (case == "own")
            if case == "unfunded":
                excluded_credit = lead_tokens if lead_tokens < chunk else own_tokens
                assert device_free + funding_credit + excluded_credit >= minimum

        assert invoke() == (None, f.running)
        assert controller.ack_load_queue[:len(initial_acks)] == initial_acks and not construction
        assert all(cache.ongoing_load_back[node] is receipt for node, receipt in receipts.items())
        tagged.finish_event.synchronize.assert_not_called()
        if case != "own":
            assert not getattr(req, "_beliefkv_d3_restore_credit", None)
            assert gate._demand_restore_chunk.pending is None
            assert [load.node_id for load in f.loads] == [*initial_load_ids, f.leaf.id]
            assert f.pipeline.pending is not None
            return

        assert not losses, f"own JOIN ACK led to referenced Host-only eviction: {losses}"
        assert f.pipeline.pending is None and [load.node_id for load in f.loads] == initial_load_ids
        wait = req._beliefkv_d3_restore_credit
        expected_receipts = {node: cache.ongoing_load_back[node] for node in initial_load_ids}
        assert wait[:2] == (f.key, hit) and wait[2].keys() == expected_receipts.keys()
        assert all(wait[2][node] is receipt for node, receipt in expected_receipts.items())
        assert req._beliefkv_d3_indexed_restore_defer_key == f.key
        assert gate._peer_restore_retry is None
        deferred, = (
            row for event in runtime._opportunity_writer.record.call_args_list
            if (row := event.args[0])["event"] == "subagent_materialization_restore_pressure_deferred"
        )
        assert deferred["native_node_ids"] == initial_load_ids and deferred["already_submitted"]
        assert deferred["own_join_ack"]
        assert deferred["reclaimable_after_ack_tokens"] == lead_tokens < deferred["unfunded_restore_tokens"]
        if lead_tokens:
            # Retire the ordinary head while the tagged transfer stays in flight.
            assert controller.ack_load_queue == initial_acks
            f.complete.ready = True
            cache.loading_check()
            f.complete.ready = False
            lead.finish_event.synchronize.assert_called_once_with()
            assert controller.ack_load_queue == [tagged]
            assert lead_node.id not in cache.ongoing_load_back
            assert tree.full_host_duplicates[lead_node.id] is lead_node
            assert f.commits[-1].node_ids == (lead_node.id,) and not f.commits[-1].child_commits

        for ready in (False, True):
            tagged_ready.value = ready
            assert invoke() == (None, f.running)
            assert req._beliefkv_d3_restore_credit is wait
            assert controller.ack_load_queue == [tagged]
            assert [load.node_id for load in f.loads] == initial_load_ids
            assert all(cache.ongoing_load_back[node] is receipt for node, receipt in receipts.items())
            assert all(runtime.physical_ledger.is_pending(command) for command in commands)
            assert all(not commit.child_commits for commit in f.commits)
            assert f.pipeline.pending is None and gate._demand_restore_chunk.pending is None
            assert scheduler.waiting_queue == [req] and not construction and not losses
            assert neighbor.component_data[0].host_value is neighbor_slots
            assert host.slot_used[neighbor_slots].all()
        tagged.finish_event.synchronize.assert_not_called()

        def publish(commit):
            f.commits.append(commit)
            if commit.child_commits:
                assert [child.command_id for child in commit.child_commits] == list(commands)
                locks.append((second.id, tree.inc_lock_ref(second.id).to_dec_params()))
                runtime._prefetch_service_leases.update(
                    (cmd, NS(node_id=node.id)) for cmd, node in commands.items()
                )
                runtime._prefetch_steps.clear()

        monkeypatch.setattr(cache, "on_hicache_transfer_commit", publish)
        cache.loading_check()
        tagged.finish_event.synchronize.assert_called_once_with()
        assert not controller.ack_load_queue and not cache.ongoing_load_back
        assert not runtime._prefetch_steps
        if lead_tokens:
            headroom = observe_materialization_headroom(cache, protected_host_nodes=protected)
            assert headroom.reclaimable_host_tokens == lead_tokens
            assert headroom.available_tokens == device_free + lead_tokens
        f.complete.ready = False
        assert invoke() == (None, f.running)
        staged = gate._demand_restore_chunk.pending
        assert staged.key == f.key and staged.tokens == 6
        assert f.loads[-1].host_indices.tolist() == source_slots[own_tokens:own_tokens + staged.tokens]
        slice_ack, = controller.ack_load_queue
        assert slice_ack.node_ids == [staged.prefix_node] and not slice_ack.child_receipts
        assert slice_ack.num_tokens_by_pool == {"kv": staged.tokens}
        assert req._beliefkv_d3_restore_credit is None
        assert runtime.counts["subagent_materialization_restore_pressure_deferrals"] == 1
        assert all(node.component_data[0].lock_ref for node in commands.values())
        for _ in range(staged.remaining_tokens):
            f.complete.ready = True
            cache.loading_check()
            try:
                assert invoke() == (None, f.running)
            except BatchConstructionReached:
                break
            assert gate._demand_restore_chunk.pending is not None or f.pipeline.pending is not None
        else:
            pytest.fail("JOIN consumer did not reach native batch construction")
        assert construction == [(req,)] and not scheduler.waiting_queue
        assert req.extend_range.start == len(source_slots)
        if lead_tokens:
            assert f.loads[0].host_indices.tolist() == lead_slots.tolist()
        assert [
            slot for load in f.loads[len(leading_ids):] for slot in load.host_indices.tolist()
        ] == source_slots
        assert neighbor.component_data[0].host_value is neighbor_slots
        assert host.slot_used[neighbor_slots].all() and not losses
        cache.loading_check()
        cache.dec_lock_ref(req.last_node, req.lock_receipt)
        cache.sanity_check()
    finally:
        f.complete.ready = True
        tagged_ready.value = True
        cache.loading_check()
        f.pipeline.close()
        gate.close()
        for node, lock in locks:
            tree.dec_lock_ref(node, lock)
        allocator.free(held_device)
        host.free(held_host)


@pytest.mark.parametrize(
    "staged_native", ({"members": 3, "select": False, "prefix_sizes": (4, 8, 4)},),
    indirect=True,
)
def test_d3_selected_batch_decline_reuses_existing_probes(staged_native, monkeypatch):
    from sglang.srt.mem_cache.base_prefix_cache import InitLoadBackParams

    f = staged_native
    native, runtime = f.cache, f.runtime
    controller, tree = native.cache_controller, native.tree_core
    selected, target, another_target = f.members
    selected.init_next_round_input(native)
    f.first.add_one_req(selected, has_chunked_req=False, truncation_align_size=None)
    assert f.first.can_run_list == [selected]
    assert selected.extend_range.length == 1
    root_id = tree.root_node.id
    selected_members = [
        selected,
        NS(rid="legacy", last_node=root_id, extend_input_len=0),
        NS(rid="missing", last_node=root_id),
    ]
    runtime._native_chunk_input_reserve = 2
    runtime._native_page_size = 1
    runtime._agent_admission.latest = {key.invocation_id: key for key in runtime.visible.values()}
    gate = SubagentMaterialization(runtime, include_root_entries=True)
    gate.materialized.add(gate._context(runtime.visible[another_target.rid]))
    gate._adder = NS(can_run_list=selected_members)
    gate._running_batch = f.running
    params = InitLoadBackParams(best_match_node=f.leaves[1].id, host_hit_length=8, req=target)

    with monkeypatch.context() as scoped:
        native_load = Mock(return_value=object())
        available = Mock(return_value=1)
        node_by_id = Mock(wraps=tree.node_by_id)
        headroom = Mock(return_value=MaterializationHeadroom(
            True, NODE_CENSUS_BOUND, device_free_tokens=1,
        ))
        unexpected = Mock(side_effect=AssertionError("unexpected native probe"))
        scoped.setattr(native, "init_load_back", native_load)
        scoped.setattr(native, "inc_lock_ref", unexpected)
        scoped.setattr(native, "inc_host_lock_ref", unexpected)
        scoped.setattr(native, "evict_for_alloc", unexpected)
        scoped.setattr(native, "loading_check", unexpected)
        scoped.setattr(native, "writing_check", unexpected)
        scoped.setattr(controller, "start_loading", unexpected)
        scoped.setattr(f.allocator, "available_size", available)
        scoped.setattr(tree, "node_by_id", node_by_id)
        scoped.setattr("beliefkv.runtime.subagent_materialization.observe_materialization_headroom", headroom)
        gate._attach_demand_restore_pressure(native)

        assert native.init_load_back(params) is native_load.return_value
        decline, = _restore_declines(runtime)
        assert decline["reason"] == "prefill_batch_already_constrained"
        assert decline["selected_batch"] == {
            "requests": [
                {**asdict(runtime.visible[selected.rid]), "extend_input_len": 1},
                {"request_id": "legacy", "extend_input_len": 0},
                {"request_id": "missing", "extend_input_len": None},
            ],
            "running_request_count": 1,
            "chunk_input_reserve_tokens": 2,
            "native_h2d": {
                "native_node_ids": [selected.last_node],
                "selected_request_anchors": {selected.rid: selected.last_node},
                "reclaimable_after_ack_tokens": 4,
                "unfunded_restore_tokens": 7,
                "headroom_observation": NODE_CENSUS_BOUND,
            },
        }
        assert native.init_load_back(params) is native_load.return_value
        assert _restore_declines(runtime) == [decline]

        scoped.setattr(controller, "load_queue", [])
        next_params = InitLoadBackParams(
            best_match_node=f.leaves[2].id, host_hit_length=4, req=another_target,
        )
        assert native.init_load_back(next_params) is native_load.return_value
        _, without_observation = _restore_declines(runtime)
        assert without_observation["key"]["request_id"] == another_target.rid
        assert without_observation["reason"] == "prefill_batch_already_constrained"
        assert without_observation["selected_batch"]["native_h2d"] is None
        assert native_load.call_args_list == [call(params), call(params), call(next_params)]
        assert available.call_args_list == [call()] * 5
        assert node_by_id.call_args_list == [call(selected.last_node)] * 2
        expected_check = call(
            native, max_nodes=gate.max_nodes, required_free_tokens=8,
            protected_host_nodes=(f.leaves[1].id,),
        )
        assert headroom.call_args_list == [expected_check] * 2
        unexpected.assert_not_called()
        gate.close()
