from array import array
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from tests.test_full_only_native_lifecycle import full_native


def running(cache, identity="a", *, prompt=(1, 2, 3, 4), output=(80, 81)):
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.sampling.sampling_params import SamplingParams

    req = Req(
        rid=identity, origin_input_text="", origin_input_ids=array("q", prompt),
        sampling_params=SamplingParams(temperature=1., top_k=1, max_new_tokens=128),
        beliefkv_metadata={
            "root_workflow_id": "workflow", "invocation_id": identity,
            "context_id": identity, "context_epoch": 0,
        },
    )
    req.output_ids.extend(output)
    assert cache.req_to_token_pool.alloc([req]) is not None
    count = len(prompt) + len(output) - 1
    indices = cache.token_to_kv_pool_allocator.alloc(count)
    assert indices is not None
    cache.req_to_token_pool.write((req.kv.req_pool_idx, slice(0, count)), indices)
    req.kv.kv_committed_len = req.kv.kv_allocated_len = count
    req.last_node = cache.root_node_handle()
    return req


@pytest.fixture
def swap(full_native, monkeypatch):
    from sglang.srt.server_args import ServerArgs, set_global_server_args_for_scheduler
    from beliefkv.runtime.agentix_swap import AgentixNativeSwap

    set_global_server_args_for_scheduler(ServerArgs(model_path="dummy"))
    cache, allocator, writes, loads, commits = full_native
    safe = NS(value=True)
    actor = AgentixNativeSwap(cache, drained=lambda: safe.value)
    yield actor, cache, allocator, writes, loads, commits, safe
    actor.close()


def ack(cache):
    assert len(cache.cache_controller.ack_write_queue) == 1
    cache.writing_check(finish_count=1)


def test_real_native_checkpoint_ack_release_and_request_work_preservation(swap):
    actor, cache, allocator, writes, _, commits, _ = swap
    req = running(cache)
    original_input, original_output = tuple(req.origin_input_ids), tuple(req.output_ids)
    parameters, row = req.sampling_params, req.kv.req_pool_idx
    available = allocator.available_size()
    result = actor.prepare([req], running_batch=NS(reqs=[req]))
    assert result["unique_checkpoint_tokens"] == 5
    assert not result["device_capacity_released"]
    assert sum(len(operation.device_indices) for operation in writes) == 5
    assert allocator.available_size() == available
    assert actor.commit() is None
    assert req.kv.req_pool_idx == row
    assert not actor.paused and not commits
    ack(cache)
    result = actor.commit()
    assert result["status"] == "paused" and result["requests"] == (req,)
    assert result["demoted_full_tokens"] == result["allocator_available_increase"] == 5
    assert req.kv.req_pool_idx is None and req.is_retracted
    assert tuple(req.origin_input_ids) == original_input
    assert tuple(req.output_ids) == original_output
    assert req.sampling_params is parameters
    assert req.retraction_count == 1
    assert actor.status()["paused_host_pins"] == 1
    assert dict(commits[0].num_tokens_by_pool) == {"kv": 5}


def test_shared_prefix_is_backed_once_and_a_live_peer_keeps_its_device_pages(swap):
    actor, cache, allocator, writes, _, _, _ = swap
    peer = running(cache, "peer", output=(90, 91))
    peer._refresh_fill_ids()
    peer.set_extend_range(0, peer.effective_kv_committed_len())
    cache.cache_unfinished_req(peer, chunked=True)
    victim = running(cache, "victim")
    actor.prepare([victim], running_batch=NS(reqs=[peer, victim]))
    before = allocator.available_size()
    ack(cache)
    result = actor.commit()
    assert result["demoted_full_tokens"] == result["allocator_available_increase"] == 1
    assert allocator.available_size() == before + 1
    assert peer.kv.holds_kv and not peer.is_retracted
    assert sum(len(operation.device_indices) for operation in writes) == 5
    assert cache.tree_core.node_by_id(peer.last_node).component_data[0].value is not None


def test_multiple_victims_share_one_native_aggregate_and_release_unique_capacity(swap):
    actor, cache, allocator, writes, _, _, _ = swap
    first, second = running(cache, "a"), running(cache, "b", output=(90, 91))
    prepared = actor.prepare([first, second], running_batch=NS(reqs=[first, second]))
    assert prepared["unique_checkpoint_tokens"] == 6
    assert sum(len(operation.device_indices) for operation in writes) == 6
    cache.cache_controller.start_writing.assert_called_once()
    assert len(cache.cache_controller.ack_write_queue) == 1
    before = allocator.available_size()
    ack(cache)
    result = actor.commit()
    assert result["allocator_available_increase"] == result["demoted_full_tokens"] == 6
    assert allocator.available_size() == before + 6
    assert len(actor.paused) == 2


def test_a_failed_host_backup_keeps_the_original_running_allocation(swap):
    actor, cache, allocator, _, _, _, _ = swap
    req = running(cache)
    cache.cache_controller.write = lambda *_args, **_kwargs: None
    row, available = req.kv.req_pool_idx, allocator.available_size()
    actor.prepare([req], running_batch=NS(reqs=[req]))
    result = actor.commit()
    assert result["status"] == "backup_declined"
    assert result["allocator_available_increase"] == 0
    assert req.kv.req_pool_idx == row and not req.is_retracted
    assert allocator.available_size() == available
    actor.cancel_preparation()
    assert not actor.pending and not actor.paused


@pytest.mark.parametrize("boundary", ("prepare", "commit"))
def test_live_pipeline_must_drain_before_native_kv_mutations(swap, boundary):
    actor, cache, allocator, _, _, _, safe = swap
    req = running(cache)
    if boundary == "commit":
        actor.prepare([req], running_batch=NS(reqs=[req]))
        ack(cache)
    before = allocator.available_size()
    safe.value = False
    with pytest.raises(ValueError, match="pipeline drained"):
        actor.prepare([req], running_batch=NS(reqs=[req])) if boundary == "prepare" else actor.commit()
    assert req.kv.holds_kv and allocator.available_size() == before


@pytest.mark.parametrize("mutation", ("output", "attempt", "sampler", "context"))
def test_changed_request_cannot_release_capacity_after_backup(swap, mutation):
    actor, cache, allocator, _, _, _, _ = swap
    req = running(cache)
    actor.prepare([req], running_batch=NS(reqs=[req]))
    before = allocator.available_size()
    ack(cache)
    if mutation == "output":
        req.output_ids.append(99)
    elif mutation == "attempt":
        req.cache_request_handle = replace(
            req.cache_request_handle, attempt_id=req.cache_request_handle.attempt_id + 1,
        )
    elif mutation == "sampler":
        req.sampling_params = object()
    else:
        req.beliefkv_metadata["context_epoch"] += 1
    with pytest.raises(ValueError, match="changed"):
        actor.commit()
    assert req.kv.holds_kv and allocator.available_size() == before


def test_a_queued_request_cannot_be_selected_as_a_running_victim(swap):
    actor, cache, allocator, writes, _, _, _ = swap
    req = running(cache)
    before = allocator.available_size()
    with pytest.raises(ValueError, match="actual running"):
        actor.prepare([req], running_batch=NS(reqs=[]))
    assert not writes and allocator.available_size() == before
    assert not actor.pending and not actor.paused


def test_native_h2d_keeps_its_own_host_lock_after_resumed_agent_consumes_the_pin(swap):
    actor, cache, _, _, loads, _, _ = swap
    req = running(cache)
    actor.prepare([req], running_batch=NS(reqs=[req]))
    ack(cache)
    actor.commit()
    pin = actor.paused[req.rid]
    leaf = cache.tree_core.node_by_id(pin.leaf)
    full = leaf.component_data[0]
    assert full.host_lock_ref == 1 and full.value is None
    with pytest.raises(ValueError, match="actual native admission"):
        actor.resumed(req)
    assert full.host_lock_ref == 1
    cache.load_back_threshold = 1
    assert cache.load_back(leaf.id)
    cache.cache_controller.start_loading()
    assert len(loads) == 1 and leaf.load_back_pending_id is not None
    assert full.host_lock_ref == 2
    assert cache.req_to_token_pool.alloc([req]) is not None
    with pytest.raises(ValueError, match="prefix was not restored"):
        actor.resumed(req)
    req.init_next_round_input(cache)
    req.lock_receipt = cache.inc_lock_ref(req.last_node).to_dec_params()
    req.kv.kv_allocated_len = req.kv.kv_committed_len = len(req.prefix_indices)
    assert actor.resumed(req)
    assert full.host_lock_ref == 1 and not actor.paused
    cache.loading_check(finish_count=1)
    assert full.host_lock_ref == 0
    assert not actor.resumed(req)


def test_each_paused_request_owns_one_pin_when_its_checkpoint_is_shared(swap):
    actor, cache, _, writes, _, _, _ = swap
    first, second = running(cache, "a"), running(cache, "b")
    prepared = actor.prepare([first, second], running_batch=NS(reqs=[first, second]))
    assert prepared["unique_checkpoint_tokens"] == 5
    assert sum(len(operation.device_indices) for operation in writes) == 5
    ack(cache)
    assert actor.commit()["demoted_full_tokens"] == 5
    pin = actor.paused["a"]
    assert actor.paused["b"].leaf == pin.leaf
    full = cache.tree_core.node_by_id(pin.leaf).component_data[0]
    assert full.host_lock_ref == 2
    assert actor.release("a")
    assert full.host_lock_ref == 1 and cache.evict_host(5) == 0
    assert not actor.release("a")
    assert actor.release("b")
    assert full.host_lock_ref == 0 and not actor.paused


def test_cancelled_preparation_leaves_native_dma_and_running_request_locks_intact(swap):
    actor, cache, allocator, _, _, _, _ = swap
    req = running(cache)
    before = allocator.available_size()
    actor.prepare([req], running_batch=NS(reqs=[req]))
    leaf = cache.tree_core.node_by_id(req.last_node)
    assert leaf.component_data[0].lock_ref == 2
    actor.cancel_preparation()
    assert actor.commit() is None
    assert leaf.component_data[0].lock_ref == 2
    assert req.kv.holds_kv and allocator.available_size() == before
    ack(cache)
    assert leaf.component_data[0].lock_ref == 1
    assert req.kv.holds_kv and allocator.available_size() == before


def test_abort_can_cancel_preparation_without_releasing_live_gpu_or_dma_ownership(swap):
    from sglang.srt.managers.schedule_batch import FINISH_ABORT

    actor, cache, allocator, _, _, _, safe = swap
    req = running(cache)
    before = allocator.available_size()
    actor.prepare([req], running_batch=NS(reqs=[req]))
    leaf = cache.tree_core.node_by_id(req.last_node)
    assert leaf.component_data[0].lock_ref == 2
    req.output_ids.append(99)
    req.finished_reason = FINISH_ABORT("cancel while backup is outstanding")
    safe.value = False
    actor.cancel_preparation()
    assert not actor.pending and not actor._preparation and not actor.paused
    assert leaf.component_data[0].lock_ref == 2
    assert req.kv.holds_kv and allocator.available_size() == before
    ack(cache)
    assert leaf.component_data[0].lock_ref == 1
    assert req.kv.holds_kv and allocator.available_size() == before


def test_cancelling_incremental_backup_releases_only_its_settled_host_protection(swap):
    from sglang.srt.mem_cache.unified_cache.cache_action import BackupKV

    actor, cache, allocator, _, _, _, safe = swap
    req = running(cache)
    req._refresh_fill_ids()
    req.set_extend_range(0, 4)
    cache.cache_unfinished_req(req, chunked=True)
    ancestor = cache.tree_core.node_by_id(req.last_node)
    cache._execute_and_commit_kv_backup(
        BackupKV([ancestor.id]), beliefkv_include_mamba=False, beliefkv_defer_writing=True,
    )
    cache.cache_controller.start_writing()
    ack(cache)
    actor.prepare([req], running_batch=NS(reqs=[req]))
    leaf = cache.tree_core.node_by_id(req.last_node)
    before = allocator.available_size()
    assert ancestor.component_data[0].host_lock_ref == 1
    safe.value = False
    actor.cancel_preparation()
    assert ancestor.component_data[0].host_lock_ref == 0
    assert leaf.component_data[0].lock_ref == 2
    assert req.kv.holds_kv and allocator.available_size() == before
    ack(cache)
    assert leaf.component_data[0].lock_ref == 1
    assert req.kv.holds_kv and allocator.available_size() == before


def test_native_split_during_backup_preserves_checkpoint_and_ack_identity(swap):
    from test_beliefkv_terminal_capacity import insert_session

    actor, cache, allocator, _, _, _, _ = swap
    req = running(cache)
    actor.prepare([req], running_batch=NS(reqs=[req]))
    original_leaf = cache.tree_core.node_by_id(req.last_node)
    other, _ = insert_session(cache, [1, 2, 90, 91], "other")
    assert original_leaf.parent is other.parent
    assert len(original_leaf.key) == 3
    assert actor.commit() is None
    ack(cache)
    before = allocator.available_size()
    result = actor.commit()
    assert result["demoted_full_tokens"] == result["allocator_available_increase"] == 3
    assert allocator.available_size() == before + 3
    assert original_leaf.component_data[0].value is None
    assert original_leaf.parent.component_data[0].value is not None
    assert other.component_data[0].value is not None


def test_paused_full_host_path_survives_native_splits_and_host_pressure(swap):
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    actor, cache, _, _, _, _, _ = swap
    req = running(cache)
    actor.prepare([req], running_batch=NS(reqs=[req]))
    ack(cache)
    actor.commit()
    pin = actor.paused[req.rid]
    cache.match_prefix(MatchPrefixParams(key=RadixKey(array("q", [1, 2]))))
    leaf = cache.tree_core.node_by_id(pin.leaf)
    assert len(leaf.key) == 3 and len(leaf.parent.key) == 2
    assert leaf.component_data[0].host_lock_ref == 1
    assert leaf.parent.component_data[0].host_lock_ref == 1
    freed = []
    cache.components[0]._full_kv_pool_host = cache.host_pool_group.anchor_entry.host_pool
    cache.host_pool_group.free = lambda indices, **_: freed.append(len(indices))
    assert cache.evict_host(64) == 0 and not freed
    actor.transfers.clock = lambda: 10**12
    with actor._authority():
        assert actor.transfers.expire_host_leases() == 0
    assert cache.evict_host(64) == 0
    assert actor.release(req.rid)
    assert cache.evict_host(64) == 5 and sum(freed) == 5


def test_incremental_swap_protects_old_host_prefix_before_enqueuing_new_tail(swap):
    from sglang.srt.mem_cache.unified_cache.cache_action import BackupKV

    actor, cache, _, writes, _, _, _ = swap
    req = running(cache)
    req._refresh_fill_ids()
    req.set_extend_range(0, 4)
    cache.cache_unfinished_req(req, chunked=True)
    ancestor = cache.tree_core.node_by_id(req.last_node)
    cache._execute_and_commit_kv_backup(
        BackupKV([ancestor.id]), beliefkv_include_mamba=False, beliefkv_defer_writing=True,
    )
    cache.cache_controller.start_writing()
    ack(cache)
    write = cache.cache_controller.write

    def increment(indices, **kwargs):
        assert ancestor.component_data[0].host_lock_ref > 0
        assert cache.evict_host(64) == 0
        return write(indices, **kwargs)

    cache.cache_controller.write = increment
    actor.prepare([req], running_batch=NS(reqs=[req]))
    assert [len(op.device_indices) for op in writes] == [4, 1]
    ack(cache)
    assert actor.commit()["demoted_full_tokens"] == 5
    assert ancestor.component_data[0].host_lock_ref == 1
    assert cache.evict_host(64) == 0


def test_host_handoff_requires_the_resumed_consumers_real_native_receipt(swap):
    actor, cache, _, _, _, _, _ = swap
    req = running(cache)
    actor.prepare([req], running_batch=NS(reqs=[req]))
    ack(cache)
    actor.commit()
    leaf = cache.tree_core.node_by_id(actor.paused[req.rid].leaf)
    cache.load_back_threshold = 1
    assert cache.load_back(leaf.id)
    cache.cache_controller.start_loading()
    req.init_next_round_input(cache)
    assert cache.req_to_token_pool.alloc([req]) is not None
    req.kv.kv_allocated_len = req.kv.kv_committed_len = len(req.prefix_indices)
    with pytest.raises(ValueError, match="actual native consumer lock"):
        actor.resumed(req)
    assert req.rid in actor.paused
    req.lock_receipt = cache.inc_lock_ref(req.last_node).to_dec_params()
    assert actor.resumed(req)
    cache.loading_check(finish_count=1)
