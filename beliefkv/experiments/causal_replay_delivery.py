"""Use the harness's per-workflow asynchronous control delivery during replay."""

from __future__ import annotations

from threading import Lock

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.event_channel import QueuedRuntimeEventSink


ASYNC_REENTRY = frozenset((
    Kind.TOOL_END, Kind.RETURN, Kind.JOIN_SATISFIED, Kind.LLM_SUBMIT, Kind.LLM_RESULT,
))
BARRIERS = frozenset((Kind.WORKFLOW_END, Kind.CONTEXT_COMPACT))


class ReplayEventDelivery:
    def __init__(self, log_batch, transport_factory=None):
        self._log = log_batch
        self._factory = transport_factory
        self._queues = {}
        self._lock = Lock()

    def emit_batch(self, events):
        if not events:
            return
        self._log(events)
        if self._factory is None:
            return
        workflows = {event.workflow_id for event in events}
        if len(workflows) != 1:
            raise ValueError("control publication must belong to one workflow")
        workflow = next(iter(workflows))
        with self._lock:
            if workflow not in self._queues:
                self._queues[workflow] = QueuedRuntimeEventSink(self._factory(workflow))
            queue = self._queues[workflow]
        tool_start = len(events) == 1 and events[0].kind is Kind.TOOL_START
        delivery = queue.submit_batch(events, tool_start=tool_start)
        reentry = not any(event.kind in BARRIERS for event in events) and any(
            event.kind in ASYNC_REENTRY for event in events
        )
        if not (tool_start or reentry):
            delivery.wait()

    def flush(self):
        for queue in self._queues.values():
            queue.flush()

    def summary(self):
        return {
            "mode": "per_workflow_harness_queued" if self._factory else "trace_only",
            "workflows": {
                workflow: queue.timing_summary()
                for workflow, queue in self._queues.items()
            },
        }

    def close(self):
        for queue in self._queues.values():
            queue.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
