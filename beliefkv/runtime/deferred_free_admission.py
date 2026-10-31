"""Coalesce free-budget events at native boundaries or declared oracle epochs."""

import time


class DeferredFreeAdmission:
    def __init__(self, admission, *, window_ms=0):
        if type(window_ms) is not int or window_ms not in (0, 1000, 2000):
            raise ValueError("oracle admission coalescing requires 0, 1000 or 2000 ms")
        self.admission = admission
        self.window_ms = window_ms
        self.dirty = False
        self.protected_dirty = False
        self.window_started = None
        self.next_dispatch = 0.

    def window_open(self, now):
        return not self.window_ms or (
            self.next_dispatch > 0 and now >= self.next_dispatch
        )

    def _notify(self):
        self.dirty = True
        if self.window_ms:
            self.protected_dirty |= bool(self.admission.runtime._join_priority_pending)
            if not self.next_dispatch:
                self.window_started = time.monotonic()
                self.next_dispatch = self.window_started + self.window_ms / 1000.

    def defer(self, preferred):
        if preferred is not None:
            self.admission._remember_dispatch(preferred)
        self._notify()
        return False

    def dispatch(self, now):
        if self.window_ms and not self.window_open(now):
            if self.protected_dirty:
                self.protected_dirty = False
                self.admission._dispatch_free_capacity()
            return
        if not self.dirty and now < self.next_dispatch:
            return
        self.dirty = False
        self.protected_dirty = False
        if self.window_ms:
            pending = len(self.admission._dispatch_pending)
            previous = self.admission.counts["free_continuation_admitted"]
        self.admission._dispatch_free_capacity()
        if self.window_ms:
            self.admission.counts["oracle_coalescing_windows"] += 1
            writer = getattr(self.admission.runtime, "_opportunity_writer", None)
            if pending and writer is not None:
                writer.record({
                    "event": "workflow_oracle_coalesced_dispatch",
                    "ts_ms": time.time() * 1000., "monotonic_s": now,
                    "window_ms": self.window_ms,
                    "window_started_monotonic_s": self.window_started,
                    "window_deadline_monotonic_s": self.next_dispatch,
                    "pending_index_members_before": pending,
                    "free_granted_programs": (
                        self.admission.counts["free_continuation_admitted"] - previous
                    ),
                    "physical_capacity_credit": False,
                })
            self.window_started = now
        self.next_dispatch = now + (
            self.window_ms or self.admission.dispatch_interval_ms
        ) / 1000.

    def siblings_pending(self, join_id):
        a = self.admission
        join = a._siblings.join(join_id) if a._siblings is not None else None
        if join is None:
            return
        for identity in join.member_invocation_ids - join.completed_member_ids:
            if a._ready_sibling(identity, join) is not None:
                a._remember_dispatch(a.latest[identity])
        self._notify()
