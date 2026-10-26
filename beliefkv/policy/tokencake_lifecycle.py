# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
"""TokenCake public lifecycle state machine, separate from predictive upload.

Adapted from lifecycle.py at 1901ebf31fdf63590148e61d5c2f65e9fb7ec010.
The native bridge supplies generation identifiers and opaque retention keys.
"""

from collections import Counter
from dataclasses import dataclass, field
import math
import time

from beliefkv.policy.tokencake import DurationHistory, TokenCakeMetadata


@dataclass(frozen=True)
class LifecycleSettings:
    enabled: bool = True
    default_stall_s: float = 1.
    release_lead_s: float = .10
    ewma_alpha: float = .5

    def __post_init__(self):
        if (
            type(self.enabled) is not bool
            or any(
                type(value) not in (float, int) or not math.isfinite(value)
                for value in (self.default_stall_s, self.release_lead_s, self.ewma_alpha)
            )
            or self.default_stall_s <= 0 or self.release_lead_s < 0
            or not 0 < self.ewma_alpha <= 1
        ):
            raise ValueError("invalid TokenCake lifecycle settings")


@dataclass(frozen=True)
class LifecycleEvent:
    event: str
    lifecycle_id: str
    kind: str = "stall"
    estimated_duration_s: float | None = None

    def __post_init__(self):
        if (
            self.event not in ("stall_started", "stall_finished")
            or type(self.lifecycle_id) is not str or not self.lifecycle_id
            or type(self.kind) is not str or not self.kind
            or self.estimated_duration_s is not None and (
                type(self.estimated_duration_s) not in (int, float)
                or not math.isfinite(self.estimated_duration_s)
                or self.estimated_duration_s <= 0
            )
        ):
            raise ValueError("invalid TokenCake lifecycle event")


@dataclass(frozen=True)
class LifecycleResult:
    lifecycle_id: str
    event: str
    state: str
    disposition: str
    status_code: int = 200


@dataclass
class Lifecycle:
    metadata: TokenCakeMetadata
    completed_at: float | None = None
    snapshot: object = None
    start: LifecycleEvent | None = None
    started_at: float | None = None
    predicted_duration: float = 0.
    safety_deadline: float = math.inf
    release_deadline: float = math.inf
    ownership_released: bool = False
    retained: set = field(default_factory=set)
    pending_retention: set = field(default_factory=set)
    pending_evaluation: bool = False
    next_evaluation_step: int = 0
    backoff_signature: tuple | None = None
    last_store_step: int = -5
    terminal_cause: str | None = None
    terminal_at: float | None = None
    finish_accepted: bool = False
    anonymous_sample: float | None = None

    @property
    def state(self):
        if self.terminal_cause is not None:
            return self.terminal_cause
        if self.start is not None:
            return "released" if self.ownership_released else "active"
        return "generating" if self.completed_at is None else "awaiting_start"

    @property
    def owns_preservation(self):
        return self.start is not None and self.terminal_at is None and not self.ownership_released


class TokenCakeLifecycleRegistry:
    def __init__(self, settings=None, manager=None, *, clock=time.monotonic):
        self.settings = settings or LifecycleSettings()
        self.manager, self.clock = manager, clock
        self.records, self.generations = {}, set()
        self.history = DurationHistory(
            alpha=self.settings.ewma_alpha, default_seconds=self.settings.default_stall_s,
        )
        self.counts = Counter()

    def associate(self, identifier, metadata):
        self.expire()
        if type(identifier) is not str or not identifier:
            raise ValueError("TokenCake requires a generation lifecycle identifier")
        if identifier in self.records or identifier in self.generations:
            self.counts["conflict"] += 1
            raise ValueError("TokenCake lifecycle already belongs to a generation")
        self.generations.add(identifier)
        self.records[identifier] = Lifecycle(metadata)
        self.counts["associated"] += 1

    def generation_finished(self, identifier, cause, snapshot=None):
        self.expire()
        self.generations.discard(identifier)
        record = self.records.get(identifier)
        if record is None or record.terminal_at is not None:
            return
        if cause not in ("completed", "aborted", "error"):
            raise ValueError("invalid TokenCake generation finish cause")
        if cause != "completed" or not self.settings.enabled:
            self._terminalize(record, cause, self.clock())
            return
        record.completed_at = self.clock()
        record.snapshot = snapshot
        record.pending_evaluation = record.start is not None
        self.counts["completed"] += 1

    @staticmethod
    def history_key(record, kind):
        if record.metadata.agent_type:
            return "type", record.metadata.agent_type, kind
        if record.metadata.agent_name:
            return "name", record.metadata.agent_name, kind
        return None

    def apply(self, event):
        self.expire()
        record = self.records.get(event.lifecycle_id)
        if record is None:
            self.counts["unknown"] += 1
            return LifecycleResult(event.lifecycle_id, event.event, "unknown", "unknown", 404)
        disposition, status, now = "applied", 200, self.clock()
        if event.event == "stall_started":
            if record.start == event:
                disposition = "duplicate"
            elif record.start is not None or record.terminal_at is not None:
                disposition, status = "conflict", 409
            else:
                record.start, record.started_at = event, now
                record.predicted_duration = self.history.predict(
                    self.history_key(record, event.kind),
                    user_estimate=event.estimated_duration_s,
                )
                record.safety_deadline = now + min(3600., max(60., 4 * record.predicted_duration))
                self.set_h2d_estimate(record, 0.)
                record.pending_evaluation = record.completed_at is not None
                self.counts["started"] += 1
        elif record.terminal_at is not None:
            disposition = "duplicate" if record.finish_accepted else "late_finish"
        elif record.start is None:
            disposition, status = "conflict", 409
        else:
            sample = max(0., now - record.started_at)
            key = self.history_key(record, record.start.kind)
            if key is None:
                record.anonymous_sample = sample
            else:
                self.history.observe(key, elapsed_seconds=sample)
            record.finish_accepted = True
            self._terminalize(record, "finished", now)
        if disposition != "applied":
            self.counts[disposition] += 1
        return LifecycleResult(event.lifecycle_id, event.event, record.state, disposition, status)

    def set_h2d_estimate(self, record, seconds):
        if record.started_at is None or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("TokenCake requires a started lifecycle and valid H2D estimate")
        record.release_deadline = (
            record.started_at + record.predicted_duration
            - max(self.settings.release_lead_s, 1.25 * seconds)
        )
        if self.clock() >= record.release_deadline:
            self.release(record)

    def retain_ready(self, record, keys):
        self.expire()
        if not record.owns_preservation:
            return set()
        if self.manager is None:
            raise ValueError("TokenCake retention requires a native ownership manager")
        retained = self.manager.retain(set(keys) - record.retained)
        record.retained.update(retained)
        return retained

    def release(self, record):
        keys, record.retained = record.retained, set()
        record.pending_retention.clear()
        record.ownership_released = True
        if keys:
            if self.manager is None:
                raise ValueError("TokenCake retained ownership has no release manager")
            self.manager.release(keys)

    def _terminalize(self, record, cause, now):
        if record.terminal_at is not None:
            return
        self.release(record)
        record.snapshot = None
        record.pending_evaluation = False
        record.next_evaluation_step = 0
        record.backoff_signature = None
        record.terminal_cause, record.terminal_at = cause, now
        self.counts[cause] += 1

    def terminate(self, identifier, cause="completed"):
        if cause not in ("completed", "finished", "aborted", "error", "expired", "reset"):
            raise ValueError("invalid TokenCake terminal lifecycle cause")
        record = self.records.get(identifier)
        if record is not None:
            self._terminalize(record, cause, self.clock())

    def expire(self):
        now = self.clock()
        for identifier, record in tuple(self.records.items()):
            if record.terminal_at is not None:
                if now >= record.terminal_at + 60.:
                    del self.records[identifier]
            elif record.started_at is not None:
                if now >= record.safety_deadline:
                    self._terminalize(record, "expired", now)
                elif now >= record.release_deadline:
                    self.release(record)
            elif record.completed_at is not None and now >= record.completed_at + 60.:
                self._terminalize(record, "expired", now)

    def invalidate_snapshots(self):
        for record in self.records.values():
            record.snapshot, record.pending_evaluation = None, False

    def external_reset_succeeded(self):
        now = self.clock()
        for record in self.records.values():
            record.retained.clear()
            self._terminalize(record, "reset", now)


class TokenCakePaperLifecycleRegistry(TokenCakeLifecycleRegistry):
    """Paper upload retains its Host source beyond the predicted upload time."""

    def set_h2d_estimate(self, record, seconds):
        if record.started_at is None or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("TokenCake requires a started lifecycle and valid H2D estimate")
        record.release_deadline = record.safety_deadline
        if self.clock() >= record.release_deadline:
            self.release(record)
