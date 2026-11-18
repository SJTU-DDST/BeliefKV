"""Bounded first-observed-ready aging for existing workflow admission choices."""

from collections import OrderedDict
from dataclasses import dataclass
import math

from beliefkv.core.events import RuntimeEventKind as Kind


@dataclass(frozen=True)
class ReadyWaitSettings:
    scale_ms: int = 0
    grace_ms: int = 60000
    max_boost: float = 1.25

    def __post_init__(self):
        if (
            type(self.scale_ms) is not int or not 0 <= self.scale_ms <= 3600000
            or type(self.grace_ms) is not int or not 0 <= self.grace_ms <= 3600000
            or type(self.max_boost) not in (int, float)
            or not math.isfinite(self.max_boost) or not 1. <= self.max_boost <= 2.
        ):
            raise ValueError("invalid bounded ready wait priority settings")

    @classmethod
    def from_artifact(cls, raw):
        settings = cls(
            raw.get("ready_wait_priority_ms", 0),
            raw.get("ready_wait_priority_grace_ms", 60000),
            raw.get("ready_wait_priority_max_boost", 1.25),
        )
        if not settings.scale_ms and any(name in raw for name in (
            "ready_wait_priority_grace_ms", "ready_wait_priority_max_boost",
        )):
            raise ValueError("ready wait priority tuning requires enabled wait priority")
        return settings

    def weight(self, base, age_ms):
        if not self.scale_ms or age_ms <= self.grace_ms:
            return base
        progress = min(1., (age_ms - self.grace_ms) / self.scale_ms)
        return min(1., base * (1. + (self.max_boost - 1.) * progress))

    def record(self):
        return {
            "kind": "bounded_relative_ready_wait_v2",
            "scale_ms": self.scale_ms,
            "grace_ms": self.grace_ms,
            "max_boost": self.max_boost,
        }


class ReadyWaitPriority:
    max_requests = 512

    def __init__(self, admission, scale_ms, *, grace_ms=60000, max_boost=1.25):
        self.admission = admission
        self.settings = ReadyWaitSettings(scale_ms, grace_ms, max_boost)
        if not scale_ms:
            raise ValueError("ready wait priority requires a positive growth interval")
        self.scale_ms = scale_ms
        self.since = OrderedDict()
        self.workflow_age_ms = {}

    def begin(self, keys, now):
        """The caller already proved that these exact keys are ready-paused."""
        ages = {}
        for key in keys:
            owner = key.invocation_id
            entry = self.since.get(owner)
            if entry is None or entry[0] is not key and entry[0] != key:
                self.since[owner] = key, now
                since = now
            else:
                since = entry[1]
            self.since.move_to_end(owner)
            age = max(0., now - since) * 1000.
            if age > ages.get(key.root_workflow_id, -1.):
                ages[key.root_workflow_id] = age
        while len(self.since) > self.max_requests:
            self.since.popitem(last=False)
            self.admission.counts["ready_wait_priority_history_evictions"] += 1
        self.workflow_age_ms = ages

    def wait_ms(self, key, now):
        entry = self.since.get(key.invocation_id)
        return (
            max(0., now - entry[1]) * 1000.
            if entry is not None and entry[0] == key else 0.
        )

    def weight(self, workflow, base, *, ready_wait_ms=0.):
        age = max(ready_wait_ms, self.workflow_age_ms.get(workflow, 0.))
        return self.settings.weight(base, age)

    def serviced(self, batch):
        if not self.since:
            return
        for request in getattr(batch, "reqs", ()):
            record = self.admission.requests.get(request.rid)
            if record is None:
                continue
            key = record[0]
            entry = self.since.get(key.invocation_id)
            if entry is not None and (entry[0] is key or entry[0] == key):
                self.forget(key.invocation_id)
                self.admission.counts["ready_wait_priority_service_resets"] += 1

    def forget(self, invocation):
        entry = self.since.pop(invocation, None)
        if entry is not None:
            self.workflow_age_ms.pop(entry[0].root_workflow_id, None)

    def events(self, events):
        for event in events:
            if event.kind in (Kind.TOOL_START, Kind.JOIN_WAIT, Kind.RETURN, Kind.INVOCATION_CANCEL):
                self.forget(event.invocation_id)

    def record(self, workflows):
        return {
            **self.settings.record(),
            "clock": "first_observed_ready_candidate_to_actual_native_service",
            "workflow_age_ms": {
                workflow: self.workflow_age_ms.get(workflow, 0.) for workflow in sorted(workflows)
            },
            "one_shared_weight_per_workflow": True,
            "active_preemption_or_capacity_authority": False,
        }
