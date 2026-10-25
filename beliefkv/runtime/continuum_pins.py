"""Physical TTL retention for the pinned Continuum public policy.

This component uses native locks only. Tool estimation, queue ordering and
running-request preemption must be qualified before exposing a runtime arm.
"""

from collections import Counter
from dataclasses import dataclass
import math
from typing import Mapping

from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey


@dataclass
class _Pin:
    cache: object
    key: PrefillCandidateKey
    node: object
    created: int | float
    lock_params: object
    expires_at: float


def _seconds(value: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("TTL and timestamps must be finite nonnegative seconds")
    return float(value)


def _same_session(before: PrefillCandidateKey, after: PrefillCandidateKey) -> bool:
    return (
        before.root_workflow_id, before.invocation_id, before.context_id,
        before.session_id, before.session_generation,
    ) == (
        after.root_workflow_id, after.invocation_id, after.context_id,
        after.session_id, after.session_generation,
    )


class ContinuumPins:
    """One real native pin per sequential invocation, including queued reentry."""

    def __init__(self):
        self.pins: dict[str, _Pin] = {}
        self.counts: Counter[str] = Counter()

    def retain(
        self, cache: object, key: PrefillCandidateKey, *, ttl: float, now: float,
    ) -> bool:
        duration, timestamp = _seconds(ttl), _seconds(now)
        self.release(key.invocation_id, "next_completion")
        if duration <= .01:
            return False
        if (
            getattr(cache, "tree_components", ()) != (0,)
            or key.session_id is None or key.session_generation is None
        ):
            self.counts["unavailable:session_or_layout"] += 1
            return False
        try:
            snapshot = cache.session_refs.snapshot_latest_session_leaf_anchors(
                key.session_id, key.session_generation, max_leaves=1,
            )
            anchors = tuple(dict(snapshot).get(0, ())) if snapshot is not None else ()
            if len(anchors) != 1:
                self.counts["unavailable:anchor"] += 1
                return False
            node_id, created = anchors[0]
            node = cache.tree_core.node_by_id(node_id)
            if node.creation_time != created:
                self.counts["unavailable:identity"] += 1
                return False
            current, checked = node, 0
            while current.parent is not None:
                checked += 1
                full = current.component_data[0]
                if (
                    checked > 256 or full.value is None or not len(full.value)
                    or current.load_back_pending_id is not None
                ):
                    self.counts["unavailable:resident_path"] += 1
                    return False
                current = current.parent
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            self.counts["unavailable:anchor"] += 1
            return False
        receipt = cache.tree_core.inc_lock_ref(node_id)
        self.pins[key.invocation_id] = _Pin(
            cache, key, node, created, receipt.to_dec_params(), timestamp + duration,
        )
        self.counts["acquired"] += 1
        return True

    def release(self, invocation: str, reason: str) -> PrefillCandidateKey | None:
        pin = self.pins.get(invocation)
        if pin is None:
            return None
        current = pin.cache.tree_core.node_by_id(pin.node.id)
        if current is not pin.node or current.creation_time != pin.created:
            raise RuntimeError("Continuum TTL lost its native lock anchor")
        pin.cache.tree_core.dec_lock_ref(pin.node.id, pin.lock_params)
        del self.pins[invocation]
        self.counts["released:" + reason] += 1
        return pin.key

    def refresh(
        self, waiting: Mapping[str, PrefillCandidateKey], *, now: float,
    ) -> tuple[PrefillCandidateKey, ...]:
        timestamp = _seconds(now)
        released = []
        for invocation, pin in tuple(self.pins.items()):
            queued = waiting.get(invocation)
            if queued is not None:
                if not _same_session(pin.key, queued):
                    released.append(self.release(invocation, "session_changed"))
                continue
            if timestamp >= pin.expires_at:
                released.append(self.release(invocation, "expired"))
        return tuple(released)

    def relieve_pressure(self, *, running_requests: int) -> PrefillCandidateKey | None:
        if type(running_requests) is not int or running_requests < 0:
            raise ValueError("running request count must be a nonnegative integer")
        if running_requests > 1 or not self.pins:
            return None
        # The author's code selects the latest deadline despite its comment.
        invocation = max(self.pins, key=lambda item: self.pins[item].expires_at)
        return self.release(invocation, "capacity_deadlock")

    def close(self) -> None:
        for invocation in tuple(self.pins):
            self.release(invocation, "shutdown")
