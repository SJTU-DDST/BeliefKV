from copy import deepcopy

import pytest

from scripts.plot_causal_decode_diagnostics import weighted_cdf
from scripts.plot_workflow_cohort_comparison import validate_reports


def report():
    return {
        "cohort": {
            "schema_version": 2, "measured_workflow_ids": ["short", "long"],
            "throughput_window_offset_seconds": [100., 150.],
        },
        "all_measured_completed": True, "normal_tails_removed": False,
        "measured_service_output_matches_complete_receipts": True,
        "measured_workflow_JCT_ms": {"short": 1000., "long": 100_000.},
        "common_window_throughput_bins": [
            {"start_s": 100., "end_s": 130., "decode_tokens": 300,
             "decode_tokens_per_second": 10.},
            {"start_s": 130., "end_s": 150., "decode_tokens": 400,
             "decode_tokens_per_second": 20.},
        ],
        "common_window_decode_tokens_per_second": 14.,
    }


def test_partial_last_bin_has_its_actual_time_weight():
    raw = report()
    validate_reports({"one": raw})
    x, y = weighted_cdf([
        (row["decode_tokens_per_second"], row["end_s"] - row["start_s"])
        for row in raw["common_window_throughput_bins"]
    ])
    assert x == [10., 20.]
    assert y == [.6, 1.]


@pytest.mark.parametrize("change", ("unfinished", "tail", "membership", "gap", "tokens", "window"))
def test_incomplete_or_unmatched_measurement_is_rejected(change):
    raw, changed = report(), deepcopy(report())
    if change == "unfinished":
        changed["all_measured_completed"] = False
    elif change == "tail":
        changed["normal_tails_removed"] = True
    elif change == "membership":
        changed["measured_workflow_JCT_ms"].pop("long")
    elif change == "gap":
        changed["common_window_throughput_bins"][1]["start_s"] += 1.
    elif change == "tokens":
        changed["common_window_throughput_bins"][1]["decode_tokens"] += 1
    else:
        changed["cohort"]["throughput_window_offset_seconds"][1] += 10.
    with pytest.raises(ValueError):
        validate_reports({"one": raw, "two": changed})
