"""Full migration with the unchanged reference ThunderAgent decay scheduler."""

from collections import Counter
from dataclasses import asdict
import time

from beliefkv.control.causal_graph import InvocationState, JoinMode
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.paper_baseline import ThunderAgentDecayAdapter


class ReferenceThunderAdmission(ThunderAgentDecayAdapter):
    strategy = "thunderagent_reference"
    policy_name = "full_with_reference_thunderagent_acting_decay"
    join_free_policy_name = "full_with_reference_thunderagent_free_join_admission"
    sibling_policy_name = "full_with_reference_thunderagent_sibling_admission"
    join_sibling_policy_name = "full_with_reference_thunderagent_join_and_sibling_admission"
    max_sibling_members = 24

    @classmethod
    def composition_name(cls, *, join_free_admission=False, coadmit_join_children=False):
        if coadmit_join_children:
            return cls.join_sibling_policy_name if join_free_admission else cls.sibling_policy_name
        return cls.join_free_policy_name if join_free_admission else cls.policy_name

    def __init__(self, runtime):
        if (
            runtime.internal_comparison_mode != "full"
            or not runtime._custom_kv_management
            or runtime._paper_baseline is not None
            or runtime.enable_frontier_restore
            or runtime.enable_execution_handoff
        ):
            raise ValueError("reference ThunderAgent admission requires Full native demand recovery")
        # Keep the paper adapter's isolation guard intact. Only this explicit
        # Full composition reuses its lifecycle and exact scheduling methods.
        self.runtime = runtime
        self.policy = None
        self.requests = {}
        self._live_requests = {}
        self.workflows = {}
        self.counts = Counter()
        self.next_tick = 0.
        self.last_forced = set()
        self._capacity_snapshot = {}
        self.latest = {}
        self.join_free_admission = getattr(runtime, "agent_join_free_admission", False)
        self.coadmit_join_children = getattr(runtime, "coadmit_join_children", False)
        self.policy_name = self.composition_name(
            join_free_admission=self.join_free_admission,
            coadmit_join_children=self.coadmit_join_children,
        )

    def registered(self, req, key):
        previous = self.latest.get(key.invocation_id)
        if previous is not None and (
            previous.root_workflow_id, previous.context_id,
        ) != (key.root_workflow_id, key.context_id):
            raise ValueError("an agent cannot change workflow or context identity")
        super().registered(req, key)
        self.latest[key.invocation_id] = key
        if self.coadmit_join_children:
            for join_id in sorted(self.runtime._join_by_invocation.get(key.invocation_id, ())):
                self._coadmit_join(join_id)

    def events(self, events):
        super().events(events)
        if self.coadmit_join_children and self.policy is not None:
            joins = set()
            for event in events:
                if event.kind in (Kind.JOIN_CREATE, Kind.JOIN_WAIT):
                    joins.add(event.join_id)
                elif event.kind in (Kind.RETURN, Kind.INVOCATION_CANCEL):
                    joins.update(self.runtime._join_by_invocation.get(event.invocation_id, ()))
            for join_id in sorted(joins - {None}):
                self._coadmit_join(join_id)

    def _waiting_on_join(self, invocation, join):
        return bool(
            invocation is not None and invocation.state is InvocationState.WAIT_JOIN
            and not invocation.active_tool_calls and not invocation.blocking_child_ids
            and join is not None and not join.satisfied
            and invocation.join_id == join.join_id
            and join.workflow_id == invocation.workflow_id
            and invocation.invocation_id in join.waiter_invocation_ids - join.released_waiter_ids
        )

    def _admit_from_free_capacity(self, programs, *, event, join_id):
        # Extra grants can occur between the reference policy's periodic ticks.
        self._refresh_streaming_progress()
        remaining = self.policy.remaining_capacity()
        required = sum(item.tokens + self.policy.buffer_tokens for item in programs)
        if required > remaining:
            return False
        for item in programs:
            item.active = True
        writer = getattr(self.runtime, "_opportunity_writer", None)
        if writer is not None:
            writer.record({
                "event": event, "ts_ms": time.time() * 1000.,
                "monotonic_s": time.monotonic(), "join_id": join_id,
                "programs": [
                    {
                        "key": asdict(self.latest[item.identity]),
                        "observed_context_tokens": item.tokens,
                        "complete_budget_tokens": item.tokens + self.policy.buffer_tokens,
                    } for item in programs
                ],
                "required_tokens": required,
                "remaining_before_tokens": remaining,
                "remaining_after_tokens": remaining - required,
                "native_capacity_authority_preserved": True,
                "semantics": "Logical eligibility grant only; no physical allocation or transfer.",
            })
        return True

    def _coadmit_join(self, join_id):
        join = self.runtime.graph.joins.get(join_id)
        if (
            join is None or join.mode is not JoinMode.ALL
            or not 2 <= len(join.member_invocation_ids) <= self.max_sibling_members
            or not any(
                self._waiting_on_join(self.runtime.graph.invocations.get(identity), join)
                for identity in join.waiter_invocation_ids
            )
        ):
            return
        pending = []
        for identity in sorted(join.member_invocation_ids - join.completed_member_ids):
            item = self.policy.programs.get(identity)
            key = self.latest.get(identity)
            child = self.runtime.graph.invocations.get(identity)
            context = self.runtime.graph.contexts.get(key.context_id) if key is not None else None
            record = self.requests.get(key.request_id) if key is not None else None
            if (
                item is None or not item.pending or item.active or item.marked_for_pause
                or key is None or key.root_workflow_id != join.workflow_id
                or record is None or record[0] != key
                or self.runtime.visible.get(key.request_id) != key
                or child is None or child.workflow_id != join.workflow_id
                or child.state not in (InvocationState.READY, InvocationState.RUNNING_LLM)
                or child.context_id != key.context_id
                or child.active_tool_calls or child.blocking_child_ids
                or context is None or context.workflow_id != join.workflow_id
                or context.epoch != key.context_epoch
            ):
                continue
            pending.append(item)
        if not pending:
            return
        if self._admit_from_free_capacity(
            pending, event="agent_join_siblings_coadmitted", join_id=join_id,
        ):
            self.counts["sibling_free_groups_admitted"] += 1
            self.counts["sibling_free_programs_admitted"] += len(pending)
        else:
            self.counts["sibling_free_capacity_insufficient"] += 1

    def allows(self, key):
        if self.policy is None or key is None:
            return False
        record = self.requests.get(key.request_id)
        item = self.policy.programs.get(key.invocation_id)
        return bool(record is not None and record[0] == key and item is not None and item.active)

    def admitted(self, req):
        pass

    def selected(self, batch):
        pass

    def order_ready(self, ordered, keys, *, now, protected_head=False):
        pass

    def _restore_owner(self, key, *, source):
        if self.policy is None or key is None or source not in ("join_ticket", "tool_wait"):
            return None
        previous = self.latest.get(key.invocation_id)
        if previous is None or (
            previous.root_workflow_id, previous.context_id,
            previous.session_id, previous.session_generation,
        ) != (
            key.root_workflow_id, key.context_id, key.session_id, key.session_generation,
        ) or key.context_epoch not in (
            previous.context_epoch - 1, previous.context_epoch, previous.context_epoch + 1,
        ):
            return None
        item = self.policy.programs.get(key.invocation_id)
        invocation = self.runtime.graph.invocations.get(key.invocation_id)
        if (
            item is None or invocation is None or invocation.state.terminal
            or invocation.workflow_id != key.root_workflow_id
            or invocation.context_id != key.context_id
        ):
            return None
        if source == "tool_wait":
            if not (
                not item.pending and invocation.state is InvocationState.WAIT_TOOL
                and invocation.active_tool_calls and key.context_epoch == previous.context_epoch
            ):
                return None
        return item

    def holds_restore(self, key, *, source="join_ticket"):
        item = self._restore_owner(key, source=source)
        return bool(item is not None and item.active)

    def claim_restore(self, key, *, source):
        allowed = self.holds_restore(key, source=source)
        existing = allowed
        if not allowed and self.join_free_admission and source == "join_ticket":
            item = self._restore_owner(key, source=source)
            invocation = self.runtime.graph.invocations.get(key.invocation_id) if item is not None else None
            join = self.runtime.graph.joins.get(invocation.join_id) if invocation is not None else None
            if (
                item is not None and key == self.latest.get(key.invocation_id)
                and not item.pending and not item.marked_for_pause
                and self._waiting_on_join(invocation, join)
            ):
                if self._admit_from_free_capacity(
                    (item,), event="agent_join_free_capacity_admitted", join_id=join.join_id,
                ):
                    # This is a complete raw-budget grant. Native allocation,
                    # locks, copies and later pressure revocation stay separate.
                    allowed = True
                    self.counts["join_free_capacity_admitted"] += 1
                else:
                    self.counts["join_free_capacity_insufficient"] += 1
        counter = (
            "restore_existing_eligibility" if existing else
            "restore_new_join_eligibility" if allowed else "restore_outside_active_set"
        )
        self.counts[counter] += 1
        return allowed

    def tool_offload_protected(self, key):
        item = self.policy.programs.get(key.invocation_id) if self.policy is not None else None
        return item is None or item.pending or self.latest.get(key.invocation_id) != key

    def requeued(self, requests, *, is_retracted):
        super().requeued(requests, is_retracted=is_retracted)
        for req in requests:
            record = self.requests.get(req.rid)
            if record is not None:
                self.latest[record[0].invocation_id] = record[0]

    def forget(self, invocation, *, now=None):
        super().forget(invocation)
        self.latest.pop(invocation, None)

    def status(self):
        programs = tuple(self.policy.programs.values()) if self.policy is not None else ()
        return {
            **super().status(),
            "strategy": self.strategy,
            "identity": "invocation_id",
            "control_interval_ms": 5000.,
            "blocked_lending": "disabled",
            "join_loan_recall": False,
            "tool_loan_return": False,
            "coadmit_join_children": self.coadmit_join_children,
            "sibling_admission_authority": "observed_blocking_all_join_complete_unused_budget",
            "sibling_admission_max_members": self.max_sibling_members,
            "sibling_admission_update_scope": "request_registration_and_join_or_member_terminal_event",
            "logical_borrowing_enabled": False,
            "restore_changes_eligibility": self.join_free_admission,
            "join_free_admission": self.join_free_admission,
            "join_free_admission_authority": "complete_undiscounted_unused_logical_budget_only",
            "restore_reservation_ms": 0,
            "ordinary_restore_reservation_ms": 0,
            "tool_warming_eligibility": "existing_active_tool_wait_only_no_new_reservation",
            "active_invocation_ids": [item.identity for item in programs if item.active],
            "paused_pending_invocation_ids": [
                item.identity for item in programs if item.pending and not item.active
            ],
            "marked_for_pause_invocation_ids": [
                item.identity for item in programs if item.marked_for_pause
            ],
            "logical_active_tokens": (
                self.policy.capacity_tokens - self.policy.remaining_capacity()
                if self.policy is not None else None
            ),
            "logical_capacity_semantics": "reference_undiscounted_contexts_plus_buffers",
            "extra_join_grant_accounting": "refresh_observed_native_outputs_before_each_capacity_check",
            "snapshot_changes_policy": False,
        }

    def close(self):
        super().close()
        self.latest.clear()


def validate_reference_admission(state, *, join_free_admission=False, coadmit_join_children=False):
    if type(join_free_admission) is not bool or type(coadmit_join_children) is not bool:
        raise ValueError("JOIN admission declarations must be booleans")
    expected = {
        "policy": ReferenceThunderAdmission.composition_name(
            join_free_admission=join_free_admission,
            coadmit_join_children=coadmit_join_children,
        ),
        "strategy": ReferenceThunderAdmission.strategy,
        "identity": "invocation_id",
        "acting_token_decay": True,
        "decay_half_life_s": 1.,
        "decay_scope": "periodic_resume_only",
        "decay_age_source": "observed_native_completion_monotonic",
        "undiscounted_capacity_enforced": True,
        "physical_capacity_credit": False,
        "native_capacity_authority_preserved": True,
        "future_information_used": False,
        "control_interval_ms": 5000.,
        "blocked_lending": "disabled",
        "logical_borrowing_enabled": False,
        "join_loan_recall": False,
        "tool_loan_return": False,
        "coadmit_join_children": coadmit_join_children,
        "restore_changes_eligibility": join_free_admission,
        "restore_reservation_ms": 0,
        "ordinary_restore_reservation_ms": 0,
        "tool_warming_eligibility": "existing_active_tool_wait_only_no_new_reservation",
    }
    if coadmit_join_children:
        expected.update({
            "sibling_admission_authority": "observed_blocking_all_join_complete_unused_budget",
            "sibling_admission_max_members": ReferenceThunderAdmission.max_sibling_members,
            "sibling_admission_update_scope": "request_registration_and_join_or_member_terminal_event",
        })
    if not isinstance(state, dict) or state.get("join_free_admission", False) is not join_free_admission or any(
        type(state.get(name)) is not type(value) or state[name] != value
        for name, value in expected.items()
    ):
        raise ValueError("reference ThunderAgent admission differs from declared Full composition")
