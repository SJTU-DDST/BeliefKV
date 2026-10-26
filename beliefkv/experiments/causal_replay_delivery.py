"""Use the harness's per-workflow asynchronous control delivery during replay."""

from __future__ import annotations

from collections import Counter
from threading import Lock

from beliefkv.core.events import RuntimeEventKind as Kind, is_unobserved_join_event
from beliefkv.runtime.event_channel import QueuedRuntimeEventSink


ASYNC_REENTRY = frozenset((
    Kind.TOOL_END, Kind.RETURN, Kind.JOIN_SATISFIED, Kind.LLM_SUBMIT, Kind.LLM_RESULT,
))
BARRIERS = frozenset((Kind.WORKFLOW_END, Kind.CONTEXT_COMPACT))
JOIN_INFORMATION_CONTRACT = "observed_runtime_join_boundaries_only_v1"


def control_event_counts(events):
    """Declare exact private and online event populations before a new run."""
    online, private = Counter(), Counter()
    for event in events:
        counts = private if is_unobserved_join_event(event) else online
        counts[event.workflow_id] += 1
    return {
        "join_information_contract": JOIN_INFORMATION_CONTRACT,
        "online_event_batches_by_workflow": dict(sorted(online.items())),
        "private_join_events_by_workflow": dict(sorted(private.items())),
    }


class ReplayEventDelivery:
    def __init__(self, log_batch, transport_factory=None):
        self._log = log_batch
        self._factory = transport_factory
        self._queues = {}
        self._lock = Lock()
        self._private_joins = Counter()

    def emit_batch(self, events):
        if not events:
            return
        self._log(events)
        visible = []
        with self._lock:
            for event in events:
                if is_unobserved_join_event(event):
                    self._private_joins[event.workflow_id] += 1
                else:
                    visible.append(event)
        events = tuple(visible)
        if not events:
            return
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
            "join_information_contract": JOIN_INFORMATION_CONTRACT,
            "private_join_events_by_workflow": dict(sorted(self._private_joins.items())),
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
