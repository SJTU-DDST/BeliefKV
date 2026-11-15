from copy import deepcopy

import pytest

from scripts.prepare_tool_path_oracle_recheck import (
    MODEL_FIELDS, benchmark_paths, label_fingerprints, ranked_artifact,
    ranked_configuration, verify_completion,
)
from tests.test_workflow_work_oracle import oracle_file, source


def control():
    raw = source()
    raw.update(
        admission_order="global_ready_remaining_path", remaining_work_ranking_enabled=False,
        free_dispatch_coalescing_window_ms=2000,
        source_binding={},
    )
    return raw


def configuration():
    return {
        "order": ["full"],
        "common_environment": {
            "WORKFLOW_COMBINATION_ADMISSION": "0",
            "BELIEFKV_WORKFLOW_COMBINATION_ADMISSION": "0",
            "WORKFLOW_WORK_MODEL": "old", "WORKFLOW_WORK_MODEL_SHA256": "old-digest",
            "BELIEFKV_WORKFLOW_WORK_MODEL": "old",
            "BELIEFKV_WORKFLOW_WORK_MODEL_SHA256": "old-digest",
            "TOOL_PREFETCH": "0", "LAYER_HANDOFF": "0",
            "PREDICTED_TOOL_OFFLOAD": "1", "JOIN_PREFETCH_TRIGGER": "observed_eos",
            "HICACHE_SIZE_GB": "400", "BELIEFKV_TOOL_OFFLOAD_INTERVAL_MS": "400",
        },
        "comparison_contract": {
            "workflow_work_ranking": False,
            "minimum_promising_measured_mean_JCT_reduction": .01,
            "common_window_output_throughput_noninferiority_fraction": .98,
        },
        "workflow_arrivals": {"sha256": "arrivals"},
        "private_analysis_cohort": {"sha256": "fixed18", "delivered_to_scheduler": False},
    }


def test_exact_control_labels_are_retained_with_only_ranking_enabled():
    old = control()
    before = deepcopy(old)
    candidate = ranked_artifact(old, old)
    assert {k: v for k, v in candidate.items() if k != "remaining_work_ranking_enabled"} == {
        k: v for k, v in old.items() if k != "remaining_work_ranking_enabled"
    }
    assert candidate["remaining_work_ranking_enabled"] is True
    assert label_fingerprints(old) == label_fingerprints(candidate)
    assert old == before


@pytest.mark.parametrize("field", (
    "requests", "workflows", "workflow_graphs", "source_binding",
    "model_seconds_per_work_token",
))
def test_other_population_or_calibration_cannot_supply_the_candidate(field):
    old = control()
    reference = deepcopy(old)
    reference[field] = "changed"
    with pytest.raises(ValueError, match="same complete labels"):
        ranked_artifact(old, reference)


@pytest.mark.parametrize("field,value", (
    ("kind", "workflow_continuous_model_work_combination_oracle_v1"),
    ("admission_order", "r5_combination_only"),
    ("free_dispatch_coalescing_window_ms", 1000),
    ("remaining_work_ranking_enabled", True),
    ("future_information_used", False),
    ("deployable", True),
))
def test_incompatible_or_unqualified_control_is_rejected(field, value):
    old = control()
    old[field] = value
    with pytest.raises(ValueError, match="disabled tool-path control"):
        ranked_artifact(old, old)


def test_configuration_preserves_arrivals_resources_and_all_other_environment(tmp_path):
    old = configuration()
    before = deepcopy(old)
    new = ranked_configuration(old, tmp_path / "work.json", "digest")
    assert {k: v for k, v in new["common_environment"].items() if k not in MODEL_FIELDS} == {
        k: v for k, v in old["common_environment"].items() if k not in MODEL_FIELDS
    }
    assert new["workflow_arrivals"] == old["workflow_arrivals"]
    assert new["private_analysis_cohort"] == old["private_analysis_cohort"]
    assert new["comparison_contract"]["minimum_promising_measured_mean_JCT_reduction"] == .01
    assert new["comparison_contract"]["common_window_output_throughput_noninferiority_fraction"] == .98
    assert new["comparison_contract"]["ranking_gain_isolated"] is False
    assert new["gpu_execution"]["registered"] is False
    assert old == before


def complete_proof():
    cohort = {"measured_workflow_ids": ["wf" + str(index) for index in range(18)]}
    work = {"request": [100, 10, "prompt-sha", "output-sha"]}
    return cohort, {
        "registration_sha256": "reg", "cohort_sha256": "cohort", "cohort": cohort,
        "protocol": "all_fixed_measured_workflows_completed", "actual_owned_exit_verified": True,
        "work": work, "measured_work": deepcopy(work), "measured_requests": 1,
    }


def test_complete_measurement_does_not_require_all_background_to_finish():
    cohort, proof = complete_proof()
    proof["all_workflows_completed"] = False
    verify_completion(proof, "reg", cohort, "cohort")


@pytest.mark.parametrize("field,value", (
    ("registration_sha256", "another"), ("cohort_sha256", "another"),
    ("actual_owned_exit_verified", False), ("measured_work", {}),
    ("work", {"other": []}), ("measured_requests", 0),
))
def test_missing_or_changed_completion_is_rejected(field, value):
    cohort, proof = complete_proof()
    proof[field] = value
    with pytest.raises(ValueError, match="complete fixed18"):
        verify_completion(proof, "reg", cohort, "cohort")


def test_actual_path_provider_is_equal_to_literal_and_only_rebuilds_on_change(tmp_path):
    from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracle, scan_remaining_paths

    raw = control()
    raw["source_binding"] = {}
    candidate = ranked_artifact(raw, raw)
    path, digest = oracle_file(tmp_path, candidate)
    result = benchmark_paths(path, digest, WorkflowWorkOracle, scan_remaining_paths)
    assert result["counts"]["path_evaluations"] == 1
    assert result["counts"]["path_cache_hits"] == 5
    assert result["counts"]["path_queries"] == 6
    assert result["initial_workflow_scores"]["wf"]["model_tool_path_s"] == pytest.approx(.94)
    assert result["scope"]["JCT_simulation_or_performance_acceptance"] is False


def test_fingerprints_bind_each_dependency_and_tool_duration():
    raw = control()
    raw["source_binding"] = {}
    changed = deepcopy(raw)
    changed["workflow_graphs"]["wf"][-1]["delay_s"] += .001
    assert label_fingerprints(raw)["workflow_graphs"] != label_fingerprints(changed)["workflow_graphs"]
