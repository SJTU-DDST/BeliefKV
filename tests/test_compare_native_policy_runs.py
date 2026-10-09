import pytest

from scripts.compare_native_policy_runs import (
    h2d_source_parts, interval_milliseconds, last_runtime_state, percentile,
    phase_statistics, transfer_parts, restore_wait_statistics,
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


def test_source_parts_keep_handoff_and_unknown_receipts_out_of_predictive_bytes():
    row = {
        "actual_bytes": 360, "num_tokens_by_pool": {"kv": 16, "mamba": 2},
        "tagged_child_commits": [
            {"command_id": "join", "num_bytes": 30, "num_tokens_by_pool": {"kv": 3}},
            {"command_id": "tool", "num_bytes": 40, "num_tokens_by_pool": {"kv": 4}},
            {"command_id": "handoff", "num_bytes": 120, "num_tokens_by_pool": {"kv": 2, "mamba": 1}},
            {"command_id": "unknown", "num_bytes": 10, "num_tokens_by_pool": {"kv": 1}},
            {"command_id": "missing_ack", "num_bytes": 40, "num_tokens_by_pool": {"kv": 4}},
        ],
    }
    parts = h2d_source_parts(row, {
        "join": "join_ticket", "tool": "tool_wait", "handoff": "execution_handoff",
        "unknown": None,
    }, {"kv": 10, "mamba": 100})
    by_category = {part["category"]: part for part in parts}
    assert by_category["predictive"]["bytes"] == 70
    assert by_category["predictive"]["pool_bytes"] == {"kv": 70}
    assert by_category["execution_handoff"]["bytes"] == 120
    assert by_category["execution_handoff"]["pool_bytes"] == {"kv": 20, "mamba": 100}
    assert by_category["unknown_controlled"]["bytes"] == 50
    assert by_category["native"]["pool_units"] == {"kv": 2, "mamba": 1}
    assert by_category["native"]["pool_bytes"] == {"kv": 20, "mamba": 100}
    assert sum(part["bytes"] for part in parts) == row["actual_bytes"]
    assert sum(sum(part["pool_bytes"].values()) for part in parts) == row["actual_bytes"]


def test_source_parts_leave_a_native_batch_native():
    assert h2d_source_parts({
        "actual_bytes": 120, "num_tokens_by_pool": {"kv": 2, "mamba": 1},
    }, {}, {"kv": 10, "mamba": 100}) == [
        {
            "category": "native", "bytes": 120,
            "pool_units": {"kv": 2, "mamba": 1},
            "pool_bytes": {"kv": 20, "mamba": 100},
        },
    ]


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


def test_restore_dependency_wait_summary_never_counts_transfer_ack_as_stall():
    result = restore_wait_statistics([
        {"event": "gpu_restore_dependency_wait", "gpu_layer_dependency_wait_ms": 3.},
        {"event": "gpu_restore_dependency_wait", "gpu_layer_dependency_wait_ms": 7.},
        {"event": "native_hicache_transfer_ack", "submit_to_ack_ms": 1000.},
    ])
    assert result["sampled_batches"] == 2
    assert result["sampled_gpu_wait_sum_ms"] == 10.
    assert result["p50_ms"] == 5.
    assert "oracle" in result["semantics"]


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
