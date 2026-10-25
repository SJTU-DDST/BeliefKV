"""Agent admission across model calls, with bounded recovery reservations."""

from collections import Counter
from dataclasses import dataclass
import math


@dataclass
class ActiveAgent:
    identity: str
    tokens: int
    sequence: int
    steps: int = 0
    active: bool = False
    pending: bool = False
    in_flight: bool = False
    blocked: str | None = None
    critical: bool = False
    pause_at_boundary: bool = False
    waiting_since: float = 0.
    restore_until: float = 0.


class AgentWorkingSet:
    """Keep logical context admission separate from native physical ownership.

    Working-set admission follows ThunderAgent's cross-call idea. Observed JOIN
    yielding, aging, and one recovery consumer are BeliefKV extensions.
    """

    def __init__(self, capacity: int, *, buffer_tokens: int = 100, aging_s: float = 1800.):
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("positive native FULL capacity is required")
        if type(buffer_tokens) is not int or buffer_tokens < 0:
            raise ValueError("agent growth reserve must be nonnegative")
        if not math.isfinite(aging_s) or aging_s <= 0:
            raise ValueError("agent aging must be positive")
        self.capacity, self.buffer_tokens, self.aging_s = capacity, buffer_tokens, aging_s
        self.agents: dict[str, ActiveAgent] = {}
        self.used_tokens = self.sequence = 0
        self.restore_owner: str | None = None
        self.counts = Counter()

    def _cost(self, item):
        return item.tokens + self.buffer_tokens

    def _active(self, item, active):
        if item.active != active:
            self.used_tokens += self._cost(item) * (1 if active else -1)
            item.active = active
            self.counts["activated" if active else "paused"] += 1
        if not active:
            item.pause_at_boundary = False

    def _tokens(self, item, tokens):
        if type(tokens) is not int or tokens <= 0:
            raise ValueError("positive observed context tokens are required")
        if item.active:
            self.used_tokens += tokens - item.tokens
        item.tokens = tokens

    def arrive(self, identity: str, tokens: int, *, now: float, critical: bool = False):
        if type(identity) is not str or not identity:
            raise ValueError("agent identity is required")
        item = self.agents.get(identity)
        if item is None:
            self.sequence += 1
            item = ActiveAgent(identity, 0, self.sequence)
            self.agents[identity] = item
        if item.pending:
            raise ValueError("an agent cannot have concurrent sequential calls")
        self._tokens(item, tokens)
        item.steps += 1
        item.pending, item.blocked, item.waiting_since = True, None, now
        item.critical = critical or item.critical
        self.rebalance(now=now)

    def begin(self, identity: str):
        item = self.agents[identity]
        if not self.allowed(identity):
            raise ValueError("agent has no live logical admission")
        item.in_flight = True
        item.critical = False
        self.release_restore(identity)

    def completed(self, identity: str, tokens: int, *, now: float):
        item = self.agents[identity]
        if not item.pending:
            raise ValueError("agent completed without a pending model call")
        self._tokens(item, tokens)
        item.pending, item.in_flight = False, False
        if item.pause_at_boundary or item.blocked:
            self._active(item, False)
        self.rebalance(now=now)

    def block(self, identity: str, reason: str, *, now: float):
        item = self.agents.get(identity)
        if item is None:
            return
        item.blocked = reason
        if item.in_flight:
            item.pause_at_boundary = True
        elif item.restore_until <= now:
            self._active(item, False)
        self.counts["yield:" + reason] += 1
        self.rebalance(now=now)

    def unblock(self, identity: str, *, now: float, critical: bool = False):
        item = self.agents.get(identity)
        if item is not None:
            item.blocked = None
            item.critical = critical or item.critical
            self.rebalance(now=now)

    def allowed(self, identity: str) -> bool:
        item = self.agents.get(identity)
        return bool(item is not None and item.active and item.pending and not item.blocked)

    def requeued(self, identity: str, *, now: float):
        item = self.agents[identity]
        item.in_flight = False
        if item.pause_at_boundary or item.blocked:
            self._active(item, False)
        self.rebalance(now=now)

    def release_restore(self, identity: str):
        item = self.agents.get(identity)
        if item is not None:
            item.restore_until = 0.
        if self.restore_owner == identity:
            self.restore_owner = None

    def reserve_restore(self, identity: str, *, now: float, critical: bool) -> bool:
        item = self.agents.get(identity)
        if item is None:
            return False
        owner = self.agents.get(self.restore_owner)
        if owner is not None and owner.restore_until > now:
            if owner is item:
                return True
            if not critical or owner.critical:
                return False
            self.release_restore(owner.identity)
            self.counts["restore_superseded"] += 1
        previous = item.critical
        item.critical = previous or critical
        item.restore_until = now + 1.5
        self.restore_owner = identity
        self.rebalance(now=now)
        if not item.active:
            self.release_restore(identity)
            item.critical = previous
            self.counts["restore_capacity_deferred"] += 1
            return False
        self.counts["restore_reserved"] += 1
        return True

    def _make_room(self, target, *, now, candidates):
        needed = self._cost(target) - (self.capacity - self.used_tokens)
        if needed <= 0:
            return
        victims = sorted(
            (item for item in candidates
             if item.active and not item.in_flight and not item.critical
             and item.restore_until <= now and item is not target
             and (not item.pending or target.critical
                  or now - target.waiting_since >= self.aging_s
                  and item.waiting_since > target.waiting_since)),
            key=lambda item: (item.pending, item.tokens, -item.sequence),
        )
        # Do not churn idle programs unless this safe switch can fit its target.
        if sum(self._cost(item) for item in victims) < needed:
            return
        for item in victims:
            self._active(item, False)
            self.counts["capacity_switch"] += 1
            self.counts["capacity_switch:" + ("join" if target.critical else "starvation")] += 1
            needed -= self._cost(item)
            if needed <= 0:
                break

    def rebalance(self, *, now: float):
        owner = self.agents.get(self.restore_owner)
        if owner is not None and owner.restore_until <= now:
            self.release_restore(owner.identity)
            if not owner.pending and not owner.in_flight:
                self._active(owner, False)
                owner.critical = False
            self.counts["restore_expired"] += 1
        for item in self.agents.values():
            if item.active and item.blocked and not item.in_flight and item.restore_until <= now:
                self.release_restore(item.identity)
                self._active(item, False)
                item.critical = False
                self.counts["blocked_reservation_released"] += 1
        excess = self.used_tokens - self.capacity
        if excess > 0:
            victims = sorted(
                (item for item in self.agents.values()
                 if item.active and item.restore_until <= now),
                key=lambda item: (item.in_flight, item.pending, item.tokens, -item.sequence),
            )
            for item in victims:
                if item.in_flight:
                    item.pause_at_boundary = True
                else:
                    self._active(item, False)
                self.counts["capacity_pause:growth"] += 1
                excess -= self._cost(item)
                if excess <= 0:
                    break
        waiting = sorted(
            (item for item in self.agents.values()
             if not item.active and (item.pending and not item.blocked or item.restore_until > now)),
            key=lambda item: (
                not item.critical,
                not (item.pending and now - item.waiting_since >= self.aging_s),
                item.waiting_since if item.pending and now - item.waiting_since >= self.aging_s else 0.,
                item.steps == 1, item.tokens, item.sequence,
            ),
        )
        switchable = tuple(
            item for item in self.agents.values()
            if item.active and not item.in_flight and not item.critical
            and item.restore_until <= now
        )
        for item in waiting:
            # Queue ordering can age much faster than agent ownership. Reusing
            # request-scale aging here evicts a healthy agent at each short tool.
            if switchable and (item.critical or now - item.waiting_since >= self.aging_s):
                self._make_room(item, now=now, candidates=switchable)
            if self.used_tokens + self._cost(item) <= self.capacity:
                self._active(item, True)

    def forget(self, identity: str, *, now: float):
        item = self.agents.get(identity)
        if item is not None:
            self.release_restore(identity)
            self._active(item, False)
            del self.agents[identity]
            self.rebalance(now=now)

    def clear(self):
        self.agents.clear()
        self.used_tokens = 0
        self.restore_owner = None
