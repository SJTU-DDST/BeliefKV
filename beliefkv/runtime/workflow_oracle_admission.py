"""Explicit oracle experiment composed with unchanged reference Full admission."""

from dataclasses import asdict
import time

from beliefkv.runtime.reference_free_continuation import ReferenceFreeContinuationAdmission as Base
from beliefkv.runtime.deferred_free_admission import DeferredFreeAdmission
from beliefkv.runtime.workflow_size_admission import WorkflowSizeAdmission
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracle


class WorkflowOracleAdmission(WorkflowSizeAdmission):
    policy_name = Base.policy_name + "_workflow_oracle"
    join_free_policy_name = Base.join_free_policy_name + "_workflow_oracle"
    sibling_policy_name = Base.sibling_policy_name + "_workflow_oracle"
    join_sibling_policy_name = Base.join_sibling_policy_name + "_workflow_oracle"
    rank_kind = "oracle_remaining_model_tool_critical_path_seconds"

    def __init__(self, runtime):
        super().__init__(runtime)
        if self.rank_before_category:
            if not self.dispatch_interval_ms or self._combination is not None:
                self.close()
                raise ValueError("global remaining-path oracle requires its separate bounded free dispatch")
            self._deferred_dispatch = DeferredFreeAdmission(
                self, window_ms=self._work.artifact.raw.get("free_dispatch_coalescing_window_ms", 0),
            )

    def _create_work(self, runtime):
        oracle = WorkflowWorkOracle(runtime.workflow_work_model, runtime.workflow_work_model_sha256)
        order = oracle.artifact.raw.get("admission_order", "within_reference_category")
        if order not in ("within_reference_category", "global_ready_remaining_path"):
            oracle.close()
            raise ValueError("unsupported explicit oracle admission order")
        self._global_priority = order == "global_ready_remaining_path"
        return oracle

    @property
    def rank_before_category(self):
        return getattr(self, "_global_priority", False)

    def _rank(self, invocation, now):
        value = super()._rank(invocation, now)
        if not self.rank_before_category:
            return value
        key = self.latest.get(invocation)
        request = self._live_requests.get(key.request_id) if key is not None else None
        if (
            invocation in self.runtime._join_priority_pending and request is not None
            and self.runtime._ready_join_parent(request, key)
        ):
            return 0.
        return value + 1.

    def _protected_join(self, key):
        request = self._live_requests.get(key.request_id) if key is not None else None
        return bool(
            key is not None and key.invocation_id in self.runtime._join_priority_pending
            and request is not None and self.runtime._ready_join_parent(request, key)
        )

    def _logical_ready_paused(self, item):
        if not super()._logical_ready_paused(item):
            return False
        deferred = self._deferred_dispatch
        if deferred is None or not getattr(deferred, "window_ms", 0):
            return True
        now = self.policy._resume_now
        return deferred.window_open(
            time.monotonic() if now is None else now,
        ) or self._protected_join(self.latest.get(item.identity))

    def _order_dispatch_keys(self, keys, joins):
        deferred = self._deferred_dispatch
        if (
            deferred is not None and getattr(deferred, "window_ms", 0)
            and not deferred.window_open(time.monotonic())
        ):
            keys = [key for key in keys if self._protected_join(key)]
        return super()._order_dispatch_keys(keys, joins)

    def _begin_control_observation(self):
        result = super()._begin_control_observation()
        deferred = self._deferred_dispatch
        if result is not None and deferred is not None and getattr(deferred, "window_ms", 0):
            now = time.monotonic()
            for item, row in result[3]:
                row["resume_window_eligible"] = (
                    deferred.window_open(now) or self._protected_join(self.latest.get(item.identity))
                )
        return result

    def _priority(self, prediction):
        if prediction is None or prediction.evidence != "oracle":
            raise ValueError("oracle ranking requires its exact bound workflow prediction")
        return prediction.remaining_path_s

    def _supported(self, prediction):
        return prediction is not None and prediction.evidence == "oracle"

    def _dispatch_prediction_fields(self, prediction):
        return {"prediction": asdict(prediction)} if prediction is not None else {}

    def status(self):
        deferred = self._deferred_dispatch
        return {
            **super().status(),
            "future_information_used": True, "deployable": False,
            "workflow_work_source": "oracle",
            "workflow_oracle_admission_order": (
                "global_ready_remaining_path" if self.rank_before_category else
                "within_reference_category"
            ),
            "workflow_size_other_admission_paths": (
                "protected_join_unchanged;first_continuation_sibling_use_shared_remaining_path_dispatch"
                if self.rank_before_category else
                "join_unchanged;bounded_free_dispatch_uses_oracle_model_tool_path"
                if self.dispatch_interval_ms else "unchanged_join_and_free_continuation"
            ),
            "workflow_oracle_eager_first_grant": self._deferred_dispatch is None,
            "workflow_oracle_shared_free_dispatch": self._deferred_dispatch is not None,
            "workflow_oracle_coalescing_window_ms": getattr(deferred, "window_ms", 0),
            "workflow_oracle_coalescing_deadline_monotonic_s": getattr(
                deferred, "next_dispatch", None,
            ),
            "workflow_oracle_join_recovery_bypasses_window": True,
            "workflow_oracle_active_continuity_preserved": True,
        }
