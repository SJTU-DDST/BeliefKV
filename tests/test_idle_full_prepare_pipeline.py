"""Idle FULL preparation and ACK-driven capacity handoff without a GPU."""

from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.runtime.sglang_v0520_physical import (
    PhysicalActionExpectation,
    PhysicalChildExpectation,
)
from tests.test_sglang_v0520_runtime import final_stage_runtime


def fixture():
    runtime = final_stage_runtime()
    runtime.enable_prepare_host = True
    key = runtime.context_sessions["ctx-parent"]
    cache = NS(
        prepare_beliefkv_idle_full=Mock(return_value=((71, 17., 4096),)),
        prepare_beliefkv_cold_full_capacity=Mock(return_value=((71, 17., 4096),)),
        reclaim_beliefkv_handoff_capacity=Mock(return_value={}),
        beliefkv_handoff_last_capacity={"full_target_satisfied": False},
        ongoing_write_through={},
    )
    runtime.attach_native_cache(cache)
    return runtime, cache, key


@pytest.mark.parametrize("enabled,disabled", ((True, False), (False, False), (True, True)))
def test_incremental_full_tracking_is_attached_only_for_enabled_prepare(enabled, disabled):
    runtime, cache, _ = fixture()
    runtime.enable_prepare_host = enabled
    runtime.physical_disabled = disabled
    cache.enable_beliefkv_incremental_prepare = Mock(return_value=True)
    try:
        runtime.attach_native_cache(cache)
        expected = enabled and not disabled
        assert cache.enable_beliefkv_incremental_prepare.call_count == int(expected)
        assert runtime.counts["incremental_full_tracking_enabled"] == int(expected)
    finally:
        runtime.close()

def test_idle_prepare_tracks_issuance_without_any_capacity_or_prefetch_credit():
    runtime, cache, _ = fixture()
    try:
        with patch.object(runtime, "_live_critical_parent_demand", return_value=None):
            runtime.dispatch_idle_prepare()
            runtime.dispatch_idle_prepare()
        cache.prepare_beliefkv_idle_full.assert_called_once_with()
        cache.reclaim_beliefkv_handoff_capacity.assert_not_called()
        assert runtime._cold_full_backups == {71: (17., "idle")}
        assert runtime.counts["idle_cold_full_backup_tokens"] == 4096
        assert runtime.counts["idle_full_prepare_checks"] == 1
        assert runtime.counts["idle_full_prepare_native_calls"] == 1
        assert runtime.counts["idle_full_prepare_issued_batches"] == 1
        assert not runtime.physical_ledger.pending_count
        assert not runtime.completed_physical_actions
    finally:
        runtime.close()


def test_background_checks_wait_200ms_without_accumulating_missed_rounds():
    runtime, cache, _ = fixture()
    try:
        with patch.object(runtime, "_live_critical_parent_demand", return_value=None):
            for now, expected in (
                (1., 1), (1.1, 1), (1.199, 1), (1.2, 2),
                (5., 3), (5., 3), (5.199, 3), (5.2, 4),
            ):
                with patch(
                    "beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
                    return_value=now,
                ):
                    runtime.dispatch_idle_prepare()
                assert cache.prepare_beliefkv_idle_full.call_count == expected
    finally:
        runtime.close()


def test_empty_incremental_index_skips_native_scanning_at_the_shorter_cadence():
    runtime, cache, _ = fixture()
    cache._beliefkv_unbacked_full = {}
    try:
        for now in (1., 1.2, 1.4):
            with patch(
                "beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
                return_value=now,
            ):
                runtime.dispatch_idle_prepare()
        cache.prepare_beliefkv_idle_full.assert_not_called()
        assert runtime.counts["idle_full_prepare_checks"] == 3
        assert runtime.counts["idle_full_prepare_empty_dirty_checks"] == 3
        assert runtime.counts["idle_full_prepare_native_calls"] == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("blocked", (
    "disabled", "physical_disabled", "critical", "prepare_pending",
    "prefetch_pending", "old_engine", "cadence",
))
def test_background_copy_yields_before_entering_the_native_backend(blocked):
    runtime, cache, key = fixture()
    cache._beliefkv_unbacked_full = {71: object()}
    prepare = cache.prepare_beliefkv_idle_full
    if blocked == "disabled":
        runtime.enable_prepare_host = False
    elif blocked == "physical_disabled":
        runtime.physical_disabled = True
    elif blocked == "old_engine":
        del cache.prepare_beliefkv_idle_full
    elif blocked == "cadence":
        runtime._idle_prepare_next_ms = float("inf")
    elif blocked in ("prepare_pending", "prefetch_pending"):
        action = "PREPARE_HOST" if blocked == "prepare_pending" else "PREFETCH_GPU"
        runtime.register_physical_action(PhysicalActionExpectation(
            "pending", action, key.context_id, key.context_epoch,
            (PhysicalChildExpectation(90, (90,), (("kv", 10),), 100),),
            (("kv", 10),), session_id=key.session_id,
            session_generation=key.session_generation,
        ))
    try:
        with patch.object(
            runtime, "_live_critical_parent_demand",
            return_value=(key, 100, 1, 30.) if blocked == "critical" else None,
        ):
            runtime.dispatch_idle_prepare()
        prepare.assert_not_called()
        assert not runtime._cold_full_backups
        if blocked in ("critical", "prepare_pending", "prefetch_pending"):
            assert runtime.counts["idle_full_prepare_checks"] == 1
            assert runtime.counts["idle_full_prepare_dirty_nodes_last"] == 1
            assert runtime.counts["idle_full_prepare_checks_with_dirty_index"] == 1
            assert runtime.counts["idle_full_prepare_native_calls"] == 0
        if blocked in ("prepare_pending", "prefetch_pending"):
            reason = "prepare" if blocked == "prepare_pending" else "prefetch"
            assert runtime.counts[f"idle_full_prepare_blocked_{reason}_transaction"] == 1
    finally:
        runtime.close()


def test_empty_round_and_native_aggregate_are_recorded_without_extra_node_scan():
    runtime, cache, _ = fixture()
    cache.prepare_beliefkv_idle_full.return_value = ()
    cache.beliefkv_idle_full_prepare_stats = {"checks": 1, "scanned_nodes": 32, "stale_nodes": 32}
    rows = []
    runtime._opportunity_writer = NS(record=rows.append, close=lambda: None)
    try:
        with patch.object(runtime, "_live_critical_parent_demand", return_value=None):
            runtime.dispatch_idle_prepare()
        runtime._record_runtime_state()
        state = rows[-1]
        assert state["idle_full_prepare"]["interval_ms"] == 200.
        assert state["counts"]["idle_full_prepare_empty_native_calls"] == 1
        assert state["idle_full_prepare"]["native"] == cache.beliefkv_idle_full_prepare_stats
        assert state["idle_full_prepare"]["native"] is not cache.beliefkv_idle_full_prepare_stats
        assert not runtime._cold_full_backups
        cache.prepare_beliefkv_idle_full.assert_called_once()
    finally:
        runtime.close()


@pytest.mark.parametrize("freed", ({}, {0: 1024}))
def test_critical_reclaim_rearms_prefetch_before_queuing_another_d2h(freed):
    runtime, cache, key = fixture()
    demand = (key, 5000, 1, 20.)
    cache.reclaim_beliefkv_handoff_capacity.return_value = freed
    runtime._report_candidate_next_ms = runtime._tool_prefetch_next_ms = 1000.
    try:
        with patch.object(
            runtime, "_live_critical_parent_demand", return_value=demand,
        ), patch.object(
            runtime, "_wait_prefetch_opportunity",
            return_value=NS(candidate=NS(nodes=(NS(node_id=1), NS(node_id=2)))),
        ), patch.object(
            runtime, "_publish_parent_pressure_candidates",
        ), patch.object(
            runtime, "_invalidate_wait_transfer_observation",
        ) as invalidate, patch(
            "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
            return_value=NS(
                observable=True, device_full_free_tokens=100,
                device_mamba_free_slots=10,
            ),
        ):
            runtime._reclaim_critical_parent_headroom()
        cache.reclaim_beliefkv_handoff_capacity.assert_called_once_with(
            full_tokens=5000, mamba_slots=1, exclude_node_ids=(1, 2),
            collect_diagnostics=True, collect_cold_full_candidates=True,
        )
        cache.prepare_beliefkv_cold_full_capacity.assert_called_once_with(
            full_tokens=5000, exclude_node_ids=(1, 2),
        )
        assert runtime._report_candidate_next_ms == (0. if freed else 1000.)
        assert runtime._tool_prefetch_next_ms == (0. if freed else 1000.)
        assert invalidate.call_count == bool(freed)
        assert runtime._cold_full_backups == {71: (17., "critical")}
        assert not runtime.completed_physical_actions
    finally:
        runtime.close()


def test_legacy_backend_keeps_the_original_reclaim_signature():
    runtime, cache, key = fixture()
    del cache.prepare_beliefkv_cold_full_capacity
    try:
        with patch.object(
            runtime, "_live_critical_parent_demand", return_value=(key, 5000, 1, 20.),
        ), patch.object(
            runtime, "_wait_prefetch_opportunity",
            return_value=NS(candidate=NS(nodes=())),
        ), patch.object(
            runtime, "_publish_parent_pressure_candidates",
        ), patch(
            "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
            return_value=NS(
                observable=True, device_full_free_tokens=100,
                device_mamba_free_slots=10,
            ),
        ):
            runtime._reclaim_critical_parent_headroom()
        cache.reclaim_beliefkv_handoff_capacity.assert_called_once_with(
            full_tokens=5000, mamba_slots=1, exclude_node_ids=(),
            collect_diagnostics=True,
        )
    finally:
        runtime.close()


@pytest.mark.parametrize("reason", (
    "native_full_scan_truncated", "transaction_truncated",
))
def test_urgent_copy_waits_for_the_existing_backed_capacity_scan(reason):
    runtime, cache, key = fixture()
    cache.beliefkv_handoff_last_capacity[reason] = True
    try:
        with patch.object(
            runtime, "_live_critical_parent_demand", return_value=(key, 5000, 1, 20.),
        ), patch.object(
            runtime, "_wait_prefetch_opportunity",
            return_value=NS(candidate=NS(nodes=())),
        ), patch.object(
            runtime, "_publish_parent_pressure_candidates",
        ), patch(
            "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
            return_value=NS(
                observable=True, device_full_free_tokens=100,
                device_mamba_free_slots=10,
            ),
        ):
            runtime._reclaim_critical_parent_headroom()
        cache.prepare_beliefkv_cold_full_capacity.assert_not_called()
    finally:
        runtime.close()


@pytest.mark.parametrize("source", ("idle", "critical"))
@pytest.mark.parametrize("state", (
    "valid", "different_generation", "no_host_copy", "still_pending",
    "ongoing_write", "h2d", "failed", "expired_demand",
))
def test_only_settled_same_identity_d2h_ack_rearms_a_live_parent(source, state):
    runtime, cache, key = fixture()
    node = NS(
        creation_time=18. if state == "different_generation" else 17.,
        component_data={0: NS(host_value=None if state == "no_host_copy" else (1,))},
        write_through_pending_id=71 if state == "still_pending" else None,
    )
    cache.tree_core = NS(node_by_id=lambda _: node)
    if state == "ongoing_write":
        cache.ongoing_write_through[71] = True
    runtime._cold_full_backups[71] = (17., source)
    runtime._critical_reclaim_next_ms = runtime._report_candidate_next_ms = 1000.
    direction = "h2d" if state == "h2d" else "d2h"
    status = "failed" if state == "failed" else "completed"
    try:
        with patch.object(
            runtime, "_live_critical_parent_demand",
            return_value=None if state == "expired_demand" else (key, 5000, 1, 20.),
        ):
            completed = runtime.on_native_transfer_commit(NS(
                direction=direction, status=status, node_ids=(71,),
                num_tokens_by_pool=(("kv", 4096),),
            ))
        expected = state == "valid"
        assert runtime._critical_reclaim_next_ms == (0. if expected else 1000.)
        assert runtime._report_candidate_next_ms == (0. if expected else 1000.)
        assert not completed
        cache.reclaim_beliefkv_handoff_capacity.assert_not_called()
    finally:
        runtime.close()


def test_dispatch_consumes_acked_capacity_before_prefetch_and_background():
    runtime, _, key = fixture()
    runtime._critical_parent_demand = (key, 5000, 1, 20.)
    runtime._join_ticket = object()
    calls = []
    runtime._reclaim_critical_parent_headroom = lambda: calls.append("reclaim")
    runtime.dispatch_join_prefetch = lambda: calls.append("parent_h2d")
    runtime.dispatch_join_prepare = lambda *a, **kw: calls.append("join_prepare")
    runtime.dispatch_tool_prepare = lambda *a, **kw: calls.append("tool_prepare")
    runtime.dispatch_idle_prepare = lambda: calls.append("idle_prepare")
    runtime.enable_tool_prefetch = False
    try:
        runtime.dispatch_after_forward()
        assert calls == [
            "reclaim", "parent_h2d", "join_prepare", "tool_prepare", "idle_prepare",
        ]
    finally:
        runtime._join_ticket = None
        runtime.close()
