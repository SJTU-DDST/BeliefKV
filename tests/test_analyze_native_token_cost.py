import pytest

from scripts.analyze_native_token_cost import partition_kernels


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
