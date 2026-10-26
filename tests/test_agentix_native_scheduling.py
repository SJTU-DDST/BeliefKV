from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.agentix_lifecycle import AgentixNativeScheduling
from tests.test_agentix_lifecycle import request
from tests.test_agentix_native_preemption import native, native_batch, register
from tests.test_agentix_swap import ack, running
from tests.test_full_only_native_lifecycle import full_native


class Scheduler:
    def __init__(self, state):
        self.state = state
        self.enable_overlap, self.result_queue, self.chunked_req = True, [], None
        self.waiting_queue = []
        self.forward_stream = state.calls.execution.compute_stream
        self.schedule_stream = object()
        self.ps = NS(tp_size=1, pp_size=1)
        self.is_generation, self.spec_algorithm = True, NS(is_none=lambda: True)
        self.dllm_config, self.enable_pdmux = None, False
        self.device_module, self.model_worker, self.tp_worker = state.device, state.worker, state.worker
        self.original_calls, self.original_defers = 0, False

    def _beliefkv_before_batch_selection(self, running_batch, last_batch):
        self.original_calls += 1
        return self.original_defers

    def _add_request_to_queue(self, req, *, is_retracted):
        self.state.calls.requeued([req], is_retracted=is_retracted)
        self.waiting_queue.append(req)
        self.state.queued.append((req, is_retracted))


@pytest.fixture
def scheduling(native):
    state = native
    state.calls.execution.close()
    scheduler = Scheduler(state)
    state.calls.execution.install_scheduler(scheduler)
    resident = set()
    controller = AgentixNativeScheduling(
        state.calls, scheduler, resident=lambda req: req.rid in resident,
        can_fit=lambda _req, _chosen: True, maximum_requests=1,
    )
    controller.install()
    yield state, scheduler, controller, resident
    state.device.completed_position = state.device.position
    state.clock.now = max(state.clock.now, state.device.position)
    state.calls.poll()
    controller.close()


def test_native_hook_demotes_preempts_and_requeues_only_after_executor_and_backup_ack(scheduling):
    state, scheduler, controller, resident = scheduling
    active, waiting = running(state.cache, "active"), request("waiting")
    register(state, active, waiting)
    resident.add(active.rid)
    scheduler.waiting_queue = [waiting]
    batch = native_batch(state.cache, [active])
    before_selection = scheduler._beliefkv_before_batch_selection
    assert before_selection(batch, None) is False
    assert controller.selected_order == (active.rid,)
    assert not controller.prefill_plan(scheduler.waiting_queue, semantic_revision=0).prioritized
    for index in range(4):
        state.worker.forward_batch_generation(batch)
        if index < 3:
            state.device.completed_position = state.device.position
            state.clock.now += 3.
            state.calls.poll()
            assert before_selection(batch, None) is False
    scheduler.result_queue.append(object())
    state.device.completed_position, state.clock.now = state.device.position, 12.
    assert before_selection(batch, batch) is True
    assert not state.writes and active.kv.holds_kv
    assert state.policy.calls[active.rid].queue == 0
    state.device.completed_position, state.clock.now = state.device.position, 15.
    scheduler.result_queue.clear()
    assert before_selection(batch, None) is True
    assert controller.selected_order == (waiting.rid,)
    assert state.policy.calls[active.rid].queue == 1
    assert state.policy.calls[active.rid].total_model_ms == 12.
    assert state.policy.counts["full_schedules"] == 2
    assert not state.queued and active.kv.holds_kv
    ack(state.cache)
    assert before_selection(batch, None) is False
    assert batch.is_empty() and not active.kv.holds_kv
    assert state.queued == [(active, True)]
    plan = controller.prefill_plan(scheduler.waiting_queue, semantic_revision=7)
    assert tuple(key.request_id for key in plan.prioritized) == (waiting.rid,)
    assert plan.semantic_revision == 7
    assert controller.counts["retained_without_queue_scan"] == 3
    assert state.policy.counts["arrived"] == 2 and state.policy.counts["completed"] == 0


def test_chunked_native_prefill_defers_full_schedule_without_consuming_the_epoch(scheduling):
    state, scheduler, controller, _ = scheduling
    active = running(state.cache)
    register(state, active)
    batch = native_batch(state.cache, [active])
    scheduler.chunked_req = active
    assert scheduler._beliefkv_before_batch_selection(batch, None) is False
    assert state.policy.counts["full_schedules"] == 0 and not state.writes
    assert controller.counts["full_schedule_deferred_for_chunk"] == 1
    scheduler.chunked_req = None
    assert scheduler._beliefkv_before_batch_selection(batch, None) is False
    assert state.policy.counts["full_schedules"] == 1


@pytest.mark.parametrize("resident_successor", (False, True))
def test_between_schedule_epochs_only_native_resident_waiting_calls_can_refill(scheduling, resident_successor):
    state, scheduler, controller, resident = scheduling
    active = running(state.cache)
    register(state, active)
    batch = native_batch(state.cache, [active])
    controller.maximum_requests = 2
    assert scheduler._beliefkv_before_batch_selection(batch, None) is False
    successor = request("successor")
    register(state, successor)
    scheduler.waiting_queue.append(successor)
    if resident_successor:
        resident.add(successor.rid)
    assert scheduler._beliefkv_before_batch_selection(batch, None) is False
    expected = (active.rid, successor.rid) if resident_successor else (active.rid,)
    assert controller.selected_order == expected
    assert state.policy.counts["full_schedules"] == 1
    assert controller.counts["resident_refill_selections"] == 1
    assert not state.writes and not state.queued


def test_native_host_backup_failure_is_explicit_and_keeps_the_running_allocation(scheduling):
    state, scheduler, controller, resident = scheduling
    active, waiting = running(state.cache), request("waiting")
    register(state, active, waiting)
    scheduler.waiting_queue.append(waiting)
    batch = native_batch(state.cache, [active])
    resident.add(active.rid)
    assert scheduler._beliefkv_before_batch_selection(batch, None) is False
    state.policy.calls[active.rid].quantum_left_ms = 0.
    state.policy.decode_completed(4)
    state.cache.cache_controller.write = lambda *_args, **_kwargs: None
    before = state.allocator.available_size()
    with pytest.raises(RuntimeError, match="backup declined"):
        scheduler._beliefkv_before_batch_selection(batch, None)
    assert controller.counts["backup_declined"] == 1
    assert state.allocator.available_size() == before and active.kv.holds_kv
    assert not state.queued and not state.actor.paused


def test_real_host_capacity_refusal_retains_running_work_then_retries_after_space_changes(
    scheduling, monkeypatch,
):
    state, scheduler, controller, resident = scheduling
    active, waiting = running(state.cache), request("waiting")
    register(state, active, waiting)
    scheduler.waiting_queue.append(waiting)
    batch = native_batch(state.cache, [active])
    resident.add(active.rid)
    assert scheduler._beliefkv_before_batch_selection(batch, None) is False
    state.policy.calls[active.rid].quantum_left_ms = 0.
    state.policy.decode_completed(4)
    free = NS(tokens=0)
    monkeypatch.setattr(
        state.cache.cache_controller.mem_pool_host, "available_size", lambda: free.tokens,
    )
    monkeypatch.setattr(state.cache, "evict_host", lambda _tokens: 0)
    before = state.allocator.available_size()
    assert scheduler._beliefkv_before_batch_selection(batch, None) is False
    assert controller.status()["host_backpressure"]
    assert controller.selected_order == (active.rid,)
    assert state.policy.counts["resource_deferred_selections"] == 1
    assert state.allocator.available_size() == before and active.kv.holds_kv
    assert not state.writes and not state.queued
    schedules = state.policy.counts["full_schedules"]
    for _ in range(8):
        assert scheduler._beliefkv_before_batch_selection(batch, None) is False
    assert state.policy.counts["full_schedules"] == schedules
    assert controller.counts["host_backpressure_retained_without_queue_scan"] == 8
    free.tokens = 2_000_000
    assert scheduler._beliefkv_before_batch_selection(batch, None) is True
    assert not controller.status()["host_backpressure"]
    assert controller.selected_order == (waiting.rid,)
    assert active.kv.holds_kv and len(state.writes) == 1
    ack(state.cache)
    assert scheduler._beliefkv_before_batch_selection(batch, None) is False
    assert batch.is_empty() and not active.kv.holds_kv
    assert state.queued == [(active, True)]
    assert state.policy.counts["arrived"] == 2 and state.policy.counts["completed"] == 0


def test_original_native_deferral_still_precedes_paper_selection(scheduling):
    state, scheduler, controller, _ = scheduling
    active = running(state.cache)
    register(state, active)
    batch = native_batch(state.cache, [active])
    scheduler.original_defers = True
    assert scheduler._beliefkv_before_batch_selection(batch, None) is True
    assert scheduler.original_calls == 1
    assert state.policy.counts["full_schedules"] == 0
    assert not controller.selected_order and not state.writes


def test_native_hooks_restore_cleanly_and_keep_outside_swap_mutations_forbidden(scheduling):
    state, scheduler, controller, _ = scheduling
    active = running(state.cache)
    register(state, active)
    batch = native_batch(state.cache, [active])
    with pytest.raises(ValueError, match="pipeline drained"):
        state.actor.prepare([active], running_batch=batch)
    controller.close()
    assert "_beliefkv_before_batch_selection" not in vars(scheduler)
    assert scheduler._beliefkv_before_batch_selection.__func__ is Scheduler._beliefkv_before_batch_selection
    assert state.actor.drained() is True
    with pytest.raises(ValueError, match="not installed"):
        controller.prefill_plan((), semantic_revision=0)
