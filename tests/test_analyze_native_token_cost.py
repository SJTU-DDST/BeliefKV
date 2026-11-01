import json

import pytest

from scripts.analyze_native_token_cost import analyze_prefill, partition_kernels


def event(name, start, duration):
    return {"cat": "kernel", "ph": "X", "name": name, "ts": start, "dur": duration}


def test_dependent_activation_overlap_is_not_additive_execution():
    result = partition_kernels([
        event("fused_moe_kernel", 0, 100),
        event("sglang::act_and_mul_kernel", 60, 42),
        event("flash::FlashAttn", 110, 200),
    ])
    assert result["kernel_duration_sum_ms"] == .342
    assert result["kernel_interval_union_ms"] == .302
    assert result["by_group"]["activation"]["exclusive_interval_ms"] == .002
    assert result["simultaneously_active_kernel_groups_ms"]["activation+moe_gemm"] == .04


def test_same_group_overlap_counts_union_once():
    result = partition_kernels([
        event("fused_moe_kernel", 0, 10),
        event("fused_moe_kernel", 5, 10),
    ])
    assert result["kernel_duration_sum_ms"] == .02
    assert result["kernel_interval_union_ms"] == .015


def test_empty_trace_is_rejected():
    with pytest.raises(ValueError, match="empty"):
        partition_kernels([])


@pytest.mark.parametrize("defect", [None, "profile_work", "writer", "cleanup"])
def test_prefill_analysis_requires_all_work_and_complete_collection(tmp_path, defect):
    directory = tmp_path / "sparse"
    directory.mkdir()

    def put(path, value):
        path.write_text(json.dumps(value))

    put(tmp_path / "plan.json", {"cold_prefill_only": True, "modes": ["sparse"]})
    put(directory / "prefill_case.json", {"executed_input_tokens_per_request": 2})
    put(directory / "receipts.json", {str(i): {"tokens": [7], "cached_tokens": 0} for i in range(4)})
    trace = directory / "trace.json"
    put(trace, {"traceEvents": [event("fused_moe_kernel", 0, 100)]})
    summary = {
        "all_output_ordinals_match_native_responses": True,
        "prefill_by_case": {
            f"1:{label}": {
                "executed_input_tokens": 2, "output_tokens": 1, "worker_ms_per_input_token": 1.,
            }
            for label in ("prefill_measure0", "prefill_measure1", "prefill_measure2", "prefill_profile")
        },
        "profiles": [{"trace": str(trace)}],
    }
    if defect == "profile_work":
        summary["prefill_by_case"]["1:prefill_profile"]["executed_input_tokens"] = 3
    put(directory / "summary.json", summary)
    put(directory / "telemetry_drain.json", {
        "previous_zero_snapshot_ts_ms": 9,
        "drained_status": {
            "snapshot_ts_ms": 10, "pending_batch_count": 0, "pending_request_count": 0,
            "writer_queue_depth": 0, "failed_records": 0,
            "dropped_records": 1 if defect == "writer" else 0, "writer_error": None,
        },
    })
    put(directory / "owned_process_cleanup.json", {
        "remaining_processes": [123] if defect == "cleanup" else [],
        "reason": "component_complete",
    })
    if defect:
        with pytest.raises(ValueError, match="work or completion|drain or cleanup"):
            analyze_prefill(tmp_path)
    else:
        result = analyze_prefill(tmp_path)
        assert result["median_unprofiled_worker_ms_per_input_token"] == 1.
        assert result["all_four_single_token_outputs_equal"]
