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
        return WorkflowWorkOracle(runtime.workflow_work_model, runtime.workflow_work_model_sha256)

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
            "workflow_size_other_admission_paths": (
                "join_unchanged;bounded_free_dispatch_uses_oracle_model_tool_path"
                if self.dispatch_interval_ms else "unchanged_join_and_free_continuation"
            ),
        }
