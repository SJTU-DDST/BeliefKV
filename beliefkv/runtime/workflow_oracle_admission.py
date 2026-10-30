"""Explicit oracle experiment composed with unchanged reference Full admission."""

from dataclasses import asdict

from beliefkv.runtime.reference_free_continuation import ReferenceFreeContinuationAdmission as Base
from beliefkv.runtime.workflow_size_admission import WorkflowSizeAdmission
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracle


class WorkflowOracleAdmission(WorkflowSizeAdmission):
    policy_name = Base.policy_name + "_workflow_oracle"
    join_free_policy_name = Base.join_free_policy_name + "_workflow_oracle"
    sibling_policy_name = Base.sibling_policy_name + "_workflow_oracle"
    join_sibling_policy_name = Base.join_sibling_policy_name + "_workflow_oracle"
    rank_kind = "oracle_remaining_model_tool_critical_path_seconds"

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

    def _priority(self, prediction):
        if prediction is None or prediction.evidence != "oracle":
            raise ValueError("oracle ranking requires its exact bound workflow prediction")
        return prediction.remaining_path_s

    def _supported(self, prediction):
        return prediction is not None and prediction.evidence == "oracle"

    def _dispatch_prediction_fields(self, prediction):
        return {"prediction": asdict(prediction)} if prediction is not None else {}

    def status(self):
        return {
            **super().status(),
            "future_information_used": True, "deployable": False,
            "workflow_work_source": "oracle",
            "workflow_oracle_admission_order": (
                "global_ready_remaining_path" if self.rank_before_category else
                "within_reference_category"
            ),
            "workflow_size_other_admission_paths": (
                "join_unchanged;bounded_free_dispatch_uses_oracle_model_tool_path"
                if self.dispatch_interval_ms else "unchanged_join_and_free_continuation"
            ),
        }
