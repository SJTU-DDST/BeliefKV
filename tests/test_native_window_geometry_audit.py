from copy import deepcopy
from pathlib import Path

import pytest

from scripts import audit_native_window_geometry as auditor


def basis():
    return {"runs": {
        name: {
            "run": "/runs/" + name,
            "clock_alignment": {
                "anchor_unix_ms": anchor, "native_client_clock_validated": True,
            },
            "windows": [{
                "start_seconds": 10, "end_seconds": 20,
                "service_window_observed_complete": True,
            }],
        }
        for name, anchor in (("diagnostic", 1000), ("native", 2000))
    }}


def geometry(mean, count=32):
    cohort = {"48/20480/true": {"batches": count, "mean_ms": mean}}
    return {
        "cohorts": deepcopy(cohort), "cuda_graph_cohorts": deepcopy(cohort),
        "prefill_geometry": {"cohorts": deepcopy(cohort)},
        "worker_interval_neighbors": {"cohorts": deepcopy(cohort)},
    }


def test_each_arm_uses_its_verified_clock_and_all_cohorts_are_retained(monkeypatch):
    calls = []

    def decode(path, begin, end):
        calls.append((path, begin, end))
        return geometry(60 if path.name == "diagnostic" else 30)

    monkeypatch.setattr(auditor, "decode_geometry", decode)
    inputs, expected = basis(), basis()
    result = auditor.audit(inputs, 10, 20)
    assert inputs == expected
    assert calls == [
        (Path("/runs/diagnostic"), 11000, 21000),
        (Path("/runs/native"), 12000, 22000),
    ]
    assert result["arms"]["diagnostic"]["monitor_window"] == {
        "begin_ts_ms": 11000, "end_ts_ms": 21000, "clock_source": "validated_clock_report",
    }
    [pair] = result["comparisons"]
    assert (pair["left"], pair["right"]) == ("diagnostic", "native")
    assert pair["matched"]["cuda_graph_cohorts"]["48/20480/true"][
        "left_to_right_worker_mean_ratio"
    ] == 2


def test_sparse_or_unmatched_bins_remain_in_arms_without_becoming_matched(monkeypatch):
    monkeypatch.setattr(
        auditor, "decode_geometry",
        lambda path, *clock: geometry(30, 31 if path.name == "native" else 32),
    )
    result = auditor.audit(basis(), 10, 20)
    assert result["arms"]["native"]["cohorts"]
    assert all(not rows for rows in result["comparisons"][0]["matched"].values())


@pytest.mark.parametrize("fault", [
    "unverified_clock", "nonfinite_anchor", "incomplete_window",
    "missing_window", "duplicate_window", "empty_arms",
])
def test_invalid_clock_or_coverage_is_rejected_before_reading_raw_files(monkeypatch, fault):
    def unexpected(*args):
        raise AssertionError("no raw analysis before all arms qualify")

    monkeypatch.setattr(auditor, "decode_geometry", unexpected)
    inputs = basis()
    arm = inputs["runs"]["native"]
    if fault == "unverified_clock":
        arm["clock_alignment"]["native_client_clock_validated"] = False
    elif fault == "nonfinite_anchor":
        arm["clock_alignment"]["anchor_unix_ms"] = float("nan")
    elif fault == "incomplete_window":
        arm["windows"][0]["service_window_observed_complete"] = False
    elif fault == "missing_window":
        arm["windows"] = []
    elif fault == "duplicate_window":
        arm["windows"] *= 2
    elif fault == "empty_arms":
        inputs["runs"] = {}
    with pytest.raises(ValueError):
        auditor.audit(inputs, 10, 20)


@pytest.mark.parametrize("start,end", [(-1, 20), (20, 20), (30, 20), (10, float("inf"))])
def test_invalid_windows_are_rejected(start, end):
    with pytest.raises(ValueError):
        auditor.audit(basis(), start, end)
