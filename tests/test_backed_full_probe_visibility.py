"""The native backed index can expose donors omitted from publication."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget, TransferStartWindow
from tests.test_wait_prefetch_batch import batch_fixture


@pytest.mark.parametrize("source", ("join_ticket", "tool_wait"))
@pytest.mark.parametrize("index", (None, (), {}, {1: object()}, {99: object()}))
def test_native_index_is_a_probe_hint_without_owner_scanning_or_issuance(source, index):
    runtime, key, opportunity, cache, _ = batch_fixture(source)
    cache.tree_core = NS(
        full_host_duplicates=index,
        node_by_id=Mock(side_effect=AssertionError("no index-node inspection")),
    )
    cache.reclaim_beliefkv_handoff_capacity = Mock()
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            return_value=replace(
                opportunity, headroom=replace(
                    opportunity.headroom, device_full_free_tokens=1,
                ),
            ),
        ), patch.object(
            runtime, "_current_residency_budget",
            return_value=PrefetchResidencyBudget(1, 1024, reserve_full_tokens=1),
        ), patch.object(
            runtime, "_live_parent_pressure_node",
            side_effect=AssertionError("there are no published donors"),
        ):
            result = runtime._wait_prefetch_opportunity(key)
        actionable = type(index) is dict and 99 in index
        assert result.capacity_reclaim_probe is actionable
        assert (result.step is not None) is actionable
        assert result.fits_current_free_lists is (False if actionable else None)
        cache.tree_core.node_by_id.assert_not_called()
        cache.reclaim_beliefkv_handoff_capacity.assert_not_called()
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("condition", ("fits", "free_only", "retry_deadline", "no_reclaim_api"))
def test_unneeded_or_throttled_plans_do_not_read_the_native_index(condition):
    class UnreadTree:
        @property
        def full_host_duplicates(self):
            raise AssertionError("no native index read on this path")

    runtime, key, opportunity, cache, _ = batch_fixture("tool_wait")
    cache.tree_core = UnreadTree()
    if condition != "no_reclaim_api":
        cache.reclaim_beliefkv_handoff_capacity = Mock()
    if condition == "retry_deadline":
        runtime._wait_capacity_probe_next_ms = float("inf")
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            return_value=replace(
                opportunity, headroom=replace(
                    opportunity.headroom,
                    device_full_free_tokens=100 if condition == "fits" else 1,
                ),
            ),
        ), patch.object(
            runtime, "_current_residency_budget",
            return_value=PrefetchResidencyBudget(1, 1024, reserve_full_tokens=1),
        ):
            result = runtime._wait_prefetch_opportunity(
                key, fit_current_capacity=condition == "free_only",
            )
        assert not result.capacity_reclaim_probe
        assert runtime.physical_ledger.pending_count == 0
        cache.prefetch_gpu_session_nodes.assert_not_called()
    finally:
        runtime.close()


@pytest.mark.parametrize("source", ("join_ticket", "tool_wait"))
@pytest.mark.parametrize("blocked", ("before_window", "nothing_freed", "reserve_grew"))
def test_backed_hint_does_not_bypass_timing_actual_reclaim_or_growth_budget(source, blocked):
    runtime, _, opportunity, cache, _ = batch_fixture(source)
    headroom = [replace(opportunity.headroom, device_full_free_tokens=1)]
    reserve = [1]
    cache.tree_core = NS(full_host_duplicates={99: object()})
    runtime._h2d_samples.extend(((40, 10.),) * 3)

    def reclaim(**_):
        if blocked == "reserve_grew":
            headroom[0] = replace(headroom[0], device_full_free_tokens=5)
            reserve[0] = 5
            return {0: 4}
        return {}

    cache.reclaim_beliefkv_handoff_capacity = Mock(side_effect=reclaim)
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **_: replace(opportunity, headroom=headroom[0]),
        ), patch.object(
            runtime, "_current_residency_budget",
            side_effect=lambda **_: PrefetchResidencyBudget(
                1, 1024, reserve_full_tokens=reserve[0],
            ),
        ), patch.object(
            runtime, "_publish_parent_pressure_candidates",
        ), patch.object(
            runtime, "_h2d_start_window",
            return_value=TransferStartWindow(
                10., 0., 50. if blocked == "before_window" else 500.,
            ),
        ):
            getattr(runtime, f"dispatch_{'join' if source == 'join_ticket' else 'tool'}_prefetch")()
        # Confirmed JOIN has an observed completion, so no optional ETA gate.
        expected_reclaims = int(blocked != "before_window" or source == "join_ticket")
        assert cache.reclaim_beliefkv_handoff_capacity.call_count == expected_reclaims
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime.physical_ledger.pending_count == 0
        assert not runtime.physical_disabled
    finally:
        runtime.close()
