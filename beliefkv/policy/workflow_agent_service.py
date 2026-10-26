"""Bounded workflow opportunities over retained agent working sets."""

from dataclasses import dataclass, field
import math

from beliefkv.policy.thunder_agent_migration import ThunderAgentMigration


@dataclass
class WorkflowService:
    first_submit: float
    ready: set[str] = field(default_factory=set)
    ready_since: float | None = None
    first_service: float | None = None
    last_service: float | None = None
    service_batches: int = 0
    max_ready_gap: float = 0.

    def gap(self, now):
        if self.ready_since is None:
            return 0.
        return max(0., now - max(
            self.ready_since,
            self.last_service if self.last_service is not None else self.ready_since,
        ))


class WorkflowAgentMigration(ThunderAgentMigration):
    """An explicitly separate candidate, not a modification to the paper arm.

    Service is observed at batch submission; exact launch/worker intervals are
    audited offline. Logical handover never grants physical eviction authority.
    """

    first_service_target_s = 30.
    ready_gap_target_s = 15.
    minimum_tenure_s = 15.
    opportunity_interval_s = 5.

    def __init__(self, capacity):
        super().__init__(capacity, retain_blocked=True)
        self.workflow_by_agent = {}
        self.service = {}
        self.active_since = {}
        self.siblings = {}
        self.join_members = {}
        self.next_opportunity = 0.
        self.deferred_opportunity = False

    def arrive(self, identity, tokens, *, now, critical=False, workflow_id=None):
        if not isinstance(workflow_id, str) or not workflow_id:
            raise ValueError("workflow service accounting requires a root workflow")
        if type(tokens) is not int or tokens <= 0 or not math.isfinite(now) or now < 0.:
            raise ValueError("positive context and finite workflow arrival are required")
        previous = self.workflow_by_agent.get(identity)
        if previous is not None and previous != workflow_id:
            raise ValueError("an agent cannot change its workflow service account")
        if identity in self.agents and self.agents[identity].pending:
            raise ValueError("an agent cannot have concurrent sequential calls")
        self.workflow_by_agent[identity] = workflow_id
        account = self.service.setdefault(workflow_id, WorkflowService(now))
        if not account.ready:
            account.ready_since = now
        account.ready.add(identity)
        super().arrive(identity, tokens, now=now, critical=critical)
        if self.agents[identity].active:
            self.active_since.setdefault(identity, now)
        self._coadmit_sibling(identity, now=now)
        self._coadmit_peers(identity, now=now)

    def _pause(self, item, now):
        super()._pause(item, now)
        self.active_since.pop(item.identity, None)

    def _activate_target(self, item, *, now, **kwargs):
        active = item.active
        activated = super()._activate_target(item, now=now, **kwargs)
        if activated and not active:
            self.active_since[item.identity] = now
        return activated

    def _debt(self, workflow, now):
        account = self.service[workflow]
        target = (
            self.first_service_target_s if account.first_service is None
            else self.ready_gap_target_s
        )
        return account.gap(now) / target

    def _resume(self):
        # One representative per workflow competes in each pass. Fanout cannot
        # multiply a workflow's position in the resumption queue.
        capacity = self.remaining_capacity()
        groups = {}
        for item in self.agents.values():
            if not item.active and item.pending and not item.blocked:
                groups.setdefault(self.workflow_by_agent[item.identity], []).append(item)
        for items in groups.values():
            items.sort(key=lambda item: (
                1 if item.steps == 1 else 0 if not item.acting else 2,
                item.tokens, item.queue_sequence,
            ))
        now = self._control_now
        workflows = sorted(groups, key=lambda workflow: (
            -self._debt(workflow, now),
            self.service[workflow].first_submit, workflow,
        ))
        selected = []
        while workflows:
            remaining = []
            for workflow in workflows:
                items = groups[workflow]
                item = next((
                    candidate for candidate in items
                    if candidate.tokens + self.buffer_tokens <= capacity
                ), None)
                if item is None:
                    continue
                item.active = True
                capacity -= item.tokens + self.buffer_tokens
                self.active_since[item.identity] = now
                selected.append(item.identity)
                items.remove(item)
                if items:
                    remaining.append(workflow)
            workflows = remaining
        self.counts["activated"] += len(selected)
        return tuple(selected)

    def observe_service(self, identities, *, now):
        # Called with this batch only. Never sort or scan the waiting population
        # on a decode step, and count parallel descendants once per workflow.
        workflows = {
            self.workflow_by_agent[identity] for identity in identities
            if identity in self.workflow_by_agent
            and identity in self.agents and self.agents[identity].in_flight
        }
        for workflow in workflows:
            account = self.service[workflow]
            if not account.ready:
                continue
            if account.first_service is not None:
                account.max_ready_gap = max(account.max_ready_gap, account.gap(now))
            else:
                account.first_service = now
            account.last_service = now
            account.service_batches += 1

    def _request_done(self, identity, now):
        workflow = self.workflow_by_agent.get(identity)
        if workflow is None:
            return
        account = self.service[workflow]
        account.ready.discard(identity)
        if not account.ready:
            account.ready_since = None

    def completed(self, identity, tokens, *, now):
        self._request_done(identity, now)
        if self.deferred_opportunity:
            # A periodic probe may find only in-flight owners. Retry at the
            # newly safe boundary instead of repeatedly missing short tools.
            self.next_opportunity = min(self.next_opportunity, now)
        super().completed(identity, tokens, now=now)

    def observe_join(self, join_id, members, *, now):
        members = frozenset(members)
        if len(members) < 2:
            return
        previous = self.join_members.get(join_id)
        if previous is not None and previous != members:
            raise ValueError("an observed JOIN cannot change its sibling membership")
        self.join_members[join_id] = members
        for identity in members:
            self.siblings.setdefault(identity, set()).add(join_id)
        for identity in sorted(members):
            self._coadmit_sibling(identity, now=now)

    def forget_join(self, join_id):
        for identity in self.join_members.pop(join_id, ()):
            joins = self.siblings.get(identity)
            if joins is not None:
                joins.discard(join_id)
                if not joins:
                    del self.siblings[identity]

    def _coadmit_sibling(self, identity, *, now):
        item = self.agents.get(identity)
        if item is None or item.active or not item.pending or item.blocked:
            return
        workflow = self.workflow_by_agent[identity]
        for other, account in self.service.items():
            if other != workflow and account.ready and self._debt(other, now) >= 1.:
                return
        peers = {
            peer for join in self.siblings.get(identity, ())
            for peer in self.join_members.get(join, ()) if peer != identity
        }
        if not any(
            peer in self.agents and self.workflow_by_agent.get(peer) == workflow
            and self.allowed(peer) for peer in peers
        ):
            return
        if self._activate_target(item, now=now, allow_eviction=False):
            self.counts["sibling_coadmitted"] += 1

    def _coadmit_peers(self, identity, *, now):
        for join in sorted(self.siblings.get(identity, ())):
            for peer in sorted(self.join_members.get(join, ())):
                self._coadmit_sibling(peer, now=now)

    def _handover(self, target, *, now):
        needed = target.tokens + self.buffer_tokens - self.remaining_capacity()
        workflow = self.workflow_by_agent[target.identity]
        victims = sorted((
            item for item in self.agents.values()
            if item.active and not item.in_flight and not item.critical
            and item.restore_until <= now
            and self.workflow_by_agent[item.identity] != workflow
            and now - self.active_since.get(item.identity, now) >= self.minimum_tenure_s
            and (
                item.blocked or not item.pending
                or self._debt(self.workflow_by_agent[item.identity], now) < 1.
            )
        ), key=lambda item: (
            not bool(item.blocked), item.pending, item.tokens, item.queue_sequence,
        )) if needed > 0 else ()
        if sum(item.tokens + self.buffer_tokens for item in victims) < needed:
            self.counts["workflow_handover_capacity_deferred"] += 1
            return False
        for item in victims:
            self._pause(item, now)
            self.counts["workflow_handover_donors"] += 1
            needed -= item.tokens + self.buffer_tokens
            if needed <= 0:
                break
        return self._activate_target(target, now=now, allow_eviction=False)

    def rebalance(self, *, now):
        self._control_now = now
        super().rebalance(now=now)
        if now < self.next_opportunity:
            return
        self.next_opportunity = now + self.opportunity_interval_s
        self.counts["workflow_opportunity_ticks"] += 1
        self.deferred_opportunity = False
        waiting = sorted((
            item for item in self.agents.values()
            if not item.active and item.pending and not item.blocked
            and self._debt(self.workflow_by_agent[item.identity], now) >= 1.
        ), key=lambda item: (
            -self._debt(self.workflow_by_agent[item.identity], now),
            self.service[self.workflow_by_agent[item.identity]].first_submit,
            item.tokens, item.queue_sequence,
        ))
        for target in waiting:
            if self._handover(target, now=now):
                account = self.service[self.workflow_by_agent[target.identity]]
                reason = "first_service" if account.first_service is None else "ready_gap"
                self.counts["workflow_handover:" + reason] += 1
                self._coadmit_peers(target.identity, now=now)
                self.deferred_opportunity = False
                break
            self.deferred_opportunity = True

    def overdue_candidate(self, identities, *, now):
        eligible = [
            identity for identity in identities if self.allowed(identity)
            and not self.agents[identity].in_flight
            and self._debt(self.workflow_by_agent[identity], now) >= 1.
        ]
        return min(eligible, key=lambda identity: (
            -self._debt(self.workflow_by_agent[identity], now),
            self.service[self.workflow_by_agent[identity]].first_submit,
            self.agents[identity].queue_sequence,
        ), default=None)

    def forget(self, identity, *, now):
        self._request_done(identity, now)
        super().forget(identity, now=now)
        self.workflow_by_agent.pop(identity, None)
        self.active_since.pop(identity, None)

    def forget_workflow(self, workflow):
        self.service.pop(workflow, None)

    def clear(self):
        super().clear()
        self.workflow_by_agent.clear()
        self.service.clear()
        self.active_since.clear()
        self.siblings.clear()
        self.join_members.clear()
        self.deferred_opportunity = False
