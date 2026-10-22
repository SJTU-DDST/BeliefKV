from copy import deepcopy

import pytest

from scripts.analyze_matched_prefill_phases import compare


def arm(times, prefixes, *, missing=None, neighbor=True):
    samples, short = [], []
    for index, (elapsed, prefix) in enumerate(zip(times, prefixes, strict=True)):
        samples.append({
            "service_sample_id": str(index), "batch_size": 1,
            "extend_tokens": 32, "prefix_tokens": prefix, "graph": "true", "loop": "overlap",
            "phases": {
                "run_batch": {"wall_ms": elapsed, "thread_cpu_ms": elapsed},
                "post_launch_physical": {} if missing == index else {"wall_ms": 1.0},
            },
            "metrics": {},
        })
        if neighbor:
            short.append({
                "sample_id": str(index), "extend_tokens": 32, "prefix_tokens": prefix,
                "previous_phase": "decode", "previous_batch_size": 8,
                "next_phase": "decode", "next_batch_size": 8,
            })
    return {"turnover": {"samples": samples}, "short_prefill": {"samples": short}}


def test_opposing_cohort_populations_do_not_turn_composition_into_a_phase_slowdown():
    left = arm([10] * 9 + [30] * 3, [0] * 9 + [8192] * 3)
    right = arm([10] * 3 + [30] * 9, [0] * 3 + [8192] * 9)
    result = compare(left, right)
    metric = result["metrics"]["run_batch.thread_cpu_ms"]
    assert metric["left"] == metric["right"] == 20
    assert metric["ratio"] == 1
    assert metric["weight_sum"] == 6
    assert result["matched_geometry_samples"] == {"left": 12, "right": 12}


def test_absent_timings_have_their_own_coverage_and_are_not_zero_filled():
    left = arm([10] * 3, [0] * 3, missing=0)
    right = arm([10] * 3, [0] * 3)
    result = compare(left, right)
    assert result["metrics"]["run_batch.wall_ms"]["weight_sum"] == 3
    assert result["metrics"]["post_launch_physical.wall_ms"]["left"] is None
    assert result["metrics"]["post_launch_physical.wall_ms"]["weight_sum"] == 0


def test_broad_bins_keep_the_actual_work_difference_visible():
    left = arm([10] * 3, [0] * 3)
    right = deepcopy(left)
    for row in left["turnover"]["samples"]:
        row["extend_tokens"] = 320
    result = compare(left, right, work_bin=512)
    cohort = result["metrics"]["run_batch.wall_ms"]["cohorts"][0]
    assert cohort["actual_geometry"]["left"]["extend_tokens"]["mean"] == 320
    assert cohort["actual_geometry"]["right"]["extend_tokens"]["mean"] == 32
    assert compare(left, right, work_bin=64)["geometry_cohort_count"] == 0


def test_sparse_or_missing_neighbors_stay_unmatched():
    left = arm([10] * 3, [0] * 3, neighbor=False)
    right = arm([10] * 3, [0] * 3)
    result = compare(left, right, neighbors=True)
    assert result["geometry_cohort_count"] == 0
    assert result["excluded"]["left"] == {"neighbor_evidence_missing": 3}
    assert all(row["ratio"] is None for row in result["metrics"].values())


@pytest.mark.parametrize("fault", ("duplicate", "neighbor_geometry", "negative_cpu", "nan_cpu"))
def test_corrupt_sample_or_timing_evidence_is_rejected(fault):
    left = arm([10] * 3, [0] * 3)
    right = deepcopy(left)
    if fault == "duplicate":
        left["turnover"]["samples"].append(left["turnover"]["samples"][0])
    elif fault == "neighbor_geometry":
        left["short_prefill"]["samples"][0]["prefix_tokens"] = 1
    else:
        left["turnover"]["samples"][0]["phases"]["run_batch"]["thread_cpu_ms"] = (
            -1 if fault == "negative_cpu" else float("nan")
        )
    with pytest.raises(ValueError):
        compare(left, right, neighbors=True)
