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


def test_fine_bin_and_support_are_explicit_without_dropping_sparse_arms(monkeypatch):
    calls = []

    def decode(path, begin, end, **options):
        calls.append(options)
        return geometry(30, 8)

    monkeypatch.setattr(auditor, "decode_geometry", decode)
    result = auditor.audit(basis(), 10, 20, prefill_work_bin=16, minimum_batches=8)
    assert calls == [{"prefill_work_bin": 16}, {"prefill_work_bin": 16}]
    assert result["comparisons"][0]["matched"]["prefill_geometry"]
    for options in (
        {"prefill_work_bin": 0}, {"minimum_batches": 0}, {"minimum_batches": True},
    ):
        with pytest.raises(ValueError, match="positive"):
            auditor.audit(basis(), 10, 20, **options)


def test_balanced_short_prefill_excludes_large_work_unknown_graphs_and_unequal_weights():
    def group(size, start, graph, mean, count):
        return {
            "batch_size": size, "extend_bin_start": start, "extend_bin_tokens": 16,
            "graph": graph, "mean_ms": mean, "batches": count,
        }

    left = {
        "short": group(1, 16, "false", 20, 10),
        "long": group(1, 4096, "false", 100, 20),
        "multi": group(2, 0, "false", 80, 20),
        "unknown": group(1, 0, "unknown", 900, 30),
        "boundary": group(1, 508, "false", 900, 30),
    }
    right = deepcopy(left)
    right["short"].update(mean_ms=10, batches=2)
    result = auditor.balanced_prefill(left, right, minimum_batches=1, short_only=True)
    assert result["cohorts"] == 1
    assert result["common_batch_weight"] == 2
    assert result["left_worker_mean_ms"] == 20
    assert result["right_worker_mean_ms"] == 10
    assert result["left_to_right_ratio"] == 2
    assert auditor.balanced_prefill(
        left, right, minimum_batches=3, short_only=True,
    )["left_worker_mean_ms"] is None
