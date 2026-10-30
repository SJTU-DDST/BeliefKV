"""Use live tool predictions only at mandatory ACTING pressure decisions."""

from dataclasses import asdict
import time

from beliefkv.control.causal_graph import InvocationState
from beliefkv.policy.predicted_acting_pressure import PredictedActingPressureWorkingSet
from beliefkv.runtime.reference_free_continuation import ReferenceFreeContinuationAdmission


class PredictedActingPressureAdmission(ReferenceFreeContinuationAdmission):
    def __init__(self, runtime):
        self.working_set_type = lambda capacity, **kwargs: PredictedActingPressureWorkingSet(
            capacity, acting_preference=self._acting_preference, **kwargs,
        )
        super().__init__(runtime)

    def _acting_preference(self, item, now):
        runtime = self.runtime
        key = self.latest.get(item.identity)
        if (
            key is None or not item.active or not item.acting or item.pending
            or item.marked_for_pause or not runtime._agent_observed_blocked_budget_key(key)
        ):
            return None
        invocation = runtime.graph.invocations[key.invocation_id]
        if (
            invocation.state is not InvocationState.WAIT_TOOL
            or len(invocation.active_tool_calls) != 1
            or not runtime._predicted_long_tool_wait(key)
            or any(lease.key.context_id == key.context_id
                   for lease in runtime._prefetch_service_leases.values())
        ):
            return None
        hint = runtime._live_tool_hint(key)
        if hint is None or hint.predictor_sha256 != runtime.predictor_sha256:
            return None
        now_ms = now * 1000.
        online = runtime._online_tool_timing
        if online is not None:
            if not isinstance(hint.residual_support_detail, str) or not online.prefetch_supported(
                key.root_workflow_id, key.invocation_id, now_ms=now_ms,
                scope=hint.residual_support_detail.removeprefix("online_").removesuffix("_command"),
            ):
                return None
        # A full control period plus this owner's possible demand restoration.
        # No physical free-page credit follows from this logical choice.
        unit = runtime._residency_pool_bytes
        if unit is None:
            return None
        size = item.tokens * unit[0]
        estimate = runtime._estimate_h2d_service(size, shape="full")
        restore_ms = (
            estimate.submit_to_ack_p90_ms + estimate.enqueue_to_submit_p90_ms
            if estimate is not None and estimate.enqueue_to_submit_p90_ms is not None
            else size / 8_000_000.
        )
        threshold_ms = self.control_interval_s * 1000. + restore_ms
        probability = hint.release_probability_within(threshold_ms, now_ms=now_ms)
        remaining = runtime._tool_offload_remaining(hint, now_ms=now_ms)
        if not (
            probability is not None
            and probability <= 1. - runtime.tool_offload_min_survival + 1e-12
            or probability is None and remaining is not None and remaining >= threshold_ms
        ):
            return None
        return {
            "key": asdict(key), "threshold_ms": threshold_ms,
            "completion_probability_within_threshold": probability,
            "minimum_survival": runtime.tool_offload_min_survival,
            "evidence": "live_supported_remaining_tool_distribution",
        }

    def tick(self, *, now=None):
        previous = self.counts["control_ticks"]
        super().tick(now=now)
        if self.counts["control_ticks"] == previous:
            return
        writer = getattr(self.runtime, "_opportunity_writer", None)
        for decision in self.policy.last_pressure:
            self.counts["acting_pressure_pauses"] += 1
            self.counts["acting_pressure_predicted_choices"] += decision["evidence"] is not None
            self.counts["acting_pressure_changed_victims"] += (
                decision["selected"] != decision["reference_selected"]
            )
            if writer is not None:
                writer.record({
                    "event": "agent_predicted_acting_pressure",
                    "ts_ms": time.time() * 1000., "monotonic_s": time.monotonic(),
                    **decision, "physical_capacity_credit": False,
                    "scope": "existing_mandatory_logical_pressure_choice_not_physical_eviction",
                })

    def status(self):
        return {
            **super().status(),
            "pressure_pause_policy": "predicted_acting",
            "pressure_prediction_scope": "mandatory_acting_pressure_only",
            "pressure_prediction_fallback": "reference_smallest_context",
            "pressure_prediction_new_timer": False,
            "planned_release_grants_capacity": False,
        }
