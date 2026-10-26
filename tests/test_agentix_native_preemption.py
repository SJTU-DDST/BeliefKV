from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.policy.agentix import AgentixScheduler
from beliefkv.runtime.agentix_executor import AgentixWorkerExecution
from beliefkv.runtime.agentix_lifecycle import AgentixCallLifecycle, AgentixNativePreemption
from beliefkv.runtime.sglang_v0520_admission import _request_key
from tests.test_agentix import configuration
from tests.test_agentix_executor import Device
from tests.test_agentix_lifecycle import request
from tests.test_agentix_swap import ack, running
from tests.test_agentix_worker_execution import Worker
from tests.test_full_only_native_lifecycle import full_native


def native_batch(cache, requests):
    import torch
    from sglang.srt.managers.schedule_batch import ScheduleBatch
    from sglang.srt.model_executor.forward_batch_info import ForwardMode

    result = ScheduleBatch(
        reqs=list(requests), tree_cache=cache, req_to_token_pool=cache.req_to_token_pool,
        token_to_kv_pool_allocator=cache.token_to_kv_pool_allocator,
        model_config=NS(is_encoder_decoder=False), device="cpu",
        spec_algorithm=NS(is_none=lambda: True), forward_mode=ForwardMode.DECODE,
    )
    result.req_pool_indices = torch.tensor([req.kv.req_pool_idx for req in requests])
    result.req_pool_indices_cpu = result.req_pool_indices.clone()
    result.seq_lens = torch.tensor([req.seqlen - 1 for req in requests])
    result.orig_seq_lens = result.seq_lens.clone()
    result.seq_lens_cpu = result.seq_lens.clone()
    result.sampling_info = NS(filter_batch=Mock())
    return result


@pytest.fixture
def native(full_native):
    from sglang.srt.server_args import ServerArgs, set_global_server_args_for_scheduler
    from beliefkv.runtime.agentix_swap import AgentixNativeSwap

    set_global_server_args_for_scheduler(ServerArgs(model_path="dummy"))
    cache, allocator, writes, loads, commits = full_native
    policy, device, stream, clock = AgentixScheduler(configuration()), Device(), object(), NS(now=0.)
    device.current_stream = lambda: stream
    execution = AgentixWorkerExecution(
        policy, device=device, compute_stream=stream, clock_ms=lambda: clock.now,
    )
    worker = Worker(device)
    execution.install(worker)
    safe = NS(value=True)
    actor = AgentixNativeSwap(cache, drained=lambda: safe.value)
    calls = AgentixCallLifecycle(policy, execution, actor, clock_ms=lambda: clock.now)
    queued = []

    def enqueue(req, *, is_retracted):
        calls.requeued([req], is_retracted=is_retracted)
        queued.append((req, is_retracted))

    controller = AgentixNativePreemption(
        calls, drained=lambda: safe.value, enqueue=enqueue,
    )
    yield NS(
        cache=cache, allocator=allocator, writes=writes, loads=loads, commits=commits,
        policy=policy, device=device, clock=clock, worker=worker, safe=safe,
        actor=actor, calls=calls, controller=controller, queued=queued,
    )
    device.completed_position = device.position
    clock.now = max(clock.now, device.position)
    calls.poll()
    controller.cancel()
    execution.close()
    actor.close()


def register(state, *requests):
    for req in requests:
        state.calls.registered(req, _request_key(req))


def test_selected_native_preemption_waits_for_ack_filters_batch_and_preserves_call(native):
    state = native
    victim, peer = running(state.cache, "victim"), running(state.cache, "peer", output=(90, 91))
    for req in (peer, victim):
        req._refresh_fill_ids()
        req.set_extend_range(0, req.effective_kv_committed_len())
        state.cache.cache_unfinished_req(req, chunked=True)
    register(state, victim, peer)
    batch = native_batch(state.cache, [victim, peer])
    state.worker.forward_batch_generation(batch)
    state.device.completed_position, state.clock.now = 3., 5.
    state.calls.poll()
    original = state.calls.requests[victim.rid]
    call = state.policy.calls[victim.rid]
    quantum, before, row = call.quantum_left_ms, state.allocator.available_size(), peer.kv.req_pool_idx
    result = state.controller.begin([peer.rid], running_batch=batch)
    assert result["status"] == "waiting_native_ack"
    assert state.allocator.available_size() == before
    assert victim.kv.holds_kv and not state.queued
    assert state.policy.executing == set()
    assert state.policy.calls[victim.rid].waiting_since_ms == 5.
    ack(state.cache)
    state.clock.now = 7.
    result = state.controller.advance(running_batch=batch)
    assert result["status"] == "paused" and result["requests"] == (victim,)
    assert result["allocator_available_increase"] == result["demoted_full_tokens"] == 1
    assert batch.reqs == [peer] and batch.req_pool_indices.tolist() == [row]
    assert batch.req_pool_indices_cpu.tolist() == [row]
    batch.sampling_info.filter_batch.assert_called_once()
    assert state.queued == [(victim, True)]
    assert not victim.kv.holds_kv and peer.kv.holds_kv
    assert state.calls.requests[victim.rid] is original
    assert original.arrived_ms == 0.
    assert state.policy.calls[victim.rid] is call
    assert call.total_model_ms == 3. and call.quantum_left_ms == quantum
    assert state.policy.counts["arrived"] == 2 and state.policy.counts["completed"] == 0
    state.worker.forward_batch_generation(batch)
    assert state.policy.calls[peer.rid].total_wait_ms == 2.
    assert call.waiting_since_ms == 5.


@pytest.mark.parametrize("boundary", ("native_pipeline", "executor"))
def test_preemption_never_checkpoints_or_releases_before_both_drains(native, boundary):
    state = native
    victim = running(state.cache)
    register(state, victim)
    batch = native_batch(state.cache, [victim])
    state.worker.forward_batch_generation(batch)
    if boundary == "native_pipeline":
        state.device.completed_position = 3.
        state.safe.value = False
    before = state.allocator.available_size()
    result = state.controller.begin((), running_batch=batch)
    assert result["status"] == "drain_required"
    assert not state.writes and not state.actor.pending and state.controller.selected is None
    assert state.allocator.available_size() == before and victim.kv.holds_kv
    state.safe.value, state.device.completed_position, state.clock.now = True, 3., 4.
    assert state.controller.begin((), running_batch=batch)["status"] == "waiting_native_ack"
    ack(state.cache)
    assert state.controller.advance(running_batch=batch)["status"] == "paused"
    assert batch.is_empty() and not victim.kv.holds_kv


def test_committing_the_backup_still_requires_the_live_native_drain(native):
    state = native
    victim = running(state.cache)
    register(state, victim)
    batch = native_batch(state.cache, [victim])
    assert state.controller.begin((), running_batch=batch)["status"] == "waiting_native_ack"
    ack(state.cache)
    before = state.allocator.available_size()
    state.safe.value = False
    result = state.controller.advance(running_batch=batch)
    assert result["status"] == "drain_required"
    assert state.allocator.available_size() == before and not state.queued
    assert batch.reqs == [victim] and victim.kv.holds_kv
    state.safe.value = True
    assert state.controller.advance(running_batch=batch)["status"] == "paused"


def test_real_native_resumption_hands_off_host_ownership_without_reregistering(native):
    state = native
    victim = running(state.cache)
    register(state, victim)
    batch = native_batch(state.cache, [victim])
    state.controller.begin((), running_batch=batch)
    ack(state.cache)
    state.controller.advance(running_batch=batch)
    pin = state.actor.paused[victim.rid]
    leaf = state.cache.tree_core.node_by_id(pin.leaf)
    state.cache.load_back_threshold = 1
    assert state.cache.load_back(leaf.id)
    state.cache.cache_controller.start_loading()
    victim.init_next_round_input(state.cache)
    assert state.cache.req_to_token_pool.alloc([victim]) is not None
    victim.lock_receipt = state.cache.inc_lock_ref(victim.last_node).to_dec_params()
    count = len(victim.prefix_indices)
    victim.kv.kv_allocated_len = victim.kv.kv_committed_len = count
    state.calls.selected(NS(reqs=[victim]))
    assert not state.actor.paused
    assert leaf.component_data[0].host_lock_ref == 1
    state.cache.loading_check(finish_count=1)
    assert leaf.component_data[0].host_lock_ref == 0
    assert state.policy.counts["arrived"] == 1
    assert state.calls.requests[victim.rid].arrived_ms == 0.


def test_declined_host_backup_preserves_original_batch_and_native_allocation(native):
    state = native
    victim = running(state.cache)
    register(state, victim)
    batch = native_batch(state.cache, [victim])
    state.cache.cache_controller.write = lambda *_args, **_kwargs: None
    before = state.allocator.available_size()
    result = state.controller.begin((), running_batch=batch)
    assert result["status"] == "backup_declined"
    assert state.allocator.available_size() == before
    assert victim.kv.holds_kv and batch.reqs == [victim]
    assert not state.actor.pending and not state.actor.paused and not state.queued
    assert state.controller.selected is None


@pytest.mark.parametrize("abort_victim", (False, True))
def test_abort_cancels_pending_selection_without_freeing_a_live_native_request(native, abort_victim):
    state = native
    victim, successor = running(state.cache, "victim"), request("successor")
    register(state, victim, successor)
    batch = native_batch(state.cache, [victim])
    before = state.allocator.available_size()
    assert state.controller.begin([successor.rid], running_batch=batch)["status"] == "waiting_native_ack"
    state.calls.aborted(victim.rid if abort_victim else successor.rid)
    assert state.controller.advance(running_batch=batch)["status"] == "cancelled"
    assert not state.actor.pending and not state.actor.paused and not state.queued
    assert state.allocator.available_size() == before and victim.kv.holds_kv
    leaf = state.cache.tree_core.node_by_id(victim.last_node)
    assert leaf.component_data[0].lock_ref == 2
    ack(state.cache)
    assert leaf.component_data[0].lock_ref == 1


def test_lost_running_victim_is_rejected_before_physical_commit(native):
    state = native
    victim = running(state.cache)
    register(state, victim)
    batch = native_batch(state.cache, [victim])
    state.controller.begin((), running_batch=batch)
    ack(state.cache)
    before = state.allocator.available_size()
    batch.reqs.clear()
    with pytest.raises(ValueError, match="actual running victim"):
        state.controller.advance(running_batch=batch)
    assert state.allocator.available_size() == before and victim.kv.holds_kv
    assert not state.actor.paused and not state.queued
