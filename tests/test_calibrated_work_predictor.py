from copy import deepcopy
import json

import pytest

from beliefkv.predictor.workflow_work import WorkflowWorkModel
from scripts.prepare_calibrated_work_predictor import (
    KIND, calibrated_predictor, deployment_evaluation,
)
from tests.test_calibrated_combination_preparation import calibration
from tests.test_workflow_work_distribution import distribution_raw


def reports():
    costs = calibration()
    costs["registration"] = "/control/registration.json"
    costs["input_jsonl_prefixes"] = {"/control/batches.jsonl": {"prefix": "complete"}}
    context = {
        "source_registration": costs["registration"],
        "frozen_current_prefill_ms_per_token": costs["completed_control_cost_proxy"]["prefill_ms_per_token"],
        "scope": {
            "same_completed_control": True,
            "sequence_tokens_before_is_native_prefix_not_total_input": True,
            "GPU_action": False,
        },
        "input_jsonl_prefixes": costs["input_jsonl_prefixes"],
        "single_request_prefill_batches_at_least_256_tokens_by_context_after_upper_bound": {
            name: {"batch_count": 30, "ms_per_input_token": {"count": 30, "p50": .05 + i * .01}}
            for i, name in enumerate(("8192", "32768", "65536", "131072", "at_least_131072"))
        },
    }
    return costs, context


def original():
    raw = distribution_raw()
    raw["admission_cost_profile"] = {"oracle_reference_path_s": 600., "kind": "old"}
    raw["metadata"]["split_contract"] = {"mode": "workflow", "complete_workflows_disjoint": True}
    return raw


def test_current_costs_do_not_change_predictions_training_labels_or_support():
    raw = original()
    costs, context = reports()
    result = calibrated_predictor(raw, costs, context, calibration_binding={})
    assert raw == original()
    before, after = WorkflowWorkModel(raw), WorkflowWorkModel(result)
    assert after.raw["admission_cost_profile"]["kind"] == KIND
    assert after.raw["admission_cost_profile"]["costs"]["decode_cost_basis"] == "full_batch"
    for key, value in raw.items():
        if key != "admission_cost_profile":
            assert result[key] == value
    assert before.predict(before.task_projection("ab"), [0.] * 36) == after.predict(
        after.task_projection("ab"), [0.] * 36,
    )


@pytest.mark.parametrize("invalid", ("not_eligible", "different_control", "missing_bucket", "partial_prefix"))
def test_cost_deployment_rejects_changed_or_inadequate_evidence(invalid):
    raw, (costs, context) = original(), reports()
    if invalid == "not_eligible":
        raw["metadata"]["decision_eligible"] = False
    elif invalid == "different_control":
        context["source_registration"] = "other"
    elif invalid == "missing_bucket":
        context["single_request_prefill_batches_at_least_256_tokens_by_context_after_upper_bound"].pop("8192")
    else:
        context["input_jsonl_prefixes"] = {}
    with pytest.raises(ValueError):
        calibrated_predictor(raw, costs, context, calibration_binding={})


def test_expanded_split_normalization_binds_new_hash_without_rewriting_accuracy():
    raw = original()
    source = {"model_sha256": "a" * 64, "expanded_split": raw["metadata"]["split_contract"],
              "held_out": {"accuracy": .75}}
    result = deployment_evaluation(
        source, raw, original_sha256="a" * 64, calibrated_sha256="b" * 64, binding={"source": "fixed"},
    )
    assert result["split_contract"] == source["expanded_split"]
    assert result["held_out"] == source["held_out"]
    assert result["model_sha256"] == "b" * 64 and source["model_sha256"] == "a" * 64
    assert not result["deployment_changes_prediction_parameters"]
    assert json.loads(json.dumps(result)) == result
    wrong = deepcopy(source)
    wrong["expanded_split"] = {"mode": "other"}
    with pytest.raises(ValueError, match="split"):
        deployment_evaluation(
            wrong, raw, original_sha256="a" * 64, calibrated_sha256="b" * 64, binding={},
        )
