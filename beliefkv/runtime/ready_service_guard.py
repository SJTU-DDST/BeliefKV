"""A single late service exception without changing ordinary workflow scores."""

from dataclasses import asdict
import time

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.sglang_v0520_admission import _request_key


class ReadyServiceGuard:
    timeout_s = 1800.

    def __init__(self, admission):
        self.admission = admission
        self.since = {}
        self.target = None

    def _ready(self, key):
        a = self.admission
        found = a._candidate(
            key.request_id, key, include_first=True, include_active=True, include_marked=True,
        )
        return found is not None and a._can_resume(found[0])

    def begin(self, now):
        a, current = self.admission, {}
        for key, _arrived in a.requests.values():
            if not self._ready(key):
                continue
            previous = self.since.get(key.invocation_id)
            since = previous[1] if previous is not None and previous[0] == key else now
            current[key.invocation_id] = key, since
        self.since = current
        overdue = {
            key: since for key, since in current.values() if now - since >= self.timeout_s
        }
        target = self.target if self.target in overdue else min(
            overdue, key=lambda key: (overdue[key], key.request_id), default=None,
        )
        if target != self.target and target is not None:
            a.counts["ready_service_guard_selected"] += 1
            writer = getattr(a.runtime, "_opportunity_writer", None)
            if writer is not None:
                writer.record({
                    "event": "ready_service_guard_selected",
                    "ts_ms": time.time() * 1000., "monotonic_s": now,
                    "key": asdict(target), "ready_without_service_s": now - overdue[target],
                    "timeout_s": self.timeout_s, "physical_capacity_credit": False,
                    "scope": "single_logical_timeout_exception;native_admission_still_required",
                })
        self.target = target

    def allows_timeout(self, key):
        allowed = key is not None and key == self.target and self._ready(key)
        if allowed:
            self.admission.counts["ready_service_guard_logical_grants"] += 1
        return allowed

    def order(self, ordered, keys, *, protected_head):
        if self.target is None or not self.admission.allows(self.target):
            return
        floor = int(protected_head)
        for index in range(floor, len(ordered)):
            if keys[ordered[index][0]] == self.target:
                if index != floor:
                    ordered.insert(floor, ordered.pop(index))
                    self.admission.counts["ready_service_guard_native_promotions"] += 1
                return

    def serviced(self, batch, completed_ids=()):
        if not self.since:
            return
        a, workflows = self.admission, set()
        for req in getattr(batch, "reqs", ()):
            record = a.requests.get(req.rid)
            if record is None:
                continue
            key, visible = record[0], a.runtime.visible.get(req.rid)
            # The native completion callback removes visibility before notifying us.
            terminal = (
                visible is None and req.rid in completed_ids
                and a._live_requests.get(req.rid) is req
                and a.latest.get(key.invocation_id) == key and _request_key(req) == key
            )
            if visible == key or terminal:
                workflows.add(key.root_workflow_id)
        if not workflows:
            return
        now = time.monotonic()
        for identity, (key, _since) in tuple(self.since.items()):
            if key.root_workflow_id in workflows:
                self.since[identity] = key, now
        if self.target is not None and self.target.root_workflow_id in workflows:
            a.counts["ready_service_guard_served"] += 1
            self.target = None

    def forget(self, invocation):
        self.since.pop(invocation, None)
        if self.target is not None and self.target.invocation_id == invocation:
            self.target = None

    def events(self, events):
        for event in events:
            if event.kind in (
                Kind.TOOL_START, Kind.JOIN_WAIT, Kind.RETURN, Kind.INVOCATION_CANCEL,
            ):
                self.forget(event.invocation_id)

    def status(self):
        return {
            "timeout_s": self.timeout_s,
            "clock": "continuously_ready_without_actual_native_workflow_service",
            "target": asdict(self.target) if self.target is not None else None,
            "max_concurrent_exceptions": 1,
            "ordinary_rank_changes": False,
            "logical_overcommit_possible_only_for_timeout_exception": True,
            "physical_capacity_credit": False,
        }
