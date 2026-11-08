from copy import deepcopy
import math

import pytest

from beliefkv.experiments.workflow_cohort import summarize_cohort
from scripts.compare_closed_workflow_window import extract_window, match_completed_report
from tests.test_workflow_cohort import fixture


def test_closed_window_matches_all_complete_cohort_bins_and_rates_without_JCT():
    arrivals, cohort, events, samples = fixture()
    expected = summarize_cohort(cohort, arrivals, events, samples, bin_seconds=5.)
    result = extract_window(cohort, arrivals, events, samples, bin_seconds=5.)
    match_completed_report(result, expected, cohort)
    assert result["output_tokens"] == 3
    assert result["decode_tokens"] == 2
    assert result["JCT_values_reported"] is False
    assert all("JCT" not in key for key in result if key != "JCT_values_reported")
    assert result["bins"][-1]["end_s"] - result["bins"][-1]["start_s"] < 5.


def test_window_can_close_before_measured_workflows_finish():
    arrivals, cohort, events, samples = fixture()
    events = [row for row in events if not (
        row["kind"] == "workflow_end" and row["workflow_id"] in cohort["measured_workflow_ids"]
    )]
    result = extract_window(cohort, arrivals, events, samples)
    assert result["observed_measured_ends"] == 0
    assert result["output_tokens"] == 3
    with pytest.raises(ValueError, match="must finish"):
        summarize_cohort(cohort, arrivals, events, samples)


def test_partial_native_window_is_rejected_instead_of_changing_its_denominator():
    arrivals, cohort, events, samples = fixture()
    with pytest.raises(ValueError, match="whole fixed common window"):
        extract_window(cohort, arrivals, events, samples[:-1])


@pytest.mark.parametrize("change", (
    "input_instead_of_output", "negative_output", "nonfinite_time", "duplicate",
    "reverse_time", "clock", "missing_arrival",
))
def test_ambiguous_output_clocks_and_membership_are_rejected(change):
    arrivals, cohort, events, samples = fixture()
    if change == "input_instead_of_output":
        samples[0]["request_samples"][0].pop("output_token_delta")
    elif change == "negative_output":
        samples[0]["request_samples"][0]["output_token_delta"] = -1
    elif change == "nonfinite_time":
        samples[0]["complete_monotonic_ms"] = math.nan
    elif change == "duplicate":
        samples.append(deepcopy(samples[-1]))
    elif change == "reverse_time":
        samples[0], samples[-1] = samples[-1], samples[0]
    elif change == "clock":
        samples[-1]["monotonic_clock_domain"] = "another"
    else:
        events = [row for row in events if row["workflow_id"] != "w4"]
    with pytest.raises(ValueError):
        extract_window(cohort, arrivals, events, samples)


def test_reference_report_cannot_silently_change_counts_or_cohort():
    arrivals, cohort, events, samples = fixture()
    report = summarize_cohort(cohort, arrivals, events, samples)
    result = extract_window(cohort, arrivals, events, samples)
    report["common_window_throughput_bins"][0]["decode_tokens"] += 1
    with pytest.raises(ValueError, match="differs"):
        match_completed_report(result, report, cohort)
