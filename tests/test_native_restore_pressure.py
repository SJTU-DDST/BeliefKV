import pytest

from scripts.audit_native_restore_pressure import (
    counter_delta,
    matched_cohorts,
    sampled_pressure,
)


def test_sample_clock_offset_and_half_open_window_preserve_unknowns():
    result = sampled_pressure(
        [
            {"clock": 0, "value": 1}, {"clock": 10, "value": 2},
            {"clock": 15}, {"clock": 20, "value": 999},
        ],
        110, 120, clock_field="clock", offset=100,
        fields={"pressure": lambda row: row.get("value")},
    )
    assert result["samples"] == 2
    assert result["fields"]["pressure"]["values"]["p50"] == 2
    assert result["fields"]["pressure"]["unknown_samples"] == 1
    assert [row["clock"] for row in result["boundary_rows"]] == [10, 20]


def test_interleaved_evictions_are_neither_pool_samples_nor_coverage():
    rows = [
        {"event": "host_pool_usage", "clock": 0, "value": 1},
        {"event": "host_pool_usage", "clock": 15, "value": 2},
        {"event": "eviction", "clock": 17},
        {"event": "eviction", "clock": 30},
    ]
    kwargs = {
        "clock_field": "clock", "event_kind": "host_pool_usage",
        "fields": {"occupancy": lambda row: row.get("value")},
    }
    with pytest.raises(ValueError, match="fully observed"):
        sampled_pressure(rows, 10, 20, **kwargs)
    rows.append({"event": "host_pool_usage", "clock": 25, "value": 3})
    result = sampled_pressure(rows, 10, 20, **kwargs)
    assert result["samples"] == 1
    assert result["fields"]["occupancy"]["unknown_samples"] == 0
    assert result["observed_through_ts_ms"] == 25
    assert result["boundary_rows"][1]["clock"] == 15


@pytest.mark.parametrize("rows", [
    [], [{"clock": 11}], [{"clock": 15}, {"clock": 21}],
    [{"clock": 0}, {"clock": float("nan")}],
])
def test_missing_boundary_or_invalid_clock_is_not_complete_coverage(rows):
    with pytest.raises(ValueError):
        sampled_pressure(rows, 10, 20, clock_field="clock", fields={})


def test_reclamation_delta_requires_enabled_and_compatible_counters():
    initial = {"enabled": True, "counts": {"freed": 3, "attempted": 5}}
    final = {"enabled": True, "counts": {"freed": 7, "attempted": 8}}
    assert counter_delta(initial, final)["counts"] == {"freed": 4, "attempted": 3}
    assert counter_delta(None, final)["available"] is False
    assert counter_delta({"enabled": False}, final)["available"] is False
    with pytest.raises(ValueError, match="reset"):
        counter_delta(final, initial)
    with pytest.raises(ValueError, match="schema"):
        counter_delta(initial, {"enabled": True, "counts": {"freed": 7}})


def test_matched_cohorts_do_not_impute_missing_or_sparse_groups():
    left = {
        "48/0": {"batches": 40, "mean_ms": 20},
        "48/2048": {"batches": 31, "mean_ms": 50},
        "48/4096": {"batches": 40, "mean_ms": 100},
    }
    right = {
        "48/0": {"batches": 35, "mean_ms": 10},
        "48/2048": {"batches": 40, "mean_ms": 10},
    }
    assert matched_cohorts(left, right) == {
        "48/0": {
            "candidate": left["48/0"], "reference": right["48/0"],
            "candidate_mean_ratio": 2,
        },
    }
