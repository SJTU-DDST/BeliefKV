"""Use already free complete budgets between reference scheduling ticks."""

from itertools import islice

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.reference_thunder_admission import ReferenceThunderAdmission


class ReferenceFreeContinuationAdmission(ReferenceThunderAdmission):
    policy_name = ReferenceThunderAdmission.policy_name + "_free_continuation"
    join_free_policy_name = ReferenceThunderAdmission.join_free_policy_name + "_free_continuation"
    sibling_policy_name = ReferenceThunderAdmission.sibling_policy_name + "_free_continuation"
    join_sibling_policy_name = ReferenceThunderAdmission.join_sibling_policy_name + "_free_continuation"
    max_continuation_checks = 8
    max_join_checks = 24

    def __init__(self, runtime):
        if not getattr(runtime, "agent_free_continuation_admission", False):
            raise ValueError("event continuation admission requires explicit Full enablement")
        super().__init__(runtime)

    def _candidate(self, request_id, key):
        item = self.policy.programs.get(key.invocation_id)
        invocation = self.runtime.graph.invocations.get(key.invocation_id)
        context = self.runtime.graph.contexts.get(key.context_id)
        req = self._live_requests.get(request_id)
        if (
            item is None or item.active or not item.pending or item.steps <= 1
            or item.marked_for_pause or self.latest.get(key.invocation_id) != key
            or self.runtime.visible.get(request_id) != key
            or self.requests.get(request_id, (None,))[0] != key or req is None
            or invocation is None or invocation.workflow_id != key.root_workflow_id
            or invocation.context_id != key.context_id
            or invocation.state not in (InvocationState.READY, InvocationState.RUNNING_LLM)
            or invocation.active_tool_calls or invocation.blocking_child_ids
            or context is None or context.workflow_id != key.root_workflow_id
            or context.epoch != key.context_epoch
        ):
            return None
        return item, req

    def _admit_free_continuation(self, *, preferred=None):
        if self.policy is None or not self.requests:
            return False
        self.counts["free_continuation_event_checks"] += 1
        joins = self.runtime._join_priority_pending
        if len(joins) > self.max_join_checks:
            self.counts["free_continuation_join_window_exceeded"] += 1
            return False
        candidates, checked = [], set()
        # Examine all known JOIN parents in the bounded dependency window first.
        for invocation in joins:
            key = self.latest.get(invocation)
            if key is None:
                continue
            found = self._candidate(key.request_id, key)
            checked.add(key.request_id)
            if found is not None and self.runtime._ready_join_parent(found[1], key):
                candidates.append((found[0], joins[invocation]))
        remaining_checks = self.max_continuation_checks
        if preferred is not None and preferred.request_id not in checked:
            found = self._candidate(preferred.request_id, preferred)
            checked.add(preferred.request_id)
            remaining_checks -= 1
            if found is not None:
                candidates.append((found[0], None))
        for request_id, (key, _arrived) in islice(
            self.requests.items(), remaining_checks,
        ):
            if request_id in checked:
                continue
            found = self._candidate(request_id, key)
            if found is not None:
                candidates.append((found[0], None))
        for item, join_id in candidates:
            if item.tokens + self.policy.buffer_tokens > self.policy.remaining_capacity():
                continue
            # The shared helper refreshes actual streaming growth and rechecks
            # complete raw capacity immediately before the single grant.
            if self._admit_from_free_capacity(
                (item,), event="agent_continuation_free_capacity_admitted", join_id=join_id,
            ):
                self.counts["free_continuation_admitted"] += 1
                self.counts["free_join_continuation_admitted" if join_id else
                            "free_ordinary_continuation_admitted"] += 1
                return True
            self.counts["free_continuation_capacity_changed"] += 1
            return False
        return False

    def registered(self, req, key):
        super().registered(req, key)
        self._admit_free_continuation(preferred=key)

    def completed(self, batch, completed_ids):
        if not completed_ids:
            return
        super().completed(batch, completed_ids)
        self._admit_free_continuation()

    def events(self, events):
        events = tuple(events)
        if not events:
            return
        super().events(events)
        if any(event.kind in (
            Kind.LLM_SUBMIT, Kind.TOOL_END, Kind.JOIN_SATISFIED,
            Kind.RETURN, Kind.INVOCATION_CANCEL, Kind.WORKFLOW_END,
        ) for event in events):
            self._admit_free_continuation()

    def status(self):
        return {
            **super().status(),
            "free_continuation_admission": True,
            "free_continuation_authority": "complete_undiscounted_unused_logical_budget",
            "free_continuation_updates": "registration_native_completion_runtime_events",
            "free_continuation_candidate_limit": self.max_continuation_checks,
            "free_continuation_join_limit": self.max_join_checks,
            "free_continuation_max_grants_per_event": 1,
            "free_continuation_new_programs": False,
            "free_continuation_physical_prefetch": False,
        }
