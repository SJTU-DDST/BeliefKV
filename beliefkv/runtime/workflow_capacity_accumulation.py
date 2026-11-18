"""Accumulate logical headroom for one ready request for at most one window."""

from dataclasses import asdict
from itertools import islice
import time

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.policy.workflow_capacity_hold import CapacityHoldCandidate, choose_capacity_hold
from beliefkv.policy.workflow_combination import prediction_weight


class ReadyCapacityAccumulation:
    max_candidates = 32
    minimum_wait_ms = 30000
    cooldown_ms = 30000

    def __init__(self, controller, max_wait_ms, *, clock=time.monotonic):
        self.controller = controller
        self.max_wait_ms = max_wait_ms
        self.clock = clock
        self.focus = None
        self.started = self.deadline = self.next_start = 0.
        self.last = None

    def cancel(self, reason):
        if self.focus is not None:
            self.controller.admission.counts["ready_capacity_accumulation_" + reason] += 1
        self.focus = None
        self.started = self.deadline = 0.

    def _candidate(self, key, predictions, now):
        control, admission = self.controller, self.controller.admission
        found = admission._candidate(key.request_id, key, include_first=True)
        if found is None:
            return None
        item, _request = found
        weight = control._work_weight(key, predictions)
        prediction = predictions.get(key.root_workflow_id)
        supported = control._supported_work(prediction)
        base = prediction_weight(prediction) if supported else control.neutral_work_weight
        priority = control.ready_wait_priority
        waited = priority.wait_ms(key, now)
        if supported:
            # A held target may be outside this bounded free-dispatch window.
            weight = max(weight, priority.weight(
                key.root_workflow_id, base, ready_wait_ms=waited,
            ))
        return CapacityHoldCandidate(
            key.invocation_id, key.root_workflow_id,
            item.tokens + admission.policy.buffer_tokens,
            base, weight, waited, item.queue_sequence,
            admission._protected_join(key), supported,
        )

    def consider(self, keys, reference, capacity, predictions, source):
        control, admission = self.controller, self.controller.admission
        now = self.clock()
        self.last = None
        continuing = self.focus is not None
        if continuing and now >= self.deadline:
            self.cancel("deadline")
            return None
        if not continuing and (source != "periodic_decay_resume" or now < self.next_start):
            return None
        if not reference:
            return None
        running = len(getattr(admission.runtime._native_running_batch, "reqs", ()))
        if running < 2:
            self.cancel("insufficient_running_work")
            return None
        if len(reference) > control.max_sibling_candidates:
            self.cancel("reference_outside_bound")
            return None
        if continuing:
            if admission.latest.get(self.focus.invocation_id) != self.focus:
                self.cancel("identity_changed")
                return None
            focus_row = self._candidate(self.focus, predictions, now)
            if focus_row is None:
                self.cancel("not_ready")
                return None
            if not focus_row.supported or focus_row.protected:
                self.cancel("prediction_or_protection_changed")
                return None
            if focus_row.ready_wait_ms < self.minimum_wait_ms:
                self.cancel("wait_identity_changed")
                return None
            if focus_row.logical_tokens <= capacity:
                self.cancel("target_logically_fits")
                return None
            window = list(dict.fromkeys((*reference, self.focus)))
        else:
            window = list(reference)
        for key in islice(keys, self.max_candidates):
            if len(window) >= self.max_candidates:
                break
            if key not in window:
                window.append(key)
        rows, by_id = [], {}
        for key in window:
            row = self._candidate(key, predictions, now)
            if row is not None:
                rows.append(row)
                by_id[row.identity] = key
        if any(key.invocation_id not in by_id for key in reference):
            self.cancel("reference_changed")
            return None
        decision = choose_capacity_hold(
            rows, tuple(key.invocation_id for key in reference),
            capacity=capacity, pool_capacity=admission.policy.capacity_tokens,
            minimum_wait_ms=self.minimum_wait_ms,
            running_width=running, relative_tie=control.costs.relative_tie,
            focus=self.focus.invocation_id if continuing else None,
        ) if rows else None
        if decision is None:
            return None
        if not continuing:
            self.focus = by_id[decision["focus"]]
            self.started, self.deadline = now, now + self.max_wait_ms / 1000.
            self.next_start = now + self.cooldown_ms / 1000.
            admission.counts["ready_capacity_accumulation_started"] += 1
        self.last = {
            "max_wait_ms": self.max_wait_ms,
            "minimum_ready_wait_ms": self.minimum_wait_ms,
            "cooldown_ms": self.cooldown_ms,
            "decision_monotonic_s": now,
            "started_monotonic_s": self.started,
            "deadline_monotonic_s": self.deadline,
            "running_width": running,
            "pool_capacity_tokens": admission.policy.capacity_tokens,
            "logical_capacity_tokens": capacity,
            "relative_tie": control.costs.relative_tie,
            "candidates": [asdict(row) for row in rows],
            "keys": {identity: asdict(key) for identity, key in by_id.items()},
            "reference": [key.invocation_id for key in reference],
            "decision": decision,
            "physical_capacity_credit": False,
        }
        admission.counts["ready_capacity_accumulation_held_decisions"] += 1
        admission.counts["ready_capacity_accumulation_held_reference_members"] += len(
            decision["held_reference"]
        )
        return [by_id[identity] for identity in decision["selected"]]

    def serviced(self, batch):
        if self.focus is None:
            return
        admission = self.controller.admission
        for request in getattr(batch, "reqs", ()):
            record = admission.requests.get(request.rid)
            if record is not None and record[0] == self.focus:
                self.cancel("actual_native_service")
                return

    def events(self, events):
        for event in events:
            if self.focus is not None and event.invocation_id == self.focus.invocation_id and (
                event.kind in (Kind.TOOL_START, Kind.JOIN_WAIT, Kind.RETURN, Kind.INVOCATION_CANCEL)
            ):
                self.cancel("blocked_or_terminal")

    def forget(self, invocation):
        if self.focus is not None and self.focus.invocation_id == invocation:
            self.cancel("forgotten")
