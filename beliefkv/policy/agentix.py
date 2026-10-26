"""Agentix program MLFQ; physical execution and swap authority stay in the engine.

The caller supplies observed model-executor service, actual running membership
and scheduler time. No trace lengths, future graph or external waits are service.
Queue bounds, quanta, aging ratio and decode cadence are explicit port parameters.
"""

from bisect import bisect_right
from collections import Counter, OrderedDict
from dataclasses import dataclass
import math
from typing import Callable, Sequence

from beliefkv.policy.program_service import ProgramServiceReference, _service


@dataclass(frozen=True)
class AgentixConfig:
    mode: str
    band_edges_ms: tuple[float, ...]
    quanta_ms: tuple[float, ...]
    starvation_ratio: float
    decode_steps_per_schedule: int

    def __post_init__(self):
        if self.mode not in ("plas", "atlas"):
            raise ValueError("Agentix requires PLAS or ATLAS service inheritance")
        edges = tuple(_service(value) for value in self.band_edges_ms)
        quanta = tuple(_service(value) for value in self.quanta_ms)
        if (
            not quanta or len(quanta) != len(edges) + 1
            or any(value <= 0 for value in (*edges, *quanta))
            or any(a >= b for a, b in zip(edges, edges[1:]))
        ):
            raise ValueError("Agentix needs increasing bands and one positive quantum per queue")
        ratio = _service(self.starvation_ratio)
        if ratio <= 0:
            raise ValueError("Agentix starvation ratio must be positive")
        if type(self.decode_steps_per_schedule) is not int or self.decode_steps_per_schedule <= 0:
            raise ValueError("Agentix scheduling cadence must be positive decode steps")
        object.__setattr__(self, "band_edges_ms", edges)
        object.__setattr__(self, "quanta_ms", quanta)
        object.__setattr__(self, "starvation_ratio", ratio)


@dataclass
class AgentixCall:
    program_id: str
    queue: int
    quantum_left_ms: float
    waiting_since_ms: float | None
    model_ms: float = 0.
    wait_ms: float = 0.
    total_model_ms: float = 0.
    total_wait_ms: float = 0.


class AgentixScheduler:
    """Algorithm 1 queues and multi-step selection at observed request boundaries.

    A shared program ID is the root workflow, including all child invocations.
    ``plan`` is intent, never native admission. ``running`` records what actually
    executed and ``charge`` accepts executor time only for those calls.

    Port choices: completed-call waits accumulate in the program table; aging
    resets per-call fairness counters but retains lifetime executor service for
    completion accounting. The last queue renews and rotates on exhaustion.
    No-op promotions of calls already in Q1 do not reset their accounting.
    """

    def __init__(self, config: AgentixConfig):
        self.config = config
        self.service = ProgramServiceReference(config.mode)
        self.calls: dict[str, AgentixCall] = {}
        self.program_wait_ms: dict[str, float] = {}
        self.queues = tuple(OrderedDict() for _ in config.quanta_ms)
        self.executing: set[str] = set()
        self.counts = Counter()
        self._last_plan: tuple[str, ...] = ()
        self._decode_steps = 0
        self._scheduled_steps: int | None = None
        self._now_ms = 0.
        self._executor_sequence = 0
        self._executor_tickets: dict[int, tuple[str, ...]] = {}
        self._inflight: Counter = Counter()

    def _time(self, now_ms):
        value = _service(now_ms)
        if value < self._now_ms:
            raise ValueError("Agentix scheduler time regressed")
        return value

    def _ids(self, identities):
        values = tuple(identities)
        if len(set(values)) != len(values) or any(value not in self.calls for value in values):
            raise ValueError("Agentix requires unique known request identities")
        return values

    def arrive(
        self, request_id: str, program_id: str, *, now_ms: float,
        queued_since_ms: float | None = None,
    ):
        now = self._time(now_ms)
        arrived = now if queued_since_ms is None else _service(queued_since_ms)
        if arrived > now:
            raise ValueError("Agentix call cannot arrive after its observed registration time")
        inherited = self.service.arrive(request_id, program_id)
        queue = bisect_right(self.config.band_edges_ms, inherited.inherited_service_ms)
        self.calls[request_id] = AgentixCall(
            program_id, queue, self.config.quanta_ms[queue], arrived,
        )
        self.queues[queue][request_id] = None
        self.program_wait_ms.setdefault(program_id, 0.)
        self._now_ms = now
        self.counts["arrived"] += 1
        return queue

    @staticmethod
    def _waiting(call, now):
        return 0. if call.waiting_since_ms is None else now - call.waiting_since_ms

    def running(self, requests: Sequence[str], *, now_ms: float):
        """Commit admitted running calls, retaining peers across another prefill."""
        now, identities = self._time(now_ms), self._ids(requests)
        chosen = set(identities)
        if any(self._inflight[request] for request in self.executing - chosen):
            raise ValueError("Agentix cannot pause a call with undrained executor launches")
        for request in self.executing - chosen:
            self.calls[request].waiting_since_ms = now
        for request in chosen - self.executing:
            call = self.calls[request]
            elapsed = self._waiting(call, now)
            call.wait_ms += elapsed
            call.total_wait_ms += elapsed
            call.waiting_since_ms = None
        self.executing = chosen
        self._now_ms = now

    def charge(self, requests: Sequence[str], *, executor_ms: float):
        """Charge the measured interval to every participating call, without division."""
        elapsed, identities = _service(executor_ms), self._ids(requests)
        if any(request not in self.executing for request in identities):
            raise ValueError("queued or external waiting is not model-executor service")
        self._charge(identities, elapsed)

    def _charge(self, identities, elapsed):
        if any(
            not math.isfinite(self.calls[request].total_model_ms + elapsed)
            or not math.isfinite(self.calls[request].model_ms + elapsed)
            for request in identities
        ):
            raise ValueError("Agentix model-executor service overflow")
        for request in identities:
            call = self.calls[request]
            call.model_ms += elapsed
            call.total_model_ms += elapsed
            call.quantum_left_ms -= elapsed
        self.counts["executor_intervals"] += 1

    def begin_executor(self, requests: Sequence[str], *, now_ms: float) -> int:
        """Bind a real GPU launch before later scheduling changes its membership."""
        identities = self._ids(requests)
        if not identities:
            raise ValueError("Agentix cannot issue an empty executor ticket")
        now = self._time(now_ms)
        if any(request not in self.executing for request in identities):
            raise ValueError("Agentix executor request has not actually been admitted")
        self._executor_sequence += 1
        ticket = self._executor_sequence
        self._executor_tickets[ticket] = identities
        self._inflight.update(identities)
        self._now_ms = now
        return ticket

    def finish_executor(self, ticket: int, *, executor_ms: float):
        """Consume exactly one completed launch, even after another batch started."""
        elapsed = _service(executor_ms)
        if type(ticket) is not int or ticket not in self._executor_tickets:
            raise ValueError("Agentix executor completion has no live launch ticket")
        identities = self._executor_tickets[ticket]
        self._charge(identities, elapsed)
        del self._executor_tickets[ticket]
        for request in identities:
            self._inflight[request] -= 1
            if not self._inflight[request]:
                del self._inflight[request]

    def decode_completed(self, steps: int = 1):
        if type(steps) is not int or steps <= 0:
            raise ValueError("completed decode steps must be positive integers")
        self._decode_steps += steps

    def has_pending_executor(self, request_id: str) -> bool:
        self._ids((request_id,))
        return bool(self._inflight[request_id])

    def schedule_due(self, ready: Sequence[str]) -> bool:
        ready_ids = set(self._ids(ready))
        return (
            self._scheduled_steps is None
            or self._decode_steps - self._scheduled_steps >= self.config.decode_steps_per_schedule
            or not any(request in ready_ids for request in self._last_plan)
        )

    def invalidate_plan(self):
        self._scheduled_steps = None
        self.counts["invalidated_schedules"] += 1

    def retain_observed_selection(self, requests: Sequence[str]):
        """Defer a physical swap without resetting queue position or quanta."""
        self._last_plan = self._ids(requests)
        self._scheduled_steps = self._decode_steps
        self.counts["resource_deferred_selections"] += 1

    def _move(self, request, target):
        call = self.calls[request]
        del self.queues[call.queue][request]
        self.queues[target][request] = None
        call.queue = target
        call.quantum_left_ms = self.config.quanta_ms[target]

    def _refresh(self, now):
        # Visit each call once even when moving it to a later queue.
        for request in tuple(key for queue in self.queues for key in queue):
            call = self.calls[request]
            if call.quantum_left_ms <= 0:
                target = min(call.queue + 1, len(self.queues) - 1)
                self._move(request, target)
                self.counts["demoted_or_rotated"] += 1
            wait = (
                self.program_wait_ms[call.program_id] + call.wait_ms
                + self._waiting(call, now)
            )
            model = self.service.service_ms[call.program_id] + call.model_ms
            if call.queue and wait > 0 and (
                model == 0 or wait / model >= self.config.starvation_ratio
            ):
                self._move(request, 0)
                # Keep lifetime work for the completion update; only aging
                # counters restart, so a promotion cannot erase actual service.
                call.wait_ms = call.model_ms = 0.
                if call.waiting_since_ms is not None:
                    call.total_wait_ms += self._waiting(call, now)
                    call.waiting_since_ms = now
                self.counts["promoted"] += 1

    def plan(
        self, ready: Sequence[str], *, resident: Sequence[str],
        can_fit: Callable[[str, tuple[str, ...]], bool], now_ms: float,
    ) -> tuple[str, ...]:
        """Choose within native capacity; retain resident work between full schedules.

        ``resident`` must describe observed GPU residency, not Host availability.
        Future requests are never valid inputs. As in Algorithm 1, the first
        candidate that cannot fit ends the scan rather than skipping to a peer.
        """
        now = self._time(now_ms)
        ready_ids, resident_ids = set(self._ids(ready)), set(self._ids(resident))
        if not resident_ids <= ready_ids:
            raise ValueError("resident candidates must be currently ready requests")
        due = self.schedule_due(tuple(ready_ids))
        if due:
            self._refresh(now)
            candidates = tuple(
                request for queue in self.queues for request in queue
                if request in ready_ids
            )
            self._scheduled_steps = self._decode_steps
            self.counts["full_schedules"] += 1
        else:
            retained = tuple(request for request in self._last_plan if request in ready_ids)
            retained_set = set(retained)
            candidates = retained + tuple(
                request for queue in self.queues for request in queue
                if request in resident_ids and request not in retained_set
            )
            self.counts["resident_refill_schedules"] += 1
        chosen: tuple[str, ...] = ()
        for request in candidates:
            if not can_fit(request, chosen):
                self.counts["capacity_stops"] += 1
                break
            chosen += (request,)
        self._last_plan = chosen
        self._now_ms = now
        return chosen

    def complete(self, request_id: str, *, now_ms: float):
        now = self._time(now_ms)
        call = self.calls[request_id]
        if self._inflight[request_id]:
            raise ValueError("Agentix completion has unaccounted executor launches")
        waited = call.total_wait_ms + self._waiting(call, now)
        total_wait = self.program_wait_ms[call.program_id] + waited
        if not math.isfinite(total_wait):
            raise ValueError("Agentix completed-program wait overflow")
        value = self.service.complete(request_id, executor_service_ms=call.total_model_ms)
        self.program_wait_ms[call.program_id] = total_wait
        self._remove(request_id)
        self._now_ms = now
        self.counts["completed"] += 1
        return value

    def _remove(self, request_id):
        call = self.calls.pop(request_id)
        del self.queues[call.queue][request_id]
        self.executing.discard(request_id)

    def cancel(self, request_id: str, *, now_ms: float):
        now = self._time(now_ms)
        if self._inflight[request_id]:
            raise ValueError("Agentix cancellation must drain its executor launches")
        self._remove(request_id)
        del self.service.calls[request_id]
        self._now_ms = now
        self.counts["cancelled"] += 1

    def forget(self, program_id: str):
        self.service.forget(program_id)
        self.program_wait_ms.pop(program_id, None)

    def status(self):
        return {
            "mode": self.config.mode, "program_identity": "root_workflow_id",
            "future_information_used": False,
            "queue_sizes": tuple(len(queue) for queue in self.queues),
            "pending_calls": len(self.calls), "executing_calls": len(self.executing),
            "pending_executor_launches": len(self._executor_tickets),
            "programs": len(self.service.service_ms), "counts": dict(self.counts),
            "executor_service_semantics": "measured_executor_interval_per_participating_call",
            "physical_execution_or_swap_authorized": False,
        }
