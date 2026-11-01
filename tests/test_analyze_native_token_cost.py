import json

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts.analyze_native_token_cost import analyze_prefill, compare, partition_kernels


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


def test_decode_subset_needs_no_unmeasured_control_widths(tmp_path):
    def put(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    inputs = tmp_path / "inputs.json"
    put(inputs, {"cases": [{"width": 6, "context_lengths": [4096] * 6}]})
    plan = {
        "inputs": str(inputs), "inputs_sha256": sha256_file(inputs),
        "native": {"state": "same-engine"},
    }
    reference, candidate = tmp_path / "reference", tmp_path / "candidate"
    for root, mode, cost in ((reference, "sparse", 2.), (candidate, "exact", 1.)):
        put(root / "plan.json", plan)
        put(root / mode / "summary.json", {
            "decode_by_case": {
                f"6:measure{index}": {"worker_ms_per_output": cost} for index in range(3)
            },
            "profiles": [],
        })
        for index in range(3):
            put(root / mode / f"width6_measure{index}.json", {
                "records": {f"r{index}": {"tokens": [1, 2], "logprobs": [-.1, -.2]}},
            })
    accounting = tmp_path / "accounting.json"
    put(accounting, {
        "scopes": {"common_window": {"worker_ms": 100.}},
        "common_window_decode_by_width": {"6": {"worker_ms": 50.}},
    })
    result = compare(
        reference, accounting, candidate_dir=candidate / "exact", candidate_label="exact_moe",
    )
    assert result["numerical"]["all_output_sequences_equal"]
    assert result["by_width"]["6"]["candidate_worker_cost_reduction_percent"] == 50.
    assert result["sample_reweighting"]["estimated_total_worker_reduction_percent"] == 25.
    assert "control_width8_cost_change_percent" not in result
