from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts.prepare_workflow_benefit_only import benefit_only_artifact
from scripts.review_workflow_objective_pair import verify_objective_pair
from tests.test_learned_work_ranking_review import learned_pair


def pair(tmp_path):
    _control, reference = learned_pair(tmp_path)
    path = Path(reference["common_environment"]["WORKFLOW_WORK_MODEL"])
    original = json.loads(path.read_bytes())
    profile = original["admission_cost_profile"]
    profile["identity"] = hashlib.sha256(json.dumps({
        "costs": profile["costs"], "capacity_summary_kind": profile["kind"],
    }, sort_keys=True).encode()).hexdigest()
    path.write_text(json.dumps(original))
    for prefix in ("", "BELIEFKV_"):
        reference["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = sha256_file(path)
    candidate = deepcopy(reference)
    target = tmp_path / "benefit.json"
    target.write_text(json.dumps(benefit_only_artifact(original)))
    for prefix in ("", "BELIEFKV_"):
        candidate["common_environment"].update({
            prefix + "WORKFLOW_WORK_MODEL": str(target),
            prefix + "WORKFLOW_WORK_MODEL_SHA256": sha256_file(target),
        })
    candidate["comparison_contract"].update(
        combination_selection_objective="benefit", only_combination_selection_objective_changed=True,
        cost_tie_break_added=False, prediction_parameters_and_support_unchanged=True,
    )
    return reference, candidate


def test_objective_pair_accepts_only_the_new_score_without_modifying_inputs(tmp_path):
    reference, candidate = pair(tmp_path)
    before = deepcopy((reference, candidate))
    assert len(verify_objective_pair(reference, candidate)) == 2
    assert (reference, candidate) == before


@pytest.mark.parametrize("change", (
    "prediction", "cost", "input", "profile", "fingerprint", "tie", "declaration", "alias", "threshold", "arrival",
))
def test_objective_pair_rejects_other_model_policy_or_measurement_changes(tmp_path, change):
    reference, candidate = pair(tmp_path)
    if change == "tie":
        candidate["comparison_contract"]["cost_tie_break_added"] = True
    elif change == "declaration":
        candidate["comparison_contract"]["only_combination_selection_objective_changed"] = False
    elif change == "alias":
        candidate["common_environment"]["BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"] = "f" * 64
    elif change == "threshold":
        candidate["comparison_contract"]["common_window_output_throughput_noninferiority_fraction"] = .5
    elif change == "arrival":
        candidate["workflow_arrivals"] = {"changed": True}
    else:
        path = Path(candidate["common_environment"]["WORKFLOW_WORK_MODEL"])
        raw = json.loads(path.read_bytes())
        if change == "prediction":
            raw["output_bias"][0] += 1.
        elif change == "input":
            raw["features"].append("future_tool_time")
        elif change == "cost":
            raw["admission_cost_profile"]["costs"]["prefill_ms_per_token"] *= 2
        elif change == "profile":
            raw["admission_cost_profile"]["kind"] = "r5_frozen_batch_cost_proxy_capacity_v6"
        else:
            raw["admission_cost_profile"]["identity"] = "f" * 64
        path.write_text(json.dumps(raw))
        for prefix in ("", "BELIEFKV_"):
            candidate["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = sha256_file(path)
    with pytest.raises(ValueError):
        verify_objective_pair(reference, candidate)
