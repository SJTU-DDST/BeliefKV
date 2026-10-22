import json

import pytest

from beliefkv.metrics.workload_compatibility import classify_workload, compare_workload
from scripts.audit_ablation_workload_after_completion import build_report


def work(scale=1.):
    return {name: value * scale for name, value in {
        "input_tokens": 1000000, "output_tokens": 10000,
        "llm_requests": 100, "tool_calls": 120,
    }.items()}


def test_fifteen_percent_less_work_cannot_qualify_a_direct_jct_comparison():
    result = compare_workload(work(.85), work())
    assert result["status"] == "workload_mismatch"
    assert not result["necessary_workload_condition_passed"]
    assert not result["direct_jct_speedup_accepted"]
    assert result["metrics"]["output_tokens"]["left_vs_right_change_percent"] == pytest.approx(-15.)


def test_close_output_does_not_hide_request_or_long_context_work_difference():
    candidate = work()
    candidate["llm_requests"] = 85
    candidate["input_tokens"] = 750000
    result = compare_workload(candidate, work())
    assert result["metrics"]["output_tokens"]["within_tolerance"]
    assert not result["necessary_workload_condition_passed"]


def test_close_work_is_only_a_necessary_condition_and_is_symmetric():
    left, right = work(.96), work()
    forward, reverse = compare_workload(left, right), compare_workload(right, left)
    assert forward["necessary_workload_condition_passed"]
    assert reverse["necessary_workload_condition_passed"]
    assert forward["log_ratio_distance"] == pytest.approx(reverse["log_ratio_distance"])
    assert not forward["direct_jct_speedup_accepted"]


@pytest.mark.parametrize("invalid", (None, -1, float("nan"), float("inf"), True))
def test_missing_or_invalid_token_counts_never_qualify(invalid):
    candidate = work()
    candidate["output_tokens"] = invalid
    assert compare_workload(candidate, work())["status"] == "workload_evidence_missing"


def test_closer_to_a_reference_can_still_be_close_to_neither():
    result = classify_workload(work(1.1), {"v123_full": work(), "native": work(1.4)})
    assert result["closest_reference"] == "v123_full"
    assert result["close_references"] == []


def test_equal_distances_remain_a_tie():
    assert classify_workload(work(), {"v123_full": work(), "native": work()})[
        "closest_reference"
    ] == "tie"


def test_zero_work_cannot_acquire_a_nearest_reference_from_a_log_ratio():
    result = classify_workload(work(0.), {"v123_full": work(), "native": work()})
    assert result["closest_reference"] is None
    assert not result["close_references"]
    assert not compare_workload(work(0.), work(0.))["necessary_workload_condition_passed"]


@pytest.mark.parametrize("tolerance", (0, -1, 1, float("nan"), True))
def test_invalid_tolerances_are_rejected(tolerance):
    with pytest.raises(ValueError):
        compare_workload(work(), work(), tolerance=tolerance)


def test_matrix_audit_waits_for_all_arms_and_never_counts_historical_full_as_a_vote(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "module_comparison.json").write_text(json.dumps({"runs": {
        "full": {**work(1.4), "jct_mean_seconds": 100.},
        "one": {**work(), "jct_mean_seconds": 100.},
        "two": {**work(1.02), "jct_mean_seconds": 101.},
        "three": {**work(1.4), "jct_mean_seconds": 102.},
    }}))
    plan = {"run_root": str(run), "order": ["one", "two", "three"]}
    state = {"status": "running_three", "completed_arms": ["one", "two"]}
    refs = {"v123_full": work(), "native": work(1.4)}
    partial = build_report(plan, state, refs)
    assert partial["majority_closer_to"] is None
    assert partial["decision"] == "wait_for_remaining_ablations_no_rerun_decision"
    assert "full" not in partial["arms"]
    state.update(status="collected_performance_review_pending", completed_arms=plan["order"])
    complete = build_report(plan, state, refs)
    assert complete["majority_closer_to"] == "v123_full"
    assert complete["closer_reference_counts"] == {"v123_full": 2, "native": 1}
    assert complete["no_gpu_experiment_launched"] is True
    assert complete["performance_accepted"] is False
