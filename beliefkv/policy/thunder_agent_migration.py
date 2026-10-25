"""ThunderAgent's public admission core composed with causal KV recovery."""

from collections import Counter
from dataclasses import dataclass
import math

from beliefkv.policy.paper_baselines import ThunderProgram, ThunderWorkingSet


@dataclass
class MigratingProgram(ThunderProgram):
    in_flight: bool = False
    blocked: str | None = None
    critical: bool = False
    waiting_since: float = 0.
    restore_until: float = 0.


class ThunderAgentMigration(ThunderWorkingSet):
    """Reuse the reference policy; add observed blocking and one KV consumer.

    The inherited program records are the single logical ownership authority.
    Native allocation, page locks and submitted DMA remain separate authorities.
    """

    control_interval_s = 5.
    aging_s = 1800.

    def __init__(self, capacity: int):
        super().__init__(capacity)
        self.capacity = capacity
        self.restore_owner: str | None = None
        self.next_control = 0.
        self.counts = Counter()

    @property
    def agents(self):
        return self.programs

    @property
    def used_tokens(self):
        return self.capacity - self.remaining_capacity()

    def _pause(self, item, now):
        if item.in_flight:
            raise ValueError("running agent capacity cannot be credited before completion")
        was_active = item.active
        super()._pause(item, now)
        item.marked_for_pause = False
        if was_active:
            self.counts["paused"] += 1

    def _resume(self):
        # Known blocked agents are not candidates for the author's resumption
        # algorithm. Keep active owners in its capacity sum, including DMA leases.
        all_programs = self.programs
        self.programs = {
            identity: item for identity, item in all_programs.items()
            if item.active or not item.blocked
        }
        try:
            resumed = super()._resume()
        finally:
            self.programs = all_programs
        self.counts["activated"] += len(resumed)
        return resumed

    def arrive(self, identity, tokens, *, now, critical=False):
        if type(identity) is not str or not identity:
            raise ValueError("agent identity is required")
        if type(tokens) is not int or tokens <= 0 or not math.isfinite(now):
            raise ValueError("positive observed context and finite arrival are required")
        item = self.programs.get(identity)
        if item is None:
            item = MigratingProgram(identity, tokens)
            self.programs[identity] = item
        if item.pending:
            raise ValueError("an agent cannot have concurrent sequential calls")
        item.blocked, item.waiting_since = None, now
        item.critical = item.critical or critical
        before = item.active
        # Ignore only inactive blocked agents when checking initial competition.
        # Active blocked/warming owners still consume the original token budget.
        all_programs = self.programs
        self.programs = {
            name: program for name, program in all_programs.items()
            if program is item or program.active or not program.blocked
        }
        try:
            super().arrive(identity, tokens, now=now)
        finally:
            self.programs = all_programs
        self.counts["activated"] += int(item.active and not before)
        self.rebalance(now=now)

    def allowed(self, identity):
        item = self.programs.get(identity)
        return bool(item and item.active and item.pending and not item.blocked)

    def begin(self, identity):
        if not self.allowed(identity):
            raise ValueError("agent has no live logical admission")
        item = self.programs[identity]
        item.in_flight, item.critical = True, False
        self.release_restore(identity)

    def completed(self, identity, tokens, *, now):
        item = self.programs[identity]
        item.in_flight = False
        super().completed(identity, tokens, now=now)
        if item.blocked and item.active and item.restore_until <= now:
            self._pause(item, now)
        self.rebalance(now=now)

    def block(self, identity, reason, *, now):
        item = self.programs.get(identity)
        if item is None:
            return
        item.blocked = reason
        if item.in_flight:
            item.marked_for_pause = True
        elif item.active and item.restore_until <= now:
            self._pause(item, now)
        self.counts["yield:" + reason] += 1
        self.rebalance(now=now)

    def unblock(self, identity, *, now, critical=False):
        item = self.programs.get(identity)
        if item is not None:
            item.blocked = None
            item.critical = item.critical or critical
            self.rebalance(now=now)

    def requeued(self, identity, *, now):
        item = self.programs[identity]
        item.in_flight = False
        if item.active and (item.marked_for_pause or item.blocked):
            self._pause(item, now)
        self.rebalance(now=now)

    def release_restore(self, identity):
        item = self.programs.get(identity)
        if item is not None:
            item.restore_until = 0.
        if self.restore_owner == identity:
            self.restore_owner = None

    def _activate_target(self, item, *, now, superseded=None):
        if item.active:
            return True
        needed = item.tokens + self.buffer_tokens - self.remaining_capacity()
        victims = sorted(
            (
                other for other in self.programs.values()
                if other.active and not other.in_flight and not other.critical
                and (other.restore_until <= now or other is superseded)
            ),
            key=lambda other: (not other.acting, other.tokens, other.queue_sequence),
        ) if needed > 0 else ()
        if sum(other.tokens + self.buffer_tokens for other in victims) < needed:
            return False
        for victim in victims:
            self._pause(victim, now)
            self.counts["capacity_switch:" + ("join" if item.critical else "starvation")] += 1
            needed -= victim.tokens + self.buffer_tokens
            if needed <= 0:
                break
        item.active, item.marked_for_pause = True, False
        self.counts["activated"] += 1
        return True

    def reserve_restore(self, identity, *, now, critical):
        item = self.programs.get(identity)
        if item is None:
            return False
        self._expire_restore(now)
        owner = self.programs.get(self.restore_owner)
        if owner is not None:
            if owner is item:
                return True
            if not critical or owner.critical:
                return False
        previous = item.critical
        item.critical = previous or critical
        if not self._activate_target(item, now=now, superseded=owner):
            item.critical = previous
            self.counts["restore_capacity_deferred"] += 1
            return False
        if owner is not None:
            self.release_restore(owner.identity)
            self.counts["restore_superseded"] += 1
        item.restore_until = now + 1.5
        self.restore_owner = identity
        self.counts["restore_reserved"] += 1
        return True

    def _expire_restore(self, now):
        owner = self.programs.get(self.restore_owner)
        if owner is not None and owner.restore_until <= now:
            self.release_restore(owner.identity)
            if owner.blocked and not owner.in_flight:
                self._pause(owner, now)
                owner.critical = False
            self.counts["restore_expired"] += 1

    def rebalance(self, *, now):
        self._expire_restore(now)
        for item in self.programs.values():
            if item.critical and not item.active and (
                item.pending and not item.blocked or item.restore_until > now
            ):
                self._activate_target(item, now=now)
        if now < self.next_control:
            return
        self.next_control = now + self.control_interval_s
        self.counts["control_ticks"] += 1
        super().tick(now=now)
        for item in sorted(self.programs.values(), key=lambda item: item.waiting_since):
            if (
                not item.active and item.pending and not item.blocked
                and now - item.waiting_since >= self.aging_s
            ):
                self._activate_target(item, now=now)

    def forget(self, identity, *, now):
        self.release_restore(identity)
        super().forget(identity)
        self.rebalance(now=now)

    def clear(self):
        self.programs.clear()
        self.restore_owner = None
