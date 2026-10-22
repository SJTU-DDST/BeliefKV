import asyncio
from threading import Event, Thread

import pytest

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay_backend import CausalHTTPBackend
from beliefkv.experiments.causal_replay_delivery import ReplayEventDelivery
from beliefkv.runtime.deepagents_adapter import DeepAgentsRuntimeAdapter
from beliefkv.runtime.event_channel import QueuedRuntimeEventSink
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata


class Log:
    def __init__(self):
        self.rows = []

    def emit_batch(self, events):
        self.rows.extend(events)


class BlockedTransport:
    def __init__(self, *, error=None):
        self.entered, self.release = Event(), Event()
        self.rows, self.closed, self.error = [], False, error

    def emit_batch(self, events):
        self.entered.set()
        if not self.release.wait(5.):
            raise TimeoutError("test must release the transport")
        if self.error:
            raise self.error
        self.rows.extend(events)

    def close(self):
        self.closed = True


def event(kind, *, workflow="w", number=0):
    return RuntimeEvent(
        f"{workflow}:{number}", 100. + number, kind, workflow,
        invocation_id="root", context_id="ctx", context_epoch=0,
    )


@pytest.mark.parametrize("kinds", (
    (Kind.TOOL_START,), (Kind.TOOL_END,), (Kind.RETURN,), (Kind.JOIN_SATISFIED,),
    (Kind.LLM_SUBMIT,), (Kind.LLM_RESULT,), (Kind.WORKFLOW_END,),
    (Kind.CONTEXT_COMPACT,), (Kind.INVOCATION_CREATE,), (Kind.INVOCATION_CANCEL,),
    (Kind.STRUCTURED_ACTION,), (Kind.CONTEXT_COMPACT, Kind.LLM_SUBMIT),
))
def test_replay_publication_matches_actual_harness_ack_boundaries(kinds):
    transports = [BlockedTransport(), BlockedTransport()]
    queues = QueuedRuntimeEventSink(transports[0])
    harness_log, replay_log = Log(), Log()
    harness = DeepAgentsRuntimeAdapter(
        harness_log, BeliefKVRequestMetadata("w", "root", "ctx", 0),
        control_sink=queues,
    )
    replay = ReplayEventDelivery(replay_log.emit_batch, lambda _workflow: transports[1])
    events = tuple(event(kind, number=index) for index, kind in enumerate(kinds))
    done = [Event(), Event()]
    errors = []

    def publish(index, function):
        try:
            function(events)
        except BaseException as error:
            errors.append(error)
        finally:
            done[index].set()

    threads = [
        Thread(target=publish, args=(0, lambda rows: harness._publish(rows, control=True))),
        Thread(target=publish, args=(1, replay.emit_batch)),
    ]
    try:
        for thread in threads:
            thread.start()
        assert all(raw.entered.wait(2.) for raw in transports)
        harness_async = done[0].wait(.1)
        replay_async = done[1].wait(.1)
        assert harness_async == replay_async
    finally:
        for raw in transports:
            raw.release.set()
        for thread in threads:
            thread.join(2.)
        queues.flush()
        replay.flush()
        queues.close()
        replay.close()
    assert not errors and all(done_event.is_set() for done_event in done)
    assert harness_log.rows == replay_log.rows == list(events)
    assert transports[0].rows == transports[1].rows == list(events)
    assert all(raw.closed for raw in transports)


def test_one_workflow_ack_does_not_block_another_workflow_or_drop_delivery_failures():
    slow, fast = BlockedTransport(), BlockedTransport()
    fast.release.set()
    log = Log()
    replay = ReplayEventDelivery(log.emit_batch, lambda workflow: slow if workflow == "a" else fast)
    try:
        replay.emit_batch((event(Kind.TOOL_END, workflow="a"),))
        assert slow.entered.wait(2.)
        replay.emit_batch((event(Kind.INVOCATION_CREATE, workflow="b"),))
        assert len(fast.rows) == 1 and slow.rows == []
    finally:
        slow.release.set()
        replay.flush()
        report = replay.summary()
        replay.close()
    assert set(report["workflows"]) == {"a", "b"}
    assert report["workflows"]["a"]["reentry_count"] == 1

    failure = BlockedTransport(error=ValueError("rejected native event"))
    failure.release.set()
    with ReplayEventDelivery(log.emit_batch, lambda _workflow: failure) as failed:
        failed.emit_batch((event(Kind.LLM_RESULT),))
        with pytest.raises(ValueError, match="rejected"):
            failed.flush()
    assert failure.closed


def test_backend_serializes_each_workflow_without_serializing_unrelated_workflows():
    entered, release = Event(), Event()

    def emit(events):
        if events[0].workflow_id == "a":
            entered.set()
            assert release.wait(5.)

    async def run():
        backend = CausalHTTPBackend(None, None, emit)
        slow = asyncio.create_task(backend.emit(event(Kind.INVOCATION_CREATE, workflow="a")))
        try:
            assert await asyncio.to_thread(entered.wait, 2.)
            await asyncio.wait_for(
                backend.emit(event(Kind.INVOCATION_CREATE, workflow="b")), timeout=2.,
            )
            assert ("b", "root") in backend._invocations
            assert ("a", "root") not in backend._invocations
        finally:
            release.set()
            await slow
        await backend.drain()
    asyncio.run(run())


def test_native_replay_logs_events_without_creating_policy_transports():
    log = Log()
    with ReplayEventDelivery(log.emit_batch) as delivery:
        delivery.emit_batch((event(Kind.TOOL_END),))
        delivery.flush()
        assert delivery.summary() == {"mode": "trace_only", "workflows": {}}
    assert len(log.rows) == 1
