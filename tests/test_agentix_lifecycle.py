from array import array
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from beliefkv.policy.agentix import AgentixScheduler
from beliefkv.runtime.agentix_executor import AgentixWorkerExecution
from beliefkv.runtime.agentix_lifecycle import AgentixCallLifecycle
from beliefkv.runtime.sglang_v0520_admission import _request_key
from tests.test_agentix import configuration
from tests.test_agentix_executor import Device
from tests.test_agentix_worker_execution import Worker, batch


class Swap:
    pending = ()

    def __init__(self):
        self.released, self.resumptions = [], []

    def release(self, request):
        self.released.append(request)
        return False

    def resumed(self, req):
        self.resumptions.append(req.rid)
        return False

    def cancel_preparation(self):
        self.pending = ()


def request(identity, *, root="workflow"):
    return NS(
        rid=identity, origin_input_ids=array("q", (1, 2)), output_ids=array("q", (3,)),
        sampling_params=object(), extra_key=root, cache_salt=root,
        session_id=None, session_generation=None,
        beliefkv_metadata={
            "root_workflow_id": root, "invocation_id": identity,
            "context_id": identity + "-context", "context_epoch": 0,
        },
        cache_request_handle=NS(attempt_id=0), finished=lambda: False,
    )


def launch(worker, *requests):
    native = batch(*(req.rid for req in requests))
    native.reqs = list(requests)
    worker.forward_batch_generation(native)
    return native


@pytest.fixture
def lifecycle():
    policy, device, stream, clock = AgentixScheduler(configuration()), Device(), object(), NS(now=0.)
    device.current_stream = lambda: stream
    execution = AgentixWorkerExecution(
        policy, device=device, compute_stream=stream, clock_ms=lambda: clock.now,
    )
    worker = Worker(device)
    execution.install(worker)
    calls = AgentixCallLifecycle(policy, execution, Swap(), clock_ms=lambda: clock.now)
    yield calls, worker, device, clock
    device.completed_position = device.position
    clock.now = max(clock.now, device.position)
    calls.poll()
    execution.close()


def register(calls, *requests):
    for req in requests:
        calls.registered(req, _request_key(req))


def test_native_completion_waits_for_every_overlapped_forward_of_that_call(lifecycle):
    calls, worker, device, clock = lifecycle
    first = request("first")
    register(calls, first)
    native = launch(worker, first)
    launch(worker, first)
    first.finished = lambda: True
    device.completed_position, clock.now = 3., 4.
    calls.completed(native, ("first",))
    assert calls.terminals == {"first": "complete"}
    assert calls.policy.calls["first"].total_model_ms == 3.
    assert calls.policy.service.service_ms["workflow"] == 0.
    with pytest.raises(ValueError, match="terminal"):
        calls.selected(native)
    device.completed_position, clock.now = 6., 6.
    calls.poll()
    assert not calls.requests and not calls.terminals
    assert calls.policy.service.service_ms["workflow"] == 6.
    assert calls.swap.released == ["first"]
    calls.completed(native, ("first",))
    assert calls.counts["completed"] == calls.counts["duplicate_completed_results"] == 1


def test_same_program_arrival_inherits_settled_service_and_keeps_its_real_wait_age(lifecycle):
    calls, worker, device, clock = lifecycle
    first, successor, other = request("first"), request("successor"), request("other", root="other")
    register(calls, first)
    native = launch(worker, first)
    launch(worker, first)
    first.finished = lambda: True
    device.completed_position, clock.now = 3., 4.
    calls.completed(native, ("first",))
    register(calls, successor, other)
    assert successor.rid in calls.awaiting_registration
    assert successor.rid not in calls.policy.calls
    assert other.rid in calls.policy.calls
    with pytest.raises(ValueError, match="terminal"):
        calls.selected(NS(reqs=[successor]))
    device.completed_position, clock.now = 6., 8.
    calls.poll()
    assert not calls.awaiting_registration
    assert calls.policy.service.calls["successor"].inherited_service_ms == 6.
    assert calls.policy.calls["successor"].waiting_since_ms == 4.
    launch(worker, successor)
    assert calls.policy.calls["successor"].total_wait_ms == 4.


def test_abort_defers_call_removal_until_its_live_gpu_record_is_accounted(lifecycle):
    calls, worker, device, clock = lifecycle
    req = request("first")
    register(calls, req)
    launch(worker, req)
    clock.now = 1.
    calls.aborted(req.rid)
    assert calls.terminals == {"first": "cancel"}
    assert req.rid in calls.policy.calls
    device.completed_position, clock.now = 3., 3.
    calls.poll()
    assert not calls.requests and not calls.policy.calls
    assert calls.policy.counts["cancelled"] == 1
    assert calls.policy.counts["completed"] == 0
    assert calls.policy.service.service_ms["workflow"] == 0.


def test_abort_of_an_unregistered_successor_does_not_create_or_complete_fake_service(lifecycle):
    calls, worker, device, clock = lifecycle
    first, successor = request("first"), request("successor")
    register(calls, first)
    native = launch(worker, first)
    first.finished = lambda: True
    clock.now = 1.
    calls.completed(native, ("first",))
    register(calls, successor)
    assert successor.rid in calls.awaiting_registration
    calls.aborted(successor.rid)
    assert successor.rid not in calls.requests
    assert calls.policy.counts["arrived"] == 1
    assert calls.counts["cancelled"] == 1
    device.completed_position, clock.now = 3., 3.
    calls.poll()
    assert calls.policy.counts["arrived"] == 1
    assert calls.policy.service.service_ms["workflow"] == 3.


def test_explicit_workflow_end_waits_for_gpu_and_cannot_reopen_the_program(lifecycle):
    calls, worker, device, clock = lifecycle
    req = request("first")
    register(calls, req)
    native = launch(worker, req)
    req.finished = lambda: True
    clock.now = 1.
    calls.completed(native, ("first",))
    calls.retire("workflow")
    assert calls.retiring == {"workflow"}
    device.completed_position, clock.now = 3., 3.
    calls.poll()
    assert not calls.retiring and not calls.program_calls
    assert not calls.policy.service.service_ms
    with pytest.raises(ValueError, match="unique live"):
        register(calls, request("late"))


def test_workflow_end_cancels_queued_calls_and_drains_running_calls_without_fake_completion(lifecycle):
    calls, worker, device, clock = lifecycle
    active, queued = request("active"), request("queued")
    register(calls, active, queued)
    launch(worker, active)
    calls.retire("workflow")
    assert calls.terminals == {active.rid: "cancel"}
    assert queued.rid not in calls.requests and queued.rid not in calls.policy.calls
    assert calls.policy.counts["completed"] == 0
    device.completed_position, clock.now = 3., 4.
    calls.poll()
    assert not calls.requests and not calls.policy.calls and not calls.retiring
    assert calls.policy.counts["cancelled"] == 2
    assert calls.policy.counts["completed"] == 0
    assert not calls.policy.service.service_ms


def test_native_requeue_preserves_arrival_quantum_and_program_history(lifecycle):
    calls, worker, device, clock = lifecycle
    req = request("first")
    register(calls, req)
    launch(worker, req)
    device.completed_position, clock.now = 3., 5.
    calls.poll()
    call = calls.policy.calls[req.rid]
    quantum = call.quantum_left_ms
    req.cache_request_handle.attempt_id += 1
    calls.requeued([req], is_retracted=True)
    assert calls.requests[req.rid].arrived_ms == 0.
    assert calls.requests[req.rid].key.attempt_id == 1
    assert calls.policy.calls[req.rid] is call
    assert call.quantum_left_ms == quantum and call.total_model_ms == 3.
    assert calls.policy.counts["arrived"] == 1


@pytest.mark.parametrize("mutation", ("request", "input", "output", "sampler", "root", "context", "attempt"))
def test_requeue_rejects_changed_native_call_ownership(lifecycle, mutation):
    calls, _, _, _ = lifecycle
    req = request("first")
    register(calls, req)
    if mutation == "request":
        req = request("first")
    elif mutation == "input":
        req.origin_input_ids = array("q", req.origin_input_ids)
    elif mutation == "output":
        req.output_ids = array("q", req.output_ids)
    elif mutation == "sampler":
        req.sampling_params = object()
    elif mutation == "root":
        req.beliefkv_metadata["root_workflow_id"] = "foreign"
    elif mutation == "context":
        req.beliefkv_metadata["context_epoch"] = 1
    else:
        calls.requests[req.rid].key = replace(calls.requests[req.rid].key, attempt_id=1)
    with pytest.raises(ValueError, match="ownership"):
        calls.requeued([req], is_retracted=True)
    assert calls.policy.counts["arrived"] == 1


def test_completed_ids_cannot_refer_to_unfinished_or_foreign_native_calls(lifecycle):
    calls, _, _, _ = lifecycle
    req = request("first")
    register(calls, req)
    with pytest.raises(ValueError, match="native result"):
        calls.completed(NS(reqs=[req]), ("foreign",))
    with pytest.raises(ValueError, match="unfinished"):
        calls.completed(NS(reqs=[req]), ("first",))
    assert not calls.terminals and calls.policy.counts["completed"] == 0
