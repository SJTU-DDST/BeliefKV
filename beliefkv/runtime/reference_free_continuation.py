"""Use already free complete budgets between reference scheduling ticks."""

from collections import OrderedDict
from dataclasses import asdict
from itertools import islice
import time

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
        self.dispatch_interval_ms = getattr(runtime, "agent_free_dispatch_interval_ms", 0)
        self._dispatch_pending = OrderedDict()
        self._next_dispatch = 0.

    def _candidate(self, request_id, key, *, include_first=False):
        item = self.policy.programs.get(key.invocation_id)
        invocation = self.runtime.graph.invocations.get(key.invocation_id)
        context = self.runtime.graph.contexts.get(key.context_id)
        req = self._live_requests.get(request_id)
        if (
            item is None or item.active or not item.pending
            or item.steps <= 1 and not self.dispatch_interval_ms and not include_first
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
        if self.dispatch_interval_ms:
            return self._dispatch_free_capacity(preferred=preferred)
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

    def _remember_dispatch(self, key):
        item = self.policy.programs.get(key.invocation_id)
        if item is not None and item.pending and not item.active:
            self._dispatch_pending[key.invocation_id] = key
        else:
            self._dispatch_pending.pop(key.invocation_id, None)

    def _order_dispatch_keys(self, keys, joins):
        return keys

    def _observe_free_capacity_choice(self, programs, remaining, keys, sequence):
        selected = {item.identity for item in programs}
        scope = "actual_dispatch_window" if keys is not None else "bounded_pending_index"
        if keys is None:
            keys = tuple(islice(self._dispatch_pending.values(), self.max_continuation_checks))
        selected_keys = [self.latest[item.identity] for item in programs]
        self.counts["free_choice_observed_grants"] += 1
        if len({key.root_workflow_id for key in (*selected_keys, *keys)}) < 2:
            self.counts["free_choice_single_workflow_key_windows"] += 1
            return None
        started = time.thread_time_ns()
        candidates, checked = [], set()
        selected_ready = True
        protected = False
        for key in (*selected_keys, *keys):
            if key.invocation_id in checked:
                continue
            checked.add(key.invocation_id)
            found = self._candidate(key.request_id, key, include_first=True)
            chosen = key.invocation_id in selected
            if found is None and not chosen:
                continue
            item = self.policy.programs[key.invocation_id] if chosen else found[0]
            req = self._live_requests.get(key.request_id)
            is_join = bool(
                key.invocation_id in self.runtime._join_priority_pending
                and req is not None and self.runtime._ready_join_parent(req, key)
            )
            protected |= is_join
            if chosen:
                selected_ready &= found is not None
            candidates.append({
                "key": asdict(key), "complete_budget_tokens": item.tokens + self.policy.buffer_tokens,
                "selected": chosen, "application_ready": found is not None,
                "protected_join": is_join,
            })
        fitting = [
            row for row in candidates
            if row["complete_budget_tokens"] <= remaining
        ]
        competition = (
            len({row["key"]["root_workflow_id"] for row in fitting}) > 1
            and sum(row["complete_budget_tokens"] for row in fitting) > remaining
        )
        self.counts["free_choice_multi_workflow_key_windows"] += 1
        self.counts["free_choice_candidate_checks"] += len(checked)
        self.counts["free_choice_logical_competitions"] += competition
        self.counts["free_choice_collection_cpu_ns"] += time.thread_time_ns() - started
        if not competition:
            return None
        rankable = selected_ready and not protected
        self.counts["free_choice_ready_unprotected_competitions"] += rankable
        return {
            "schema_version": 1, "ts_ms": time.time() * 1000.,
            "monotonic_s": time.monotonic(), "remaining_before_tokens": remaining,
            "selected_tokens": sum(
                item.tokens + self.policy.buffer_tokens for item in programs
            ),
            "candidates": candidates, "candidate_scope": scope,
            "dispatch_sequence": sequence,
            "pending_index_members": len(self._dispatch_pending),
            "pending_index_members_outside_observation": (
                len(self._dispatch_pending)
                - sum(invocation in self._dispatch_pending for invocation in checked)
            ),
            "all_selected_application_ready": selected_ready,
            "protected_join_present": protected,
            "ready_unprotected_competition": rankable,
            "logical_capacity_only": True,
            "physical_feasibility_proven": False,
            "semantics": (
                "Bounded live alternatives observed before the published logical grant; "
                "several grants from one dispatch are correlated, not independent choices."
            ),
        }

    def _dispatch_free_capacity(self, *, preferred=None):
        if self.policy is None:
            return False
        if preferred is not None:
            self._remember_dispatch(preferred)
        if not self._dispatch_pending:
            return False
        self.counts["free_dispatch_passes"] += 1
        joins = self.runtime._join_priority_pending
        if len(joins) > self.max_join_checks:
            self.counts["free_continuation_join_window_exceeded"] += 1
            return False
        self._refresh_streaming_progress()
        if self.policy.remaining_capacity() <= self.policy.buffer_tokens:
            return False
        keys, checked = [], set()
        for invocation in joins:
            key = self.latest.get(invocation)
            if key is not None:
                keys.append(key)
                checked.add(invocation)
        # Rotate only pending agents; active requests cannot consume the bounded
        # scan window and repeatedly hide a fitting agent beyond its first page.
        for _ in range(min(self.max_continuation_checks, len(self._dispatch_pending))):
            invocation, key = self._dispatch_pending.popitem(last=False)
            self._dispatch_pending[invocation] = key
            if invocation not in checked:
                keys.append(key)
                checked.add(invocation)
        granted = 0
        ordered = self._order_dispatch_keys(keys, joins)
        for key in ordered:
            self.counts["free_dispatch_candidate_checks"] += 1
            found = self._candidate(key.request_id, key)
            if found is None:
                self._dispatch_pending.pop(key.invocation_id, None)
                continue
            item, req = found
            join_id = (
                joins[key.invocation_id]
                if key.invocation_id in joins and self.runtime._ready_join_parent(req, key)
                else None
            )
            if item.tokens + self.policy.buffer_tokens > self.policy.remaining_capacity():
                continue
            if not self._admit_from_free_capacity(
                (item,), event="agent_dispatch_free_capacity_admitted", join_id=join_id,
                choice_keys=ordered, choice_sequence=self.counts["free_dispatch_passes"],
            ):
                self.counts["free_continuation_capacity_changed"] += 1
                break
            self._dispatch_pending.pop(key.invocation_id, None)
            self.counts["free_continuation_admitted"] += 1
            self.counts["free_join_continuation_admitted" if join_id else
                        "free_ordinary_continuation_admitted"] += 1
            self.counts["free_dispatch_new_programs_admitted"] += item.steps == 1
            granted += 1
            if granted == 4:
                break
        return bool(granted)

    def tick(self, *, now=None):
        if now is None:
            now = time.monotonic()
        previous = self.counts["control_ticks"]
        super().tick(now=now)
        if not self.dispatch_interval_ms or self.policy is None:
            return
        if self.counts["control_ticks"] != previous:
            # The complete working-set control can pause additional agents.
            # Reindex only at that boundary, not on each decode step.
            for key, _arrived in self.requests.values():
                self._remember_dispatch(key)
        if now >= self._next_dispatch:
            self._next_dispatch = now + self.dispatch_interval_ms / 1000.
            self.counts["free_dispatch_fallback_ticks"] += 1
            self._admit_free_continuation()

    def requeued(self, requests, *, is_retracted):
        requests = tuple(requests)
        super().requeued(requests, is_retracted=is_retracted)
        if self.dispatch_interval_ms:
            for req in requests:
                record = self.requests.get(req.rid)
                if record is not None:
                    self._remember_dispatch(record[0])

    def forget(self, invocation, *, now=None):
        super().forget(invocation, now=now)
        self._dispatch_pending.pop(invocation, None)

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
        result = {
            **super().status(),
            "free_continuation_admission": True,
            "free_continuation_authority": "complete_undiscounted_unused_logical_budget",
            "free_continuation_updates": "registration_native_completion_runtime_events",
            "free_continuation_candidate_limit": self.max_continuation_checks,
            "free_continuation_join_limit": self.max_join_checks,
            "free_continuation_max_grants_per_event": 1,
            "free_continuation_new_programs": False,
            "free_continuation_physical_prefetch": False,
            "free_capacity_choice_observation_version": 1,
            "free_capacity_choice_observation_enabled": (
                getattr(self.runtime, "_opportunity_writer", None) is not None
            ),
        }
        if self.dispatch_interval_ms:
            result.update(
                free_dispatch_interval_ms=self.dispatch_interval_ms,
                free_dispatch_pending_agents=len(self._dispatch_pending),
                free_dispatch_scan_order="join_first_then_rotating_pending_agents",
                free_dispatch_replaces_pressure_tick=False,
                free_continuation_updates="registration_native_completion_runtime_events_bounded_dispatch",
                free_continuation_max_grants_per_event=4,
                free_continuation_new_programs=True,
            )
        return result

    def close(self):
        super().close()
        self._dispatch_pending.clear()
