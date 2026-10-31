"""Observe tool retention once per working-set control, without KV traversal."""

from dataclasses import asdict
import time

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.policy.workflow_size_resume import WorkflowToolState
from beliefkv.runtime.sglang_v0520_admission import _request_key


class WorkflowToolPressure:
    def __init__(self, admission):
        self.admission = admission
        self.starts = {}

    def events(self, events):
        now = time.monotonic()
        for event in events:
            identity = event.invocation_id
            run = event.attributes.get("tool_run_id")
            if event.kind is Kind.TOOL_START and run is not None:
                self.starts.setdefault(identity, {}).setdefault(run, now)
            elif event.kind is Kind.TOOL_END and identity in self.starts:
                self.starts[identity].pop(run, None)

    def forget(self, identity):
        self.starts.pop(identity, None)

    def _unstarted(self, item, key):
        a, runtime = self.admission, self.admission.runtime
        request = a._live_requests.get(key.request_id)
        kv = getattr(request, "kv", None)
        return bool(
            request is not None and a.requests.get(key.request_id, (None,))[0] == key
            and runtime.visible.get(key.request_id) == key
            and _request_key(request, known_keys=runtime.visible) == key
            and kv is not None and getattr(kv, "req_pool_idx", False) is None
            and all(type(value := getattr(kv, name, None)) is int and value == 0
                    for name in ("kv_allocated_len", "kv_committed_len"))
            and getattr(request, "is_retracted", True) is False
            and getattr(request, "retracted_stain", True) is False
            and (outputs := getattr(request, "output_ids", None)) is not None and not len(outputs)
            and callable(getattr(request, "finished", None)) and not request.finished()
            and not any(request is running for running in
                        getattr(runtime._native_running_batch, "reqs", ()))
        )

    def _protected(self, key, now):
        a, runtime = self.admission, self.admission.runtime
        residency = a._residency_pause
        if residency is not None and key in residency.compared_requests:
            return True
        if any(
            lease.key.context_id == key.context_id and now < lease.expires_at
            for lease in runtime._prefetch_service_leases.values()
        ):
            return True
        for ticket in (
            runtime._join_ticket, *runtime._confirmed_join_tickets.values(),
            *runtime._join_inflight_tickets.values(),
        ):
            if (
                ticket is not None and ticket.key.invocation_id == key.invocation_id
                and runtime._live_join_ticket(ticket)
            ):
                return True
        request = a._live_requests.get(key.request_id)
        return bool(
            request is not None and key.invocation_id in runtime._join_priority_pending
            and runtime._ready_join_parent(request, key)
        )

    def _horizon_ms(self, item):
        a, runtime = self.admission, self.admission.runtime
        horizon = a.control_interval_s * 1000.
        unit = runtime._residency_pool_bytes
        if unit is None:
            return horizon
        size = item.tokens * unit[0]
        costs = []
        for estimate in (
            runtime._estimate_d2h_service(size, shape="full"),
            runtime._estimate_h2d_service(size, shape="full"),
        ):
            costs.append(
                estimate.submit_to_ack_p90_ms + estimate.enqueue_to_submit_p90_ms
                if estimate is not None and estimate.enqueue_to_submit_p90_ms is not None
                else size / 8_000_000.
            )
        return max(horizon, sum(costs))

    def observe(self, item, now):
        a, runtime = self.admission, self.admission.runtime
        key = a.latest.get(item.identity)
        invocation = runtime.graph.invocations.get(item.identity)
        context = runtime.graph.contexts.get(key.context_id) if key is not None else None
        if (
            key is None or invocation is None or context is None
            or runtime.context_sessions.get(key.context_id) != key
            or invocation.context_id != key.context_id
            or invocation.workflow_id != key.root_workflow_id
            or context.workflow_id != key.root_workflow_id or context.epoch != key.context_epoch
            or runtime._terminal(key)
        ):
            return WorkflowToolState(blocked=True)
        blocked = bool(
            invocation.active_tool_calls or invocation.blocking_child_ids
            or runtime._agent_observed_blocked_budget_key(key)
            or item.acting and not item.pending
        )
        protected = self._protected(key, now)
        safe = not item.pending or self._unstarted(item, key)
        if not blocked or not safe or protected:
            return WorkflowToolState(
                blocked=blocked, pause_safe=safe, protected=protected,
                source="restore_protected" if protected else
                "mutable_request" if not safe else "executable",
            )
        if invocation.active_tool_calls:
            starts = self.starts.setdefault(item.identity, {})
            # Start from observed events, never the oracle's future tool times.
            starts = {
                run: starts.get(run, now) for run in invocation.active_tool_calls
            }
            self.starts[item.identity] = starts
            age = max(0., now - max(starts.values()))
            horizon = self._horizon_ms(item)
            if age < a.control_interval_s:
                return WorkflowToolState(
                    True, True, source="short_tool_grace", elapsed_s=age, horizon_ms=horizon,
                )
            hint = runtime._live_tool_hint(key, now_ms=now * 1000.)
            probability = (
                hint.release_probability_within(horizon, now_ms=now * 1000.)
                if hint is not None and hint.predictor_sha256 == runtime.predictor_sha256 else None
            )
            if probability is not None:
                retention = min(1., probability / (1. - runtime.tool_offload_min_survival))
                source = "live_conditional_tool_return"
            else:
                retention = 2. ** -max(0., age - a.control_interval_s)
                source = "elapsed_tool_fallback"
            return WorkflowToolState(
                True, True, retention, source=source, elapsed_s=age,
                horizon_ms=horizon, return_probability=probability,
            )
        if invocation.blocking_child_ids or invocation.state is InvocationState.WAIT_JOIN:
            return WorkflowToolState(True, True, 0., source="observed_join_blocked")
        since = a.policy.acting_since.get(item.identity, now)
        age = max(0., now - since)
        return WorkflowToolState(
            True, True, 2. ** -max(0., age - a.control_interval_s),
            source="elapsed_native_gap_fallback", elapsed_s=age,
        )

    def status(self):
        policy = self.admission.policy
        return {
            "workflow_tool_pressure": True,
            "workflow_tool_pressure_same_weight_for_resume_and_pause": True,
            "workflow_tool_pressure_future_tool_durations_used": False,
            "workflow_tool_pressure_short_grace_ms": self.admission.control_interval_s * 1000.,
            "workflow_tool_pressure_horizon": "max_control_interval_and_cached_full_roundtrip",
            "workflow_tool_pressure_unknown_fallback": "elapsed_wait_after_short_grace_1s_half_life",
            "workflow_tool_pressure_update_scope": "existing_working_set_control_cached_per_boundary",
            "workflow_tool_pressure_last_states": (
                {identity: asdict(state) for identity, state in policy._tool_states.items()}
                if policy is not None else {}
            ),
        }
