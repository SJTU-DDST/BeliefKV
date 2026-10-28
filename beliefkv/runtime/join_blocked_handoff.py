"""Give a real JOIN parent complete budget from safely blocked active agents."""

from dataclasses import asdict
from itertools import islice
import time

from beliefkv.control.causal_graph import InvocationState


class JoinBlockedHandoff:
    max_donors = 64
    max_parents = 24

    def __init__(self, admission):
        self.admission = admission
        self.next_attempt = 0.
        from beliefkv.runtime.join_physical_recovery import JoinPhysicalRecovery

        self.recovery = JoinPhysicalRecovery(self)

    def can_resume(self, item):
        if item.pending:
            return True
        key = self.admission.latest.get(item.identity)
        return key is None or not self.admission.runtime._agent_observed_blocked_budget_key(key)

    def _donor(self, item, target):
        return item.active and self._blocked_donor(item, target)

    def _blocked_donor(self, item, target):
        admission, runtime = self.admission, self.admission.runtime
        key = admission.latest.get(item.identity)
        if (
            item.identity == target.invocation_id
            or item.pending or not item.acting or item.marked_for_pause or key is None
            or not runtime._agent_observed_blocked_budget_key(key)
            or any(lease.key.context_id == key.context_id
                   for lease in runtime._prefetch_service_leases.values())
        ):
            return False
        for ticket in (
            runtime._join_ticket, *runtime._confirmed_join_tickets.values(),
            *runtime._join_inflight_tickets.values(),
        ):
            if (
                ticket is not None and ticket.key.invocation_id == item.identity
                and runtime._live_join_ticket(ticket)
            ):
                return False
        demand = runtime._live_critical_parent_demand()
        if demand is not None and demand[0].invocation_id == item.identity:
            return False
        invocation = runtime.graph.invocations[key.invocation_id]
        return (
            invocation.state is InvocationState.WAIT_JOIN
            and admission._waiting_on_join(invocation, runtime.graph.joins.get(invocation.join_id))
            or invocation.state is InvocationState.WAIT_TOOL
            and runtime._predicted_long_tool_wait(key)
        )

    def claim(self, key, join_id):
        admission, runtime = self.admission, self.admission.runtime
        policy = admission.policy
        parent = policy.programs.get(key.invocation_id)
        if (
            runtime.physical_disabled or parent is None or parent.active
            or parent.marked_for_pause or admission.latest.get(key.invocation_id) != key
            or admission._handoff_join(key) != join_id
        ):
            return False
        now = time.monotonic()
        if now < self.next_attempt:
            admission.counts["join_blocked_handoff_backoff"] += 1
            return False
        self.next_attempt = now + .2
        admission._refresh_streaming_progress()
        required = parent.tokens + policy.buffer_tokens
        remaining = policy.remaining_capacity()
        deficit = required - remaining
        if deficit <= 0:
            return admission._admit_from_free_capacity(
                (parent,), event="agent_join_free_capacity_admitted", join_id=join_id,
            )
        candidates = []
        for item in islice(policy.programs.values(), self.max_donors):
            admission.counts["join_blocked_handoff_donor_checks"] += 1
            if self._donor(item, key):
                candidates.append(item)
        fitting = [item for item in candidates if item.tokens + policy.buffer_tokens >= deficit]
        if fitting:
            selected = [min(fitting, key=lambda item: (item.tokens, item.identity))]
        else:
            selected, total = [], 0
            for item in sorted(candidates, key=lambda item: (-item.tokens, item.identity)):
                selected.append(item)
                total += item.tokens + policy.buffer_tokens
                if total >= deficit:
                    break
            if total < deficit:
                admission.counts["join_blocked_handoff_insufficient"] += 1
                return False
        if (
            admission._handoff_join(key) != join_id
            or any(not self._donor(item, key) for item in selected)
        ):
            return False
        donors = [{
            "key": asdict(admission.latest[item.identity]),
            "observed_context_tokens": item.tokens,
            "complete_budget_tokens": item.tokens + policy.buffer_tokens,
        } for item in selected]
        for item in selected:
            policy._pause(item, now)
        parent.active = True
        self.recovery.remember(key, join_id, selected)
        assert policy.remaining_capacity() >= 0
        admission.counts["join_blocked_handoff_grants"] += 1
        admission.counts["join_blocked_handoff_paused_agents"] += len(selected)
        writer = runtime._opportunity_writer
        if writer is not None:
            writer.record({
                "event": "agent_join_blocked_capacity_admitted",
                "ts_ms": time.time() * 1000., "monotonic_s": now,
                "join_id": join_id, "programs": [{
                    "key": asdict(key), "observed_context_tokens": parent.tokens,
                    "complete_budget_tokens": required,
                }],
                "donors": donors, "required_tokens": required,
                "remaining_before_tokens": remaining,
                "remaining_after_tokens": policy.remaining_capacity(),
                "native_capacity_authority_preserved": True,
                "released_device_tokens": 0, "released_host_tokens": 0,
                "semantics": "Blocked logical budget handover; native recovery and admission remain required.",
            })
        return True

    def ready_parents(self, *, preferred=None):
        admission = self.admission
        joins = admission.runtime._join_priority_pending
        if admission.policy is None or not joins:
            return
        identities = ([preferred.invocation_id] if preferred is not None else [])
        identities.extend(islice(joins, self.max_parents))
        for identity in dict.fromkeys(identities):
            key = admission.latest.get(identity)
            item = admission.policy.programs.get(identity)
            if item is None or not item.pending:
                continue
            join_id = admission._handoff_join(key) if key is not None else None
            if join_id is not None and self.claim(key, join_id):
                return
