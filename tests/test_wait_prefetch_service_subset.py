from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.runtime.native_transfer_service import NativeServiceSample
from beliefkv.runtime.sglang_v0520_runtime import _WaitPrefetchServiceShape
from tests.test_wait_prefetch_batch import (
    batch_fixture, provisional_join_batch_fixture,
)


def shrink(opportunity):
    step = replace(opportunity.steps[0], include_mamba=False)
    return replace(
        opportunity, step=step, steps=(step,),
        required_full_tokens=4, required_mamba_slots=0,
        fits_current_free_lists=True,
    )


def supported_parent(runtime):
    runtime._h2d_samples.clear()
    runtime._native_service_samples.clear()
    runtime._native_service_samples.extend(
        NativeServiceSample(180, delay, shape="hybrid", enqueue_to_submit_ms=5.)
        for delay in (50., 60., 70.)
    )


def test_smaller_same_checkpoint_keeps_supported_cost_without_scaling_fixed_delay():
    runtime, _, opportunity, _, _ = batch_fixture("tool_wait")
    supported_parent(runtime)
    small = shrink(opportunity)
    selected = _WaitPrefetchServiceShape.from_opportunity(opportunity)
    try:
        assert runtime._h2d_start_window(small) is None
        original = runtime._h2d_start_window(opportunity)
        actual = runtime._h2d_start_window(small, service_shape=selected)
        assert actual.service_ms == original.service_ms == 68.
        assert actual.enqueue_ms == original.enqueue_ms == 5.
        assert actual.service_ceiling_bytes == 180
        assert actual.service_ceiling_shape == "hybrid"
        assert actual.horizon_ms <= runtime.prefetch_lead_ms
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("change", (
    "workflow", "epoch", "session", "generation", "leaf", "creation",
    "larger_full", "larger_mamba", "empty",
))
def test_selected_cost_cannot_authorize_a_different_checkpoint_or_larger_payload(change):
    runtime, key, opportunity, _, _ = batch_fixture("tool_wait")
    supported_parent(runtime)
    selected = _WaitPrefetchServiceShape.from_opportunity(opportunity)
    small = shrink(opportunity)
    step = small.step
    if change in ("workflow", "epoch", "session", "generation"):
        field, value = {
            "workflow": ("root_workflow_id", "other"),
            "epoch": ("context_epoch", key.context_epoch + 1),
            "session": ("session_id", "other"),
            "generation": ("session_generation", key.session_generation + 1),
        }[change]
        step = replace(step, key=replace(key, **{field: value}))
    elif change == "leaf":
        step = replace(step, leaf_node_id=99)
    elif change == "creation":
        step = replace(step, leaf_creation_time=99.)
    elif change == "larger_full":
        small = replace(small, required_full_tokens=17)
    elif change == "larger_mamba":
        small = replace(small, required_mamba_slots=2)
    else:
        step = None
    small = replace(small, step=step)
    try:
        assert not selected.contains(small)
        # Independently supported payloads remain eligible via the normal path.
        if runtime._h2d_start_window(small) is None:
            assert runtime._h2d_start_window(small, service_shape=selected) is None
    finally:
        runtime.close()


def test_subset_preserves_direct_estimate_and_requires_current_superset_support():
    runtime, _, opportunity, _, _ = batch_fixture("tool_wait")
    supported_parent(runtime)
    selected = _WaitPrefetchServiceShape.from_opportunity(opportunity)
    small = shrink(opportunity)
    try:
        runtime._native_service_samples.extend(
            NativeServiceSample(40, delay, shape="full") for delay in (2., 3., 4.)
        )
        expected = runtime._h2d_start_window(small)
        assert runtime._h2d_start_window(small, service_shape=selected) == expected
        assert expected.service_ceiling_bytes is None
        assert runtime.counts["wait_prefetch_subset_service_queries"] == 0
        runtime._native_service_samples.clear()
        runtime._h2d_service_estimates.clear()
        assert runtime._h2d_start_window(small, service_shape=selected) is None
    finally:
        runtime.close()


@pytest.mark.parametrize("source", ("join_ticket", "tool_wait"))
@pytest.mark.parametrize("invalidated", (False, True))
def test_capacity_shrink_reaches_enqueue_only_with_live_timing_and_causal_identity(
    source, invalidated,
):
    fixture = (
        provisional_join_batch_fixture() if source == "join_ticket"
        else batch_fixture(source)
    )
    runtime, _, opportunity, cache, _ = fixture
    supported_parent(runtime)
    original = replace(opportunity, fits_current_free_lists=False)
    small = shrink(opportunity)
    inspections = []
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    if runtime._semantic_worker is not None:
        runtime._semantic_worker.error = ""

    def inspect(key, **kwargs):
        inspections.append(kwargs)
        return small if len(inspections) > 1 else original

    def reclaim(key, observed, *, source):
        return small

    def drain(**kwargs):
        if invalidated:
            runtime.context_sessions.clear()

    runtime.event_server = NS(drain=drain, close=lambda: None)
    try:
        with patch.object(runtime, "_wait_prefetch_opportunity", side_effect=inspect), \
            patch.object(runtime, "_reclaim_wait_prefetch_capacity", side_effect=reclaim), \
            patch.object(runtime, "_reclaim_wait_prefetch_tail_capacity"):
            if source == "join_ticket":
                runtime.dispatch_join_prefetch()
            else:
                runtime.dispatch_tool_prefetch()
        assert runtime.physical_ledger.pending_count == int(not invalidated)
        assert cache.prefetch_gpu_session_nodes.call_count == int(not invalidated)
        assert not runtime.physical_disabled
        if not invalidated:
            submitted = next(
                row for row in records
                if row["event"] == "wait_prefetch_plan" and row["reason"] == "submitted"
            )
            assert submitted["planned_pool_bytes"] == {"kv": 40, "mamba": 0}
    finally:
        runtime.close()
