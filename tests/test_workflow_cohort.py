from copy import deepcopy
import math

import pytest

from beliefkv.experiments.replay_arrivals import poisson_arrival_plan
from beliefkv.experiments.workflow_cohort import declare_cohort, summarize_cohort, validate_cohort


def fixture():
    arrivals = poisson_arrival_plan(
        [f"w{i}" for i in range(6)], rate_per_second=.1, seed=31, initial_workflows=1,
    )
    end = arrivals["arrival_schedule"][-1]["offset_seconds"]
    cohort = declare_cohort(arrivals, warmup=1, measured=2, throughput_start_s=0., throughput_end_s=end)
    origin = 10000.
    events = []
    for index, row in enumerate(arrivals["arrival_schedule"]):
        start = origin + row["offset_seconds"] * 1000.
        # The measured long member finishes after later-arriving background workflows.
        duration = 200_000. if index == 1 else 1000.
        events.extend((
            {"kind": "workflow_start", "workflow_id": row["workflow_id"], "ts_ms": start},
            {"kind": "workflow_end", "workflow_id": row["workflow_id"], "ts_ms": start + duration},
        ))
    samples = [{
        "event": "gpu_service_sample", "forward_iter": 1, "phase": "prefill",
        "complete_monotonic_ms": origin + 100., "monotonic_clock_domain": "clock",
        "batch_size": 1, "request_samples": [{
            "workflow_id": "w0", "token_delta": 2048, "output_token_delta": 1,
        }],
    }, {
        "event": "gpu_service_sample", "forward_iter": 2, "phase": "decode",
        "complete_monotonic_ms": origin + arrivals["arrival_schedule"][4]["offset_seconds"] * 1000. + 100.,
        "monotonic_clock_domain": "clock",
        "batch_size": 2, "request_samples": [
            {"workflow_id": "w4", "token_delta": 1, "output_token_delta": 1},
            {"workflow_id": "w4", "token_delta": 1, "output_token_delta": 1},
        ],
    }, {
        "event": "gpu_service_sample", "forward_iter": 3, "phase": "decode",
        "complete_monotonic_ms": origin + end * 1000., "monotonic_clock_domain": "clock",
        "batch_size": 1, "request_samples": [{
            "workflow_id": "w1", "token_delta": 1, "output_token_delta": 1,
        }],
    }]
    return arrivals, cohort, events, samples


def test_fixed_arrival_members_include_long_tails_and_diagnose_background_exhaustion():
    arrivals, cohort, events, samples = fixture()
    result = summarize_cohort(cohort, arrivals, events, samples, bin_seconds=5.)
    assert list(result["measured_workflow_JCT_ms"]) == ["w1", "w2"]
    assert result["measured_complete_workflow_JCT_ms"]["mean"] == pytest.approx(100500.)
    assert result["measured_workflow_JCT_ms"]["w1"] == pytest.approx(200_000.)
    assert not result["background_submission_coverage"]["covers_all_measured_completions"]
    assert result["background_submission_coverage"]["uncovered_drain_seconds"] > 0
    assert result["all_workflows_completed"]
    assert not result["normal_tails_removed"]


def test_common_window_counts_background_outputs_and_prefill_first_tokens():
    arrivals, cohort, events, samples = fixture()
    result = summarize_cohort(cohort, arrivals, events, samples, bin_seconds=5.)
    window = cohort["throughput_window_offset_seconds"][1]
    assert result["common_window_all_workflows_output_tokens"] == 3
    assert result["common_window_output_tokens_per_second"] == pytest.approx(3 / window)
    assert result["common_window_decode_tokens_per_second"] == pytest.approx(2 / window)
    assert result["common_window_decode_batch_counts"] == {2: 1}
    assert sum(row["output_tokens"] for row in result["common_window_throughput_bins"]) == 3
    assert result["all_service_output_tokens"] == 4
    assert result["all_service_output_tokens_by_workflow"] == {"w0": 1, "w4": 2, "w1": 1}
    assert result["output_accounting_field"] == "output_token_delta"


def test_intermediate_prefill_chunk_adds_no_output_despite_nonzero_input():
    arrivals, cohort, events, samples = fixture()
    samples[0]["request_samples"][0]["output_token_delta"] = 0
    result = summarize_cohort(cohort, arrivals, events, samples)
    assert result["common_window_all_workflows_output_tokens"] == 2
    assert result["all_service_output_tokens"] == 3


@pytest.mark.parametrize("value", (None, -1, True, 1., math.nan))
def test_ambiguous_or_invalid_native_output_cannot_fall_back_to_input_delta(value):
    arrivals, cohort, events, samples = fixture()
    if value is None:
        samples[0]["request_samples"][0].pop("output_token_delta")
    else:
        samples[0]["request_samples"][0]["output_token_delta"] = value
    with pytest.raises(ValueError):
        summarize_cohort(cohort, arrivals, events, samples)


@pytest.mark.parametrize("mutation", ("first_completed", "stop_background", "scheduler_label", "window"))
def test_cohort_membership_and_statistics_cannot_change_after_execution(mutation):
    arrivals, cohort, _, _ = fixture()
    changed = deepcopy(cohort)
    if mutation == "first_completed":
        changed["measured_workflow_ids"] = ["w2", "w3"]
    elif mutation == "stop_background":
        changed["background_submission_stops_when_cohort_finishes"] = True
    elif mutation == "scheduler_label":
        changed["cohort_identity_available_to_scheduler"] = True
    else:
        changed["arrival_plan_sha256"] = "wrong"
    with pytest.raises(ValueError):
        validate_cohort(changed, arrivals)


@pytest.mark.parametrize("mutation", ("incomplete_measured", "incomplete_background", "shifted_arrival", "foreign_sample", "duplicate_sample", "clock"))
def test_cohort_audit_rejects_censoring_and_inconsistent_serving_evidence(mutation):
    arrivals, cohort, events, samples = fixture()
    if mutation.startswith("incomplete"):
        target = "w1" if mutation == "incomplete_measured" else "w5"
        events = [row for row in events if not (row["kind"] == "workflow_end" and row["workflow_id"] == target)]
    elif mutation == "shifted_arrival":
        events[2]["ts_ms"] += 1.
    elif mutation == "foreign_sample":
        samples[0]["request_samples"][0]["workflow_id"] = "foreign"
    elif mutation == "duplicate_sample":
        samples.append(samples[0])
    else:
        samples[0]["monotonic_clock_domain"] = "another"
    with pytest.raises(ValueError):
        summarize_cohort(cohort, arrivals, events, samples)


@pytest.mark.parametrize("width", (0., -1., math.nan))
def test_invalid_common_throughput_bins_are_rejected(width):
    arrivals, cohort, events, samples = fixture()
    with pytest.raises(ValueError):
        summarize_cohort(cohort, arrivals, events, samples, bin_seconds=width)
