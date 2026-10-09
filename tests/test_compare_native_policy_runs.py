import pytest

from scripts.compare_native_policy_runs import (
    interval_milliseconds, last_runtime_state, percentile, phase_statistics, transfer_parts,
)


def test_split_transfer_conserves_bytes_and_native_remainder():
    row = {
        "actual_bytes": 122880, "num_tokens_by_pool": {"kv": 6},
        "tagged_child_commits": [{"command_id": "prefetch"}],
    }
    ack = {"action": "PREFETCH_GPU", "num_bytes": 40960, "pool_bytes": [["kv", 40960]]}
    assert transfer_parts(row, {"prefetch": ack}, {"kv": 20480}) == [
        {"kind": "PREFETCH_GPU", "bytes": 40960, "pool_bytes": {"kv": 40960}},
        {"kind": "native", "bytes": 81920, "pool_bytes": {"kv": 81920}},
    ]


def test_unknown_tagged_receipt_remains_native_not_claimed_predictive():
    row = {
        "actual_bytes": 20480, "num_tokens_by_pool": {"kv": 1},
        "tagged_child_commits": [{"command_id": "unverified"}],
    }
    assert transfer_parts(row, {}, {"kv": 20480}) == [
        {"kind": "native", "bytes": 20480, "pool_bytes": {"kv": 20480}},
    ]


def test_split_transfer_rejects_excess_tagged_credit():
    row = {
        "actual_bytes": 20480, "num_tokens_by_pool": {"kv": 1},
        "tagged_child_commits": [{"command_id": "bad"}],
    }
    with pytest.raises(ValueError):
        transfer_parts(row, {
            "bad": {"action": "PREFETCH_GPU", "num_bytes": 40960, "pool_bytes": [["kv", 40960]]},
        }, {"kv": 20480})


def test_worker_union_clips_and_never_double_counts_overlap():
    rows = [
        {"start_ms": 0, "end_ms": 8},
        {"start_ms": 5, "end_ms": 12},
        {"start_ms": 20, "end_ms": 30},
    ]
    assert interval_milliseconds(rows, 2, 25) == 15
    assert interval_milliseconds(rows, 12, 20) == 0


def test_percentiles_use_all_observations_with_interpolation():
    assert percentile([], .5) is None
    assert percentile([4, 2], .5) == 3
    assert percentile([5], .95) == 5


def test_phase_statistics_preserves_coalesced_batch_counts():
    rows = [
        {"start_ms": 0, "end_ms": 20, "running": 2, "observation_count": 2},
        {"start_ms": 20, "end_ms": 50, "running": 4, "observation_count": 1},
    ]
    stats = phase_statistics(rows)
    assert stats["batch_count"] == 3
    assert stats["mean_batch_size"] == pytest.approx(8 / 3)
    assert stats["worker_interval_seconds"] == .05


def test_last_runtime_state_ignores_partial_head_and_trailing_other_events(tmp_path):
    path = tmp_path / "opportunities.jsonl"
    path.write_text(
        '{"event":"padding","value":"' + "x" * 140000 + '"}\n'
        '{"event":"admission_runtime_state","physical_disabled":false}\n'
        '{"event":"other"}\n'
    )
    assert last_runtime_state(path) == {
        "event": "admission_runtime_state", "physical_disabled": False,
    }
