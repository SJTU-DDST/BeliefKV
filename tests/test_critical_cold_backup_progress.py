"""An authoritative absence of backed alternatives permits bounded cold D2H."""

from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from tests.test_idle_full_prepare_pipeline import fixture


@pytest.mark.parametrize("backed", ({}, {1: object()}, {1: object(), 2: object()}))
def test_known_cold_copy_can_start_before_scan_end_only_without_backed_alternatives(backed):
    runtime, cache, key = fixture()
    cache.tree_core = NS(full_host_duplicates=backed)
    cache.beliefkv_handoff_cold_full_candidates = ((71, 17., 4096),)
    cache.beliefkv_handoff_last_capacity["native_full_scan_truncated"] = True
    rows = []
    runtime._opportunity_writer = NS(record=rows.append, close=lambda: None)
    try:
        with patch.object(
            runtime, "_live_critical_parent_demand", return_value=(key, 5000, 1, 20.),
        ), patch.object(
            runtime, "_wait_prefetch_opportunity",
            return_value=NS(candidate=NS(nodes=(NS(node_id=1), NS(node_id=2)))),
        ), patch.object(
            runtime, "_publish_parent_pressure_candidates",
        ), patch(
            "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
            return_value=NS(
                observable=True, device_full_free_tokens=100, device_mamba_free_slots=10,
            ),
        ):
            assert runtime._reclaim_critical_parent_headroom() is False
        cache.prepare_beliefkv_cold_full_capacity.assert_called_once_with(
            full_tokens=5000, exclude_node_ids=(1, 2),
        )
        assert runtime._cold_full_backups == {71: (17., "critical")}
        assert runtime.counts["critical_no_backed_full_copy_scan_shortcut"] == 1
        assert rows[-1]["before_scan_complete"] is True
        assert not runtime.physical_ledger.pending_count
        assert not runtime.completed_physical_actions
    finally:
        runtime.close()


@pytest.mark.parametrize("blocked", (
    "unknown_index", "wrong_index_type", "foreign_copy", "too_many_copies",
    "unknown_cold_candidate", "transaction_truncated", "target_already_fits",
))
def test_incomplete_scan_still_blocks_copy_without_the_full_live_proof(blocked):
    runtime, cache, key = fixture()
    cache.tree_core = NS(full_host_duplicates={})
    cache.beliefkv_handoff_cold_full_candidates = ((71, 17., 4096),)
    cache.beliefkv_handoff_last_capacity["native_full_scan_truncated"] = True
    if blocked == "unknown_index":
        del cache.tree_core.full_host_duplicates
    elif blocked == "wrong_index_type":
        cache.tree_core.full_host_duplicates = ()
    elif blocked == "foreign_copy":
        cache.tree_core.full_host_duplicates = {77: object()}
    elif blocked == "too_many_copies":
        cache.tree_core.full_host_duplicates = {index: object() for index in range(3)}
    elif blocked == "unknown_cold_candidate":
        cache.beliefkv_handoff_cold_full_candidates = ()
    elif blocked == "transaction_truncated":
        cache.beliefkv_handoff_last_capacity["transaction_truncated"] = True
    else:
        cache.beliefkv_handoff_last_capacity["full_target_satisfied"] = True
    try:
        with patch.object(
            runtime, "_live_critical_parent_demand", return_value=(key, 5000, 1, 20.),
        ), patch.object(
            runtime, "_wait_prefetch_opportunity",
            return_value=NS(candidate=NS(nodes=(NS(node_id=1), NS(node_id=2)))),
        ), patch.object(
            runtime, "_publish_parent_pressure_candidates",
        ), patch(
            "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
            return_value=NS(
                observable=True, device_full_free_tokens=100, device_mamba_free_slots=10,
            ),
        ):
            assert runtime._reclaim_critical_parent_headroom() is False
        cache.prepare_beliefkv_cold_full_capacity.assert_not_called()
        assert not runtime._cold_full_backups
        assert not runtime.counts["critical_no_backed_full_copy_scan_shortcut"]
    finally:
        runtime.close()


def test_nontruncated_scan_keeps_legacy_prepare_without_reading_the_native_index():
    class UnreadTree:
        @property
        def full_host_duplicates(self):
            raise AssertionError("the existing completed scan already provides this decision")

    runtime, cache, key = fixture()
    cache.tree_core = UnreadTree()
    try:
        with patch.object(
            runtime, "_live_critical_parent_demand", return_value=(key, 5000, 1, 20.),
        ), patch.object(
            runtime, "_wait_prefetch_opportunity", return_value=NS(candidate=NS(nodes=())),
        ), patch.object(
            runtime, "_publish_parent_pressure_candidates",
        ), patch(
            "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
            return_value=NS(
                observable=True, device_full_free_tokens=100, device_mamba_free_slots=10,
            ),
        ):
            runtime._reclaim_critical_parent_headroom()
        cache.prepare_beliefkv_cold_full_capacity.assert_called_once()
        assert not runtime.counts["critical_no_backed_full_copy_scan_shortcut"]
    finally:
        runtime.close()
