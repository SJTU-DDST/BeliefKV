from copy import deepcopy

import pytest

from scripts.review_sustained_workload import inspect


def reports():
    return [
        {"complete_work_and_actual_exit_verified": True},
        {
            "cohort": {
                "throughput_window_offset_seconds": [0., 1000.],
                "selection_rule": "fixed_arrival_indices_before_execution",
                "measured_workflow_ids": list(range(18)),
            },
            "all_workflows_completed": True, "normal_tails_removed": False,
            "service_output_matches_complete_receipts": True,
            "measured_complete_workflow_JCT_ms": {"count": 18},
            "background_submission_coverage": {"covers_all_measured_completions": True},
        },
        {
            "all_workflows_completed": True, "all_measured_completed": True,
            "sample_counts": {"common_window": 100, "with_ready_paused": 50},
            "latest_native_counters_not_atomic_with_prefixes": {
                "context_prefix_reuse_evidence": {
                    "counts": {"previously_served_prefix_recompute_proxy_tokens": 0},
                },
            },
        },
        {"counts": {"cross_workflow_logical_set_competition_controls": 10}},
        {
            "regular_sample_interval_s": 60,
            "observations": [{
                "observation_kind": "periodic", "arrival_offset_s": number * 60.,
                "next_request_reusable_prior_union_fraction_of_combined_ceiling": .95,
            } for number in range(12)],
        },
        {"population": {"model_work_units": {"p80_p20": 3.1}}},
    ]


def test_complete_low_loss_workload_with_real_competition_allows_only_an_oracle_diagnostic():
    result = inspect(*reports())
    assert result["accepted_for_oracle_diagnostic"] and not result["failed_checks"]
    assert not result["model_work_p80_p20_ge_4"]
    assert result["scope"]["JOIN_child_shape_limitations_not_waived_by_this_diagnostic"]


@pytest.mark.parametrize("failure", (
    "truncated_tail", "wrong_output", "uncovered_tail", "no_competition",
    "no_supply", "lost_prefix", "unknown_loss", "overcapacity", "only_loss_probes",
))
def test_diversity_alone_does_not_qualify_a_workload(failure):
    values = deepcopy(reports())
    if failure == "truncated_tail":
        values[1]["normal_tails_removed"] = True
    elif failure == "wrong_output":
        values[1]["service_output_matches_complete_receipts"] = False
    elif failure == "uncovered_tail":
        values[1]["background_submission_coverage"]["covers_all_measured_completions"] = False
    elif failure == "no_competition":
        values[3]["counts"]["cross_workflow_logical_set_competition_controls"] = 9
    elif failure == "no_supply":
        values[2]["sample_counts"]["with_ready_paused"] = 0
    elif failure in ("lost_prefix", "unknown_loss"):
        values[2]["latest_native_counters_not_atomic_with_prefixes"][
            "context_prefix_reuse_evidence"
        ]["counts"]["previously_served_prefix_recompute_proxy_tokens"] = 1 if failure == "lost_prefix" else None
    elif failure == "overcapacity":
        values[4]["observations"][5]["next_request_reusable_prior_union_fraction_of_combined_ceiling"] = 1.1
    else:
        for row in values[4]["observations"]:
            row["observation_kind"] = "prefix_loss_probe"
    result = inspect(*values)
    assert not result["accepted_for_oracle_diagnostic"] and result["failed_checks"]
