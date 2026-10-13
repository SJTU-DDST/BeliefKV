from datetime import datetime
import json

import pytest

from scripts.compare_native_policy_runs import (
    acknowledged_prefetch_sources, busy_window_statistics, h2d_source_parts,
    interval_milliseconds, last_runtime_state, percentile,
    paired_workflow_jct, phase_statistics, transfer_parts, restore_wait_statistics,
    cached_native_reference, native_reference_metadata,
)


def test_historical_reference_records_configuration_differences_without_claiming_pair(tmp_path):
    current, reference = tmp_path / "current", tmp_path / "reference"
    current.mkdir()
    reference.mkdir()
    base = {
        "code_commit": "native-version", "sglang_patch_sha256": "engine",
        "root_count": 156, "host_split": "80:20",
    }
    (reference / "ab_plan.json").write_text(json.dumps(base))
    (current / "ab_plan.json").write_text(json.dumps({
        **base, "code_commit": "new-predictive", "host_split": "70:30",
    }))
    metadata = native_reference_metadata(current, reference)
    assert metadata["same_engine_patch"] is True
    assert metadata["formal_paired_evidence"] is False
    assert metadata["configuration_differences"] == {
        "host_split": {"current": "70:30", "reference": "80:20"},
    }
    assert "root_count" in metadata["matched_configuration_fields"]
    assert "workload_sha256" in metadata["unrecorded_configuration_fields"]
    assert "host_numa_memory_policy" in metadata["unrecorded_configuration_fields"]


def test_native_reference_reuses_completed_metrics_only_with_matching_bins(tmp_path):
    cached = {
        "run": str((tmp_path / "native").resolve()),
        "bands": [{"start_seconds": 0.}, {"start_seconds": 600.}],
    }
    assert cached_native_reference(tmp_path, 600) is None
    (tmp_path / "native_policy_comparison.json").write_text(
        json.dumps({"runs": {"native": cached}}),
    )
    assert cached_native_reference(tmp_path, 600) == cached
    assert cached_native_reference(tmp_path, 300) is None


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


def test_paired_jct_retains_failed_and_unmatched_instances_without_biasing_means():
    left = {"workflows": [
        {"instance_id": "b", "outcome": "completed", "duration_seconds": 10.,
         "native_agent_jct_eligible": True, "trace": {"llm_request_count": 5}},
        {"instance_id": "a", "outcome": "completed", "duration_seconds": 8.,
         "native_agent_jct_eligible": False},
        {"instance_id": "failed", "outcome": "incomplete", "duration_seconds": 100.},
        {"instance_id": "left_only", "outcome": "completed", "duration_seconds": 1.},
    ]}
    right = {"workflows": [
        {"instance_id": "a", "outcome": "completed", "duration_seconds": 4.},
        {"instance_id": "b", "outcome": "completed", "duration_seconds": 12.,
         "native_agent_jct_eligible": True, "trace": {"llm_request_count": 7}},
        {"instance_id": "failed", "outcome": "completed", "duration_seconds": 1000.},
        {"instance_id": "right_only", "outcome": "completed", "duration_seconds": 500.},
    ]}
    result = paired_workflow_jct(left, right)
    assert result["shared_instances"] == 3
    assert result["shared_outcome_pairs"] == {"completed/completed": 2, "incomplete/completed": 1}
    assert result["paired_completed_workflows"] == 2
    assert result["paired_completed_with_explicit_jct_eligibility"] == 1
    assert result["unpaired_completed_instances"] == {
        "left": ["left_only"], "right": ["failed", "right_only"],
    }
    assert result["duration_seconds"]["left"]["mean"] == 9.
    assert result["duration_seconds"]["right"]["mean"] == 8.
    assert result["mean_delta_seconds"] == 1.
    assert result["left_shorter_workflows"] == result["right_shorter_workflows"] == 1
    assert result["tied_workflows"] == 0
    assert [row["instance_id"] for row in result["rows"]] == ["a", "b"]
    assert result["rows"][1]["left_llm_request_count"] == 5
    assert result["rows"][1]["right_llm_request_count"] == 7


def test_empty_paired_jct_cohort_remains_unknown_instead_of_zero_latency():
    result = paired_workflow_jct(
        {"workflows": [{"instance_id": "a", "outcome": "incomplete", "duration_seconds": 1.}]},
        {"workflows": [{"instance_id": "a", "outcome": "completed", "duration_seconds": 2.}]},
    )
    assert result["paired_completed_workflows"] == 0
    assert result["duration_seconds"] == {
        "left": {"mean": None, "p50": None, "p95": None},
        "right": {"mean": None, "p50": None, "p95": None},
    }
    assert result["mean_delta_seconds"] is None
    assert result["rows"] == []
    assert result["unpaired_completed_instances"]["right"] == ["a"]


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


def write_busy_window_fixture(arm, *, partial=False):
    client, server = arm / "client_4", arm / "server"
    client.mkdir()
    server.mkdir()
    client.joinpath("gpu_samples.csv").write_text(
        "timestamp,gpu_utilization_percent\n"
        "2026/10/10 00:00:00.000,0\n"
        "2026/10/10 00:00:01.500,60\n"
        "2026/10/10 00:00:02.500,80\n"
        "2026/10/10 00:00:03.500,0\n"
    )
    anchor = datetime(2026, 10, 10).timestamp() * 1000.
    services = [{
        "event": "gpu_service_sample", "phase": phase, "batch_size": size,
        "service_start_ts_ms": anchor + begin, "complete_ts_ms": anchor + end,
        "complete_monotonic_ms": 1000. + end, "request_samples": [{"token_delta": tokens}],
    } for phase, size, begin, end, tokens in (
        ("decode", 2, 500., 1500., 4),
        ("prefill", 4, 1250., 2500., 0),
        ("decode", 6, 2500., 3500., 8),
    )]
    if partial:
        services.pop()
    server.joinpath("runtime_audit.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in services)
        + ('{"event":"gpu_service_sample","complete_ts_ms":' if partial else "")
    )
    server.joinpath("runtime_events.sglang.jsonl").write_text(
        json.dumps({
            "kind": "llm_result", "ts_ms": anchor + 2750.,
            "attributes": {"output_tokens": 100},
        }) + "\n" + json.dumps({
            "kind": "llm_submit", "ts_ms": anchor + 2000.,
            "attributes": {"prompt_tokens": 20, "uncached_prompt_tokens": 4, "cached_tokens_host": 8},
        }) + "\n"
    )
    client.joinpath("sglang_metrics.jsonl").write_text(json.dumps({
        "monotonic_ts_ms": 3000., "num_running_reqs": 4, "num_queue_reqs": 2,
    }) + "\n")


def test_live_busy_window_separates_decode_work_from_request_completion_and_clips_overlap(tmp_path):
    write_busy_window_fixture(tmp_path)
    result = busy_window_statistics(tmp_path, [(1., 3.)])
    window = result["windows"][0]
    assert window["service_window_observed_complete"] is True
    assert window["gpu_utilization_mean"] == 70.
    assert window["running_mean"] == 4.
    assert window["decode_output_tokens"] == 4
    assert window["decode_output_tokens_per_second"] == 2.
    assert window["request_completed_output_tokens_per_second"] == 50.
    assert window["submitted_requests"] == 1
    assert window["submitted_prompt_tokens"] == 20
    assert window["submitted_uncached_prompt_tokens"] == 4
    assert window["submitted_host_hit_tokens"] == 8
    assert window["decode_batch_count"] == 2
    assert window["prefill_batch_count"] == 1
    assert window["decode_worker_interval_seconds"] == 1.
    assert window["prefill_worker_interval_seconds"] == 1.25
    assert window["decode_time_weighted_batch_size"] == 4.
    assert window["worker_interval_union_seconds"] == 2.
    assert "jct_mean_seconds" not in result


def test_live_busy_window_keeps_incomplete_service_horizon_explicit(tmp_path):
    write_busy_window_fixture(tmp_path, partial=True)
    window = busy_window_statistics(tmp_path, [(1., 3.)])["windows"][0]
    assert window["service_window_observed_complete"] is False
    assert window["decode_output_tokens"] == 4
    assert window["decode_worker_interval_seconds"] == .5


def test_live_busy_window_partitions_mixed_transfers_without_duplicating_batch_time(tmp_path):
    write_busy_window_fixture(tmp_path)
    server = tmp_path / "server"
    anchor = datetime(2026, 10, 10).timestamp() * 1000.
    server.joinpath("native_telemetry_status.json").write_text(json.dumps({
        "host_pool_evidence": {
            "full": {"bytes_per_unit": 10}, "mamba": {"bytes_per_unit": 100},
        },
    }))
    server.joinpath("physical_action_ack.jsonl").write_text("".join(
        json.dumps(row) + "\n" for row in [
            {"command_id": "join", "action": "PREFETCH_GPU", "source": "join_ticket"},
            {"command_id": "handoff", "action": "PREFETCH_GPU", "source": "execution_handoff"},
            {"command_id": "prepare", "action": "PREPARE_HOST",
             "num_bytes": 20, "pool_bytes": {"kv": 20}},
        ]
    ))
    server.joinpath("transfer_telemetry.jsonl").write_text("".join(
        json.dumps(row) + "\n" for row in [
            {
                "direction": "h2d", "submit_ts_ms": anchor + 2000.,
                "actual_bytes": 180, "num_tokens_by_pool": {"kv": 8, "mamba": 1},
                "submit_to_ack_ms": 20., "transfer_stream_elapsed_ms": 5.,
                "tagged_child_commits": [
                    {"command_id": "join", "num_bytes": 30, "num_tokens_by_pool": {"kv": 3}},
                    {"command_id": "handoff", "num_bytes": 120,
                     "num_tokens_by_pool": {"kv": 2, "mamba": 1}},
                ],
            },
            {
                "direction": "d2h", "submit_ts_ms": anchor + 1500.,
                "actual_bytes": 50, "num_tokens_by_pool": {"kv": 5},
                "submit_to_ack_ms": 10., "transfer_stream_elapsed_ms": None,
                "tagged_child_commits": [{"command_id": "prepare"}],
            },
            {
                "direction": "h2d", "submit_ts_ms": anchor + 500.,
                "actual_bytes": 1000, "num_tokens_by_pool": {"kv": 100},
                "submit_to_ack_ms": 5., "transfer_stream_elapsed_ms": 1.,
            },
        ]
    ) + '{"direction":"h2d"')
    audit_path = server / "runtime_audit.jsonl"
    rows = [json.loads(line) for line in audit_path.read_text().splitlines()]
    rows.insert(0, {
        "event": "gpu_restore_dependency_wait", "ts_ms": anchor + 500.,
        "gpu_layer_dependency_wait_ms": 100.,
    })
    rows.insert(2, {
        "event": "gpu_restore_dependency_wait", "ts_ms": anchor + 2250.,
        "gpu_layer_dependency_wait_ms": 3.,
    })
    audit_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    window = busy_window_statistics(tmp_path, [(1., 3.)])["windows"][0]
    assert window["transfer_telemetry_present"] is True
    assert window["transfer_batches"]["h2d"] == {
        "count": 1, "bytes": 180, "submit_to_ack_ms": 20.,
        "stream_measured_count": 1, "transfer_stream_ms": 5.,
        "kv_units": 8, "kv_bytes": 80, "mamba_units": 1, "mamba_bytes": 100,
    }
    assert window["h2d_sources"]["predictive"]["bytes"] == 30
    assert window["h2d_sources"]["execution_handoff"]["bytes"] == 120
    assert window["h2d_sources"]["native"]["bytes"] == 30
    assert window["d2h_actions"]["PREPARE_HOST"]["bytes"] == 20
    assert window["d2h_actions"]["native"]["bytes"] == 30
    assert window["gpu_restore_dependency_wait"]["sampled_batches"] == 1
    assert window["gpu_restore_dependency_wait"]["sampled_gpu_wait_sum_ms"] == 3.


def test_receipt_sources_skip_unneeded_opportunity_scan_and_keep_legacy_fallback(tmp_path):
    opportunities = tmp_path / "opportunities"
    opportunities.mkdir()
    path = opportunities / "admission_opportunities.jsonl"
    path.write_text(
        '{"event":"prefetch_native_issued","command_id":"join","source":"join_ticket"}\n'
        '{"event":"prefetch_native_issued","command_id":"legacy","source":"execution_handoff"}\n'
    )
    receipts = [{"command_id": "join", "action": "PREFETCH_GPU", "source": "tool_wait"}]
    assert acknowledged_prefetch_sources(tmp_path, receipts) == {"join": "tool_wait"}
    receipts[0]["source"] = None
    assert acknowledged_prefetch_sources(tmp_path, receipts) == {"join": "join_ticket"}
    receipts[0]["source"] = "tool_wait"
    receipts.append({"command_id": "legacy", "action": "PREFETCH_GPU"})
    assert acknowledged_prefetch_sources(tmp_path, receipts) == {
        "join": "tool_wait", "legacy": "execution_handoff",
    }
