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

    def __init__(
        self, capacity: int, *, retain_blocked: bool = False,
        coadmit_join_children: bool = False, recall_join_loans: bool = False,
    ):
        super().__init__(capacity)
        if type(retain_blocked) is not bool:
            raise ValueError("blocked eligibility retention must be explicitly boolean")
        self.retain_blocked = retain_blocked
        if type(coadmit_join_children) is not bool or (
            coadmit_join_children and not retain_blocked
        ):
            raise ValueError("sibling admission requires retained agent eligibility")
        self.coadmit_join_children = coadmit_join_children
        if type(recall_join_loans) is not bool or (recall_join_loans and not retain_blocked):
            raise ValueError("JOIN loan recall requires retained agent eligibility")
        self.recall_join_loans = recall_join_loans
        self.loan_borrowers: dict[str, set[str]] = {}
        self.borrowed_from: dict[str, set[str]] = {}
        self.observed_sibling_joins = {}
        self.observed_agent_joins = {}
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
        self._release_borrowed_budget(item.identity)
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
        for identity in resumed:
            self._settle_lender(identity)
        lenders = tuple(self.loan_borrowers)
        for identity in resumed:
            self._record_borrowing(self.programs[identity], lenders)
        return resumed

    def _release_borrowed_budget(self, identity):
        for lender in self.borrowed_from.pop(identity, ()):
            borrowers = self.loan_borrowers[lender]
            borrowers.discard(identity)

    def _settle_lender(self, identity):
        for borrower in self.loan_borrowers.pop(identity, ()):
            lenders = self.borrowed_from[borrower]
            lenders.discard(identity)
            if not lenders:
                del self.borrowed_from[borrower]

    def _record_borrowing(self, target, lenders):
        """New admissions share the continuity condition of open lending periods."""
        if not self.recall_join_loans:
            return
        for identity in lenders:
            lender = self.programs.get(identity)
            if lender is not None and identity != target.identity and not lender.active:
                self.loan_borrowers.setdefault(identity, set()).add(target.identity)
                self.borrowed_from.setdefault(target.identity, set()).add(identity)

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
        if item.active and not before:
            self._record_borrowing(item, tuple(self.loan_borrowers))
        self.rebalance(now=now)
        if self.coadmit_join_children:
            self._coadmit_join_peers(identity, now=now)

    def observe_sibling_join(self, join_id, members, *, now):
        members = frozenset(members)
        if not self.coadmit_join_children or len(members) < 2:
            return
        previous = self.observed_sibling_joins.get(join_id)
        if previous is not None and previous != members:
            raise ValueError("an observed JOIN cannot change its sibling membership")
        self.observed_sibling_joins[join_id] = members
        for identity in members:
            self.observed_agent_joins.setdefault(identity, set()).add(join_id)
        self._coadmit_join_peers(next(iter(members)), now=now)

    def forget_sibling_join(self, join_id):
        for identity in self.observed_sibling_joins.pop(join_id, ()):
            joins = self.observed_agent_joins.get(identity)
            if joins is not None:
                joins.discard(join_id)
                if not joins:
                    del self.observed_agent_joins[identity]

    def _coadmit_join_peers(self, identity, *, now):
        for join in sorted(self.observed_agent_joins.get(identity, ())):
            members = self.observed_sibling_joins[join]
            if not any(self.allowed(peer) for peer in members):
                continue
            for peer in sorted(members):
                item = self.agents.get(peer)
                if item is not None and not item.active and item.pending and not item.blocked:
                    if self._activate_target(item, now=now, allow_eviction=False):
                        self.counts["sibling_coadmitted"] += 1

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
        if not self.retain_blocked and item.blocked and item.active and item.restore_until <= now:
            self._pause(item, now)
        self.rebalance(now=now)

    def block(self, identity, reason, *, now):
        item = self.programs.get(identity)
        if item is None:
            return
        item.blocked = reason
        if not self.retain_blocked:
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
        if item.active and (item.marked_for_pause or item.blocked and not self.retain_blocked):
            self._pause(item, now)
        self.rebalance(now=now)

    def release_restore(self, identity):
        item = self.programs.get(identity)
        if item is not None:
            item.restore_until = 0.
        if self.restore_owner == identity:
            self.restore_owner = None

    def _activate_target(
        self, item, *, now, superseded=None, allow_eviction=True, blocked_only=False,
    ):
        if item.active:
            return True
        needed = item.tokens + self.buffer_tokens - self.remaining_capacity()
        if needed > 0 and not allow_eviction:
            return False
        victims = sorted(
            (
                other for other in self.programs.values()
                if other.active and not other.in_flight and not other.critical
                and (other.restore_until <= now or other is superseded)
                and (not blocked_only or other.blocked)
            ),
            key=lambda other: (not other.acting, other.tokens, other.queue_sequence),
        ) if needed > 0 else ()
        if sum(other.tokens + self.buffer_tokens for other in victims) < needed:
            return False
        for victim in victims:
            self._pause(victim, now)
            reason = "idle" if blocked_only else "join" if item.critical else "starvation"
            self.counts["capacity_switch:" + reason] += 1
            needed -= victim.tokens + self.buffer_tokens
            if needed <= 0:
                break
        item.active, item.marked_for_pause = True, False
        self._settle_lender(item.identity)
        self._record_borrowing(item, tuple(self.loan_borrowers))
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
        if not self._activate_target(
            item, now=now, superseded=owner, allow_eviction=not self.retain_blocked,
        ):
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
                if not self.retain_blocked:
                    self._pause(owner, now)
                owner.critical = False
            self.counts["restore_expired"] += 1

    def _resume_if_idle(self, now):
        if not any(
            item.active and item.blocked and not item.in_flight
            for item in self.programs.values()
        ):
            return
        if any(
            item.active and (item.in_flight or item.pending and not item.blocked)
            for item in self.programs.values()
        ):
            return
        waiting = sorted(
            (item for item in self.programs.values()
             if not item.active and item.pending and not item.blocked),
            key=lambda item: (
                1 if item.steps == 1 else 0 if not item.acting else 2,
                item.tokens, item.queue_sequence,
            ),
        )
        for item in waiting:
            # Only known blocked, non-running owners can lend eligibility.
            # Native allocation/ACKs must separately establish physical space.
            if self._activate_target(item, now=now, blocked_only=True):
                self.counts["idle_work_resumed"] += 1
                return

    def lend_backed_blocked(self, can_lend, *, now):
        """Admit one queued agent using proven blocked owners, without ready victims."""
        if not self.retain_blocked:
            raise ValueError("backed lending requires retained agent admission")
        waiting = sorted(
            (item for item in self.programs.values()
             if not item.active and item.pending and not item.blocked),
            key=lambda item: (
                not item.critical,
                1 if item.steps == 1 else 0 if not item.acting else 2,
                item.tokens, item.queue_sequence,
            ),
        )[:8]
        donors = sorted(
            (item for item in self.programs.values()
             if item.active and item.blocked and not item.pending and not item.in_flight
             and not item.critical and item.restore_until <= now),
            key=lambda item: (item.blocked != "join", -item.tokens, item.queue_sequence),
        )[:8]
        if not waiting or not donors:
            return None
        available, checked = self.remaining_capacity(), {}
        for target in waiting:
            required = target.tokens + self.buffer_tokens - available
            if required <= 0:
                if self._activate_target(target, now=now, allow_eviction=False):
                    self.counts["backed_wait_free_admitted"] += 1
                    return target.identity
                continue
            selected, released = [], 0
            for donor in donors:
                if donor.identity not in checked:
                    self.counts["backed_wait_probes"] += 1
                    checked[donor.identity] = can_lend(donor.identity) is True
                if not checked[donor.identity]:
                    continue
                selected.append(donor)
                released += donor.tokens + self.buffer_tokens
                if released >= required:
                    break
            if released < required:
                continue
            lenders = {
                identity for donor in selected
                for identity in (donor.identity, *self.borrowed_from.get(donor.identity, ()))
            }
            for donor in selected:
                self._pause(donor, now)
                self.counts["capacity_switch:backed_wait"] += 1
            if not self._activate_target(target, now=now, allow_eviction=False):
                raise ValueError("proven blocked budgets did not cover selected admission")
            self._record_borrowing(target, lenders)
            self.counts["backed_wait_admitted"] += 1
            return target.identity
        return None

    def _recall_ready_join(self, item, *, now):
        if not (
            self.recall_join_loans and item.critical and item.pending
            and not item.active and not item.blocked
        ):
            return False
        needed = item.tokens + self.buffer_tokens - self.remaining_capacity()
        if needed <= 0:
            return self._activate_target(item, now=now, allow_eviction=False)
        candidates = sorted(
            (
                borrower for identity in self.loan_borrowers.get(item.identity, ())
                if (borrower := self.programs.get(identity)) is not None
                and borrower.active and not borrower.in_flight
                and not borrower.critical and borrower.restore_until <= now
            ),
            key=lambda borrower: (-borrower.tokens, borrower.queue_sequence),
        )
        if sum(borrower.tokens + self.buffer_tokens for borrower in candidates) < needed:
            if self.loan_borrowers.get(item.identity):
                self.counts["join_loan_recall_deferred"] += 1
            return False
        # Return only recorded loans, after a current request has completed.
        # Native KV allocation and any required backup remain authoritative.
        for borrower in candidates:
            self._pause(borrower, now)
            self.counts["capacity_switch:join_loan_return"] += 1
            needed -= borrower.tokens + self.buffer_tokens
            if needed <= 0:
                break
        if not self._activate_target(item, now=now, allow_eviction=False):
            raise ValueError("returned JOIN budgets did not cover their lender")
        self.counts["join_loan_recall_admitted"] += 1
        return True

    def rebalance(self, *, now):
        self._expire_restore(now)
        for item in self.programs.values():
            if item.critical and not item.active and (
                item.pending and not item.blocked or item.restore_until > now
            ):
                if not self._activate_target(
                    item, now=now, allow_eviction=not self.retain_blocked,
                ):
                    self._recall_ready_join(item, now=now)
        if self.retain_blocked:
            self._resume_if_idle(now)
        if now < self.next_control:
            return
        self.next_control = now + self.control_interval_s
        self.counts["control_ticks"] += 1
        super().tick(now=now)
        for item in sorted(self.programs.values(), key=lambda item: item.waiting_since):
            if (
                not item.active and item.pending and not item.blocked
                and now - max(item.waiting_since, item.queued_at) >= self.aging_s
            ):
                if self._activate_target(item, now=now):
                    self.counts["starvation_handover"] += 1
                    break

    def forget(self, identity, *, now):
        self.release_restore(identity)
        self._release_borrowed_budget(identity)
        self._settle_lender(identity)
        super().forget(identity)
        self.rebalance(now=now)

    def clear(self):
        self.programs.clear()
        self.restore_owner = None
        self.observed_sibling_joins.clear()
        self.observed_agent_joins.clear()
        self.loan_borrowers.clear()
        self.borrowed_from.clear()
