from dataclasses import asdict, replace
import pickle
from unittest.mock import Mock, patch

import pytest

from beliefkv.runtime.sglang_v0520_physical import (
    PhysicalActionExpectation, PhysicalChildExpectation, PhysicalReceiptError,
    PrefetchLoadStep, plan_prefetch_gpu_steps, prefetch_expectation_from_native_op,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_wait_prefetch_dispatch_cpu import dispatch_fixture
from tests.test_sglang_v0520_physical import native_prefetch


@pytest.mark.parametrize("source", ("join_ticket", "tool_wait"))
@pytest.mark.parametrize("depth", (63, 64, 65, 96))
def test_complete_full_submission_skips_tail_work_only_when_no_full_remains(source, depth):
    runtime, _, cache, _, actions, _, _, dispatch, now = dispatch_fixture(
        NativeAdmissionRuntime, source=source, depth=depth,
        native_budget=True, tail_reclaim=True,
    )
    cache.reclaim_beliefkv_handoff_capacity = Mock(return_value={})
    try:
        with patch("time.monotonic", lambda: now), patch(
            "beliefkv.runtime.sglang_v0520_runtime.plan_prefetch_gpu_steps",
            wraps=plan_prefetch_gpu_steps,
        ) as tail_plan:
            dispatch()
        assert len(actions) == min(depth, 64)
        assert runtime.physical_ledger.pending_count == min(depth, 64)
        assert not runtime.completed_physical_actions
        assert not runtime.physical_disabled
        if depth <= 64:
            tail_plan.assert_not_called()
            cache.reclaim_beliefkv_handoff_capacity.assert_not_called()
        else:
            tail_plan.assert_called_once()
            assert cache.reclaim_beliefkv_handoff_capacity.call_count == 1
            kwargs = cache.reclaim_beliefkv_handoff_capacity.call_args.kwargs
            reserve = runtime._residency_budget.reserve_full_tokens
            assert kwargs["full_tokens"] == (depth - 64) * 128 + reserve
            assert set(kwargs["exclude_node_ids"]) == set(range(depth + 1))
    finally:
        runtime.close()


@pytest.mark.parametrize("source", ("join_ticket", "tool_wait"))
def test_missing_full_outside_the_legal_input_plan_does_not_prove_all_full_submitted(source):
    runtime, key, cache, _, actions, _, _, dispatch, now = dispatch_fixture(
        NativeAdmissionRuntime, source=source, depth=64,
        native_budget=True, tail_reclaim=True,
    )
    runtime._context_tokens[key.context_id] = (0, 63 * 128 + 1, 0, False)
    cache.tree_core.node_by_id(63).component_data[2].host_value = (0,)
    original = runtime._wait_prefetch_opportunity
    cache.reclaim_beliefkv_handoff_capacity = Mock(return_value={})

    def without_cached_plan(*args, **kwargs):
        opportunity = original(*args, **kwargs)
        assert opportunity.required_full_tokens == 63 * 128
        assert opportunity.candidate.missing_full_device_tokens == 64 * 128
        return replace(opportunity, checkpoint_plan=None)

    runtime._wait_prefetch_opportunity = without_cached_plan
    try:
        with patch("time.monotonic", lambda: now), patch(
            "beliefkv.runtime.sglang_v0520_runtime.plan_prefetch_gpu_steps",
            wraps=plan_prefetch_gpu_steps,
        ) as tail_plan:
            dispatch()
        assert len(actions) == 63
        tail_plan.assert_called_once()
        cache.reclaim_beliefkv_handoff_capacity.assert_not_called()
    finally:
        runtime.close()


@pytest.mark.parametrize("field", ("leaf_node_id", "node_id"))
@pytest.mark.parametrize("invalid", (True, -1, 1.0, None))
def test_prefetch_scalar_identity_validation_keeps_strict_node_ids(field, invalid):
    step, operation, controller = native_prefetch(mamba=0, sidecar=None)
    step = replace(step, **{field: invalid})
    if field == "node_id":
        operation.node_ids = [invalid]
    with pytest.raises(PhysicalReceiptError):
        prefetch_expectation_from_native_op("prefetch-1", step, operation, controller)


@pytest.mark.parametrize("field", ("leaf_creation_time", "creation_time"))
@pytest.mark.parametrize("invalid", (True, -1, float("nan"), float("inf"), None))
def test_prefetch_scalar_identity_validation_keeps_finite_creation_times(field, invalid):
    step, operation, controller = native_prefetch(mamba=0, sidecar=None)
    step = replace(step, **{field: invalid})
    with pytest.raises(PhysicalReceiptError):
        prefetch_expectation_from_native_op("prefetch-1", step, operation, controller)


@pytest.mark.parametrize("counts", (
    {}, {"kv": True}, {"kv": -1}, {"kv": 2.0},
    {"kv": 2, "mamba": 0}, {"kv": 2, "swa": 0}, {"mamba": 2},
))
def test_single_pool_validation_rejects_malformed_or_extra_native_counts(counts):
    step, operation, controller = native_prefetch(mamba=0, sidecar=None)
    controller._num_tokens_by_pool = lambda operation: counts
    with pytest.raises(PhysicalReceiptError):
        prefetch_expectation_from_native_op("prefetch-1", step, operation, controller)


def test_compact_prefetch_records_keep_dataclass_copy_serialization_and_metadata():
    step, operation, controller = native_prefetch(mamba=0, sidecar=None)
    action = prefetch_expectation_from_native_op("prefetch-1", step, operation, controller)
    child = replace(
        action.children[0], anchor_creation_time=4,
        host_destination_indices=(("kv", (1, 2)),),
    )
    action = replace(action, children=(child,))
    assert isinstance(step, PrefetchLoadStep)
    assert isinstance(child, PhysicalChildExpectation)
    assert isinstance(action, PhysicalActionExpectation)
    for record in (step, child, action):
        restored = pickle.loads(pickle.dumps(record))
        assert asdict(restored) == asdict(record)
        assert asdict(replace(record)) == asdict(record)
    assert action.children[0].host_destination_indices == (("kv", (1, 2)),)
