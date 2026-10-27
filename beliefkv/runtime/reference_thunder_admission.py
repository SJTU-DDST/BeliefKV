"""Full migration with the unchanged reference ThunderAgent decay scheduler."""

from collections import Counter

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime.paper_baseline import ThunderAgentDecayAdapter


class ReferenceThunderAdmission(ThunderAgentDecayAdapter):
    strategy = "thunderagent_reference"
    policy_name = "full_with_reference_thunderagent_acting_decay"

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

    def registered(self, req, key):
        previous = self.latest.get(key.invocation_id)
        if previous is not None and (
            previous.root_workflow_id, previous.context_id,
        ) != (key.root_workflow_id, key.context_id):
            raise ValueError("an agent cannot change workflow or context identity")
        super().registered(req, key)
        self.latest[key.invocation_id] = key

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

    def holds_restore(self, key, *, source="join_ticket"):
        if self.policy is None or key is None or source not in ("join_ticket", "tool_wait"):
            return False
        previous = self.latest.get(key.invocation_id)
        if previous is None or (
            previous.root_workflow_id, previous.context_id,
            previous.session_id, previous.session_generation,
        ) != (
            key.root_workflow_id, key.context_id, key.session_id, key.session_generation,
        ) or key.context_epoch not in (
            previous.context_epoch - 1, previous.context_epoch, previous.context_epoch + 1,
        ):
            return False
        item = self.policy.programs.get(key.invocation_id)
        invocation = self.runtime.graph.invocations.get(key.invocation_id)
        if (
            item is None or not item.active or invocation is None or invocation.state.terminal
            or invocation.workflow_id != key.root_workflow_id
            or invocation.context_id != key.context_id
        ):
            return False
        if source == "tool_wait":
            return bool(
                not item.pending and invocation.state is InvocationState.WAIT_TOOL
                and invocation.active_tool_calls and key.context_epoch == previous.context_epoch
            )
        return True

    def claim_restore(self, key, *, source):
        # Warming may use existing eligibility; it never changes the active set
        # or creates a logical reservation. Native capacity checks still apply.
        allowed = self.holds_restore(key, source=source)
        self.counts["restore_existing_eligibility" if allowed else "restore_outside_active_set"] += 1
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
            "coadmit_join_children": False,
            "logical_borrowing_enabled": False,
            "restore_changes_eligibility": False,
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
            "snapshot_changes_policy": False,
        }

    def close(self):
        super().close()
        self.latest.clear()


def validate_reference_admission(state):
    expected = {
        "policy": ReferenceThunderAdmission.policy_name,
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
        "coadmit_join_children": False,
        "restore_changes_eligibility": False,
        "restore_reservation_ms": 0,
        "ordinary_restore_reservation_ms": 0,
        "tool_warming_eligibility": "existing_active_tool_wait_only_no_new_reservation",
    }
    if not isinstance(state, dict) or any(
        type(state.get(name)) is not type(value) or state[name] != value
        for name, value in expected.items()
    ):
        raise ValueError("reference ThunderAgent admission differs from declared Full composition")
