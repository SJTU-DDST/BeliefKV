import json
from pathlib import Path

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts.prepare_calibrated_combination_oracle import calibrated_artifact
from scripts.review_matched_work_ranking import (
    verify_calibrated_combination_pair, verify_ranking_only_pair,
)
from tests.test_matched_work_ranking_review import pair
from tests.test_workflow_work_oracle import source


def calibration():
    return {
        "configured_oracle": {"prefill_token_weight": .01, "model_seconds_per_work_token": .02},
        "scope": {"same_completed_control_not_partial_oracle": True},
        "completed_control_weighted_decode_step_ms": 23.,
        "completed_control_cost_proxy": {
            "prefill_ms_per_token": .08, "h2d_ms_per_token": .004,
            "decode_ms_by_width": [5. + i for i in range(24)],
            "reference_context_tokens": 40000.,
        },
    }


def original():
    raw = source()
    raw.update(
        remaining_work_ranking_enabled=True, admission_order="global_ready_remaining_path",
        free_dispatch_coalescing_window_ms=2000,
    )
    return raw


def artifact(raw=None, report=None):
    return calibrated_artifact(
        original() if raw is None else raw,
        calibration() if report is None else report,
        calibration_path="/completed/control/costs.json", calibration_sha256="a" * 64,
    )


def test_calibration_preserves_exact_source_work_graph_tools_and_ordinary_window():
    before = original()
    after = artifact(before)
    for name in ("requests", "workflows", "workflow_graphs", "work_thresholds", "prefill_token_weight"):
        assert after[name] == before[name]
    assert before == original()
    assert after["model_seconds_per_work_token"] == .023
    assert after["model_prefill_seconds_per_token"] == .00008
    assert after["model_prefill_progress"] == "first_observed_output"
    assert after["free_dispatch_coalescing_window_ms"] == 2000
    assert after["admission_cost_profile"]["oracle_reference_path_s"] == 600.


@pytest.mark.parametrize("invalid", ("wrong_coefficient", "partial", "zero", "nan", "boolean", "curve"))
def test_calibration_refuses_partial_or_inconsistent_cost_support(invalid):
    report = calibration()
    if invalid == "wrong_coefficient":
        report["configured_oracle"]["model_seconds_per_work_token"] = .03
    elif invalid == "partial":
        report["scope"]["same_completed_control_not_partial_oracle"] = False
    elif invalid == "curve":
        report["completed_control_cost_proxy"]["decode_ms_by_width"] = [10., 5.]
    else:
        report["completed_control_weighted_decode_step_ms"] = {
            "zero": 0., "nan": float("nan"), "boolean": True,
        }[invalid]
    with pytest.raises(ValueError):
        artifact(report=report)


def configured_pair(tmp_path):
    control, oracle = pair(tmp_path)
    for spec, raw in (
        (control, {**original(), "remaining_work_ranking_enabled": False}),
        (oracle, artifact()),
    ):
        common = spec["common_environment"]
        path = Path(common["WORKFLOW_WORK_MODEL"])
        path.write_text(json.dumps(raw))
        common["WORKFLOW_WORK_MODEL_SHA256"] = common["BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"] = (
            sha256_file(path)
        )
    oracle["common_environment"].update(
        WORKFLOW_COMBINATION_ADMISSION="1", BELIEFKV_WORKFLOW_COMBINATION_ADMISSION="1",
    )
    oracle["comparison_contract"].update(
        matched_pair_changes_only_remaining_work_ranking=False,
        oracle_cost_and_set_selection_are_joint_change=True,
    )
    return control, oracle


def test_combined_comparison_is_accepted_only_with_its_explicit_joint_change_label(tmp_path):
    control, oracle = configured_pair(tmp_path)
    assert len(verify_calibrated_combination_pair(control, oracle)) == 2
    with pytest.raises(ValueError):
        verify_ranking_only_pair(control, oracle)


@pytest.mark.parametrize("invalid", ("tools", "host", "intrinsic", "alias", "window", "threshold", "hidden"))
def test_joint_comparison_still_rejects_changed_workload_and_hidden_changes(tmp_path, invalid):
    control, oracle = configured_pair(tmp_path)
    if invalid == "tools":
        oracle["tool_timing_overlay"] = {"sha256": "changed"}
    elif invalid == "host":
        oracle["common_environment"]["HOST_GB"] = "256"
    elif invalid == "alias":
        oracle["common_environment"]["BELIEFKV_WORKFLOW_COMBINATION_ADMISSION"] = "0"
    elif invalid == "threshold":
        oracle["comparison_contract"]["minimum_promising_measured_mean_JCT_reduction"] = .01
    elif invalid == "hidden":
        oracle["comparison_contract"]["matched_pair_changes_only_remaining_work_ranking"] = True
    else:
        path = Path(oracle["common_environment"]["WORKFLOW_WORK_MODEL"])
        raw = json.loads(path.read_bytes())
        if invalid == "intrinsic":
            raw["work_thresholds"] = [4., 5.]
        else:
            raw["free_dispatch_coalescing_window_ms"] = 1000
        path.write_text(json.dumps(raw))
        oracle["common_environment"]["WORKFLOW_WORK_MODEL_SHA256"] = (
            oracle["common_environment"]["BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"]
        ) = sha256_file(path)
    with pytest.raises(ValueError):
        verify_calibrated_combination_pair(control, oracle)
