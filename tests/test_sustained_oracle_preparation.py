from copy import deepcopy

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts.prepare_sustained_oracle_admission import (
    calibrate_costs, validate_decision, validate_workload,
)


def samples():
    batches = [{
        "event": "gpu_service_sample", "phase": "decode",
        "decode_batch_size": width, "service_elapsed_ms": elapsed,
        "request_samples": [{"sequence_tokens_before": 1000}] * width,
    } for width, elapsed in ((1, 10.), (2, 15.))]
    batches += [{
        "event": "gpu_service_sample", "phase": "prefill",
        "service_elapsed_ms": 204.8, "request_samples": [{"token_delta": 2048}],
    }]
    transfers = [{
        "direction": "h2d", "status": "completed",
        "num_tokens_by_pool": {"kv": 2048}, "transfer_stream_elapsed_ms": 2.048,
    }]
    return batches, transfers


def test_cost_profile_uses_input_for_prefill_and_native_stream_for_copy():
    batches, transfers = samples()
    batches[-1]["request_samples"][0]["output_token_delta"] = 1
    transfers[0]["submit_to_ack_ms"] = 999999.
    costs, evidence = calibrate_costs(batches, transfers)
    assert costs["prefill_ms_per_token"] == pytest.approx(.1)
    assert costs["h2d_ms_per_token"] == pytest.approx(.001)
    assert costs["decode_ms_by_width"][:3] == [10., 15., 22.5]
    assert costs["reference_context_tokens"] == 1000.
    assert evidence["observed_decode_widths"] == [1, 2]


def test_nonmonotone_observations_are_declared_as_an_upper_envelope():
    batches, transfers = samples()
    batches[1]["service_elapsed_ms"] = 5.
    costs, _ = calibrate_costs(batches, transfers)
    assert costs["decode_ms_by_width"][:3] == [10., 10., 10.]
    assert costs["decode_ms_by_width"] == sorted(costs["decode_ms_by_width"])


@pytest.mark.parametrize("mutation", ("copy", "prefill", "geometry", "zero"))
def test_missing_cost_support_or_incomplete_geometry_is_not_silently_fabricated(mutation):
    batches, transfers = samples()
    if mutation == "copy":
        transfers.clear()
    elif mutation == "prefill":
        batches.pop()
    elif mutation == "geometry":
        batches[1]["request_samples"].pop()
    else:
        batches[0]["service_elapsed_ms"] = 0.
    with pytest.raises(ValueError):
        calibrate_costs(batches, transfers)


def reviews():
    return (
        {
            "all_workflows_completed": True, "normal_tails_removed": False,
            "service_output_matches_complete_receipts": True,
            "background_submission_coverage": {"covers_all_measured_completions": True},
            "measured_complete_workflow_JCT_ms": {"count": 18},
        },
        {"all_workflows_completed": True, "all_measured_completed": True},
        {"counts": {"cross_workflow_logical_set_competition_controls": 10}},
        {"population": {"model_work_units": {"p80_p20": 3.1}}},
    )


def test_oracle_gate_requires_complete_fixed_cohort_and_repeated_actual_decisions():
    acceptance = validate_workload(*reviews())
    assert acceptance["minimum_actual_periodic_competition_controls"] == 10
    assert not acceptance["full_candidate_and_physical_coverage_proven"]


@pytest.mark.parametrize("mutation", ("tail", "background", "receipt", "population", "competition", "diversity"))
def test_incomplete_workload_or_weak_choice_supply_stays_in_phase_one(mutation):
    cohort, supply, periodic, population = deepcopy(reviews())
    if mutation == "tail":
        cohort["normal_tails_removed"] = True
    elif mutation == "background":
        cohort["background_submission_coverage"]["covers_all_measured_completions"] = False
    elif mutation == "receipt":
        cohort["service_output_matches_complete_receipts"] = False
    elif mutation == "population":
        cohort["measured_complete_workflow_JCT_ms"]["count"] = 17
    elif mutation == "competition":
        periodic["counts"]["cross_workflow_logical_set_competition_controls"] = 9
    else:
        population["population"]["model_work_units"]["p80_p20"] = 2.9
    with pytest.raises(ValueError, match="unqualified"):
        validate_workload(cohort, supply, periodic, population)


def test_oracle_requires_a_passing_phase_one_decision_bound_to_the_same_reviews(tmp_path):
    review = tmp_path / "review.json"
    review.write_text("{}")
    decision = {
        "accepted_for_oracle_diagnostic": True, "failed_checks": [],
        "checks": {"no_loss": True}, "inputs_sha256": {str(review.resolve()): sha256_file(review)},
    }
    validate_decision(decision, [review])
    decision["accepted_for_oracle_diagnostic"] = False
    with pytest.raises(ValueError, match="phase-one"):
        validate_decision(decision, [review])
    decision["accepted_for_oracle_diagnostic"] = True
    review.write_text('{"changed":true}')
    with pytest.raises(ValueError, match="different complete reviews"):
        validate_decision(decision, [review])
