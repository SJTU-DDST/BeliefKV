from dataclasses import replace
import math

import pytest

from beliefkv.policy.agentix import AgentixScheduler
from beliefkv.runtime.agentix_executor import AgentixExecutor
from tests.test_agentix import configuration


class Event:
    def __init__(self, device):
        self.device, self.stream, self.position = device, None, None

    def record(self, *, stream):
        self.stream, self.position = stream, self.device.position

    def query(self):
        return self.position <= self.device.completed_position

    def elapsed_time(self, other):
        assert self.stream is other.stream
        assert self.position <= other.position <= self.device.completed_position
        return other.position - self.position

    def synchronize(self):
        raise AssertionError("Agentix executor accounting must never synchronize the GPU")


class Device:
    position = 0.
    completed_position = -1.
    capturing = False

    def Event(self, *, enable_timing):
        assert enable_timing
        return Event(self)

    def is_current_stream_capturing(self):
        return self.capturing


def meter(*, limit=8):
    policy = AgentixScheduler(configuration())
    for identity in ("a", "b"):
        policy.arrive(identity, identity, now_ms=0.)
    policy.running(["a", "b"], now_ms=0.)
    return policy, AgentixExecutor(policy, limit=limit), Device(), object()


def launch(executor, device, stream, identities, duration, *, decode=True, now=0.):
    token = executor.begin(
        identities, decode=decode, device=device, stream=stream, now_ms=now,
    )
    device.position += duration
    executor.end(token)
    return token


def test_overlapped_membership_changes_cannot_reassign_a_previous_forward_service():
    policy, executor, device, stream = meter()
    launch(executor, device, stream, ["a"], 5., decode=False)
    launch(executor, device, stream, ["b"], 2., now=1.)
    assert policy.executing == {"a", "b"}
    assert executor.poll() == ()
    with pytest.raises(ValueError, match="unaccounted"):
        policy.complete("a", now_ms=6.)
    device.completed_position = 5.
    assert executor.poll() == (("a",),)
    assert policy.calls["a"].total_model_ms == 5.
    assert policy.calls["b"].total_model_ms == 0.
    assert policy.complete("a", now_ms=6.) == 5.
    device.completed_position = 7.
    assert executor.poll() == (("b",),)
    assert policy.complete("b", now_ms=7.) == 2.
    executor.assert_drained()
    assert executor.status()["measured_executor_ms"] == 7.
    assert executor.status()["counts"]["accounted_decode_batches"] == 1
    assert executor.poll() == ()


def test_shared_batch_elapsed_time_is_charged_once_per_actual_participant():
    policy, executor, device, stream = meter()
    launch(executor, device, stream, ["a", "b"], 12.5)
    device.completed_position = 12.5
    assert executor.poll() == (("a", "b"),)
    assert [policy.calls[key].total_model_ms for key in ("a", "b")] == [12.5, 12.5]
    assert executor.total_executor_ms == 12.5
    executor.assert_drained()
    assert policy.counts["executor_intervals"] == 1


def test_backlog_exhaustion_rejects_new_launch_without_dropping_an_unfinished_interval():
    policy, executor, device, stream = meter(limit=1)
    token = launch(executor, device, stream, ["a"], 3.)
    with pytest.raises(ValueError, match="backlog"):
        launch(executor, device, stream, ["b"], 1.)
    assert len(executor.pending) == policy.status()["pending_executor_launches"] == 1
    with pytest.raises(ValueError, match="unaccounted"):
        executor.assert_drained()
    device.completed_position = 3.
    assert executor.poll() == (("a",),)
    executor.assert_drained()
    with pytest.raises(ValueError, match="ticket"):
        policy.finish_executor(token.ticket, executor_ms=3.)


@pytest.mark.parametrize("bad", (-1., math.nan, math.inf, True, "1"))
def test_invalid_measured_time_retains_the_original_live_ticket_and_pending_interval(bad):
    policy, executor, device, stream = meter()
    token = launch(executor, device, stream, ["a"], 3.)
    token.start.elapsed_time = lambda _: bad
    device.completed_position = 3.
    with pytest.raises(ValueError):
        executor.poll()
    assert len(executor.pending) == policy.status()["pending_executor_launches"] == 1
    assert policy.calls["a"].total_model_ms == 0.


def test_foreign_or_double_end_does_not_commit_a_second_executor_interval():
    policy, executor, device, stream = meter()
    token = executor.begin(
        ["a"], decode=True, device=device, stream=stream, now_ms=0.,
    )
    with pytest.raises(ValueError, match="open forward"):
        executor.end(replace(token))
    executor.end(token)
    with pytest.raises(ValueError, match="open forward"):
        executor.end(token)
    assert len(executor.pending) == 1
    assert policy.status()["pending_executor_launches"] == 1


def test_live_gpu_launch_must_be_accounted_before_program_abort_or_retirement():
    policy, executor, device, stream = meter()
    launch(executor, device, stream, ["a"], 3.)
    with pytest.raises(ValueError, match="drain"):
        policy.cancel("a", now_ms=1.)
    assert "a" in policy.calls
    device.completed_position = 3.
    executor.poll()
    policy.cancel("a", now_ms=3.)
    policy.forget("a")
    assert "a" not in policy.service.service_ms


def test_running_membership_cannot_pause_a_call_before_its_gpu_execution_drains():
    policy, executor, device, stream = meter()
    launch(executor, device, stream, ["a"], 3.)
    with pytest.raises(ValueError, match="undrained"):
        policy.running(["b"], now_ms=1.)
    assert policy.executing == {"a", "b"}
    device.completed_position = 3.
    executor.poll()
    policy.running(["b"], now_ms=3.)
    assert policy.executing == {"b"}
    assert policy.calls["a"].waiting_since_ms == 3.


@pytest.mark.parametrize("bad", (
    "capturing", "unknown_request", "duplicate", "missing_stream", "time", "not_admitted",
))
def test_ambiguous_native_launch_never_gains_an_executor_accounting_ticket(bad):
    policy, executor, device, stream = meter()
    requests, now = ["a"], 0.
    if bad == "capturing":
        device.capturing = True
    elif bad == "unknown_request":
        requests = ["unknown"]
    elif bad == "duplicate":
        requests = ["a", "a"]
    elif bad == "missing_stream":
        stream = None
    elif bad == "not_admitted":
        policy.running([], now_ms=0.)
    else:
        now = -1.
    original_running = policy.executing.copy()
    with pytest.raises(ValueError):
        executor.begin(requests, decode=True, device=device, stream=stream, now_ms=now)
    assert not policy.status()["pending_executor_launches"]
    assert policy.executing == original_running
    assert executor.open is None
    assert not executor.pending
