from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.core.events import RuntimeEventKind
from beliefkv.runtime.native_transfer_policy import (
    PrefetchResidencyBudget, native_residency_budget, transfer_start_window,
)
from beliefkv.runtime.native_transfer_service import NativeServiceEstimate
from beliefkv.runtime.sglang_v0520_physical import (
    PhysicalActionExpectation, PhysicalChildExpectation,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_native_transfer_policy import capacity
from tests.test_sglang_v0520_runtime import event
from tests.test_wait_prefetch_batch import batch_fixture, semantic_join_batch_fixture
from tests.test_tool_predictive_transfers import leased_runtime


@pytest.mark.parametrize("minimum,expected", [(0., 110.), (500., 500.), (1500., 1000.)])
def test_partial_advance_changes_launch_horizon_without_claiming_transfer_completion(minimum, expected):
    estimate = NativeServiceEstimate(5., 10., 0., 8, "measured")
    window = transfer_start_window(
        estimate, max_lead_ms=1000., observation_spacing_ms=100.,
        minimum_advance_ms=minimum,
    )
    assert window.horizon_ms == expected
    assert window.service_ms == 10.
    assert window.minimum_advance_ms == minimum
    slow = transfer_start_window(
        replace(estimate, submit_to_ack_p90_ms=2000.),
        max_lead_ms=1000., observation_spacing_ms=100., minimum_advance_ms=minimum,
    )
    assert slow.horizon_ms == 1000.
    assert slow.service_ms == 2000.


@pytest.mark.parametrize("minimum", [-1., float("inf"), float("nan")])
def test_invalid_advance_is_rejected(minimum):
    assert transfer_start_window(
        NativeServiceEstimate(5., 10., 0., 8, "measured"),
        max_lead_ms=1000., observation_spacing_ms=100., minimum_advance_ms=minimum,
    ) is None


def add_pending(runtime, key, *, command="already-loading", node_id=90):
    expected = PhysicalActionExpectation(
        command, "PREFETCH_GPU", key.context_id, key.context_epoch,
        (PhysicalChildExpectation(node_id, (node_id,), (("kv", 40),), 40),),
        (("kv", 10),), session_id=key.session_id,
        session_generation=key.session_generation,
    )
    runtime.physical_ledger.register(expected)
    from beliefkv.runtime.sglang_v0520_physical import PrefetchLoadStep
    runtime._prefetch_steps[command] = (
        PrefetchLoadStep(key, node_id, 1, node_id, 1), "tool_wait", 1.,
    )


@pytest.mark.parametrize("parallel,slots,expected_new", [(False, 2, 0), (True, 1, 0), (True, 2, 4)])
def test_unrelated_load_can_overlap_only_with_an_extra_context_and_live_native_capacity(
    parallel, slots, expected_new,
):
    runtime, key, opportunity, cache, _ = batch_fixture("tool_wait")
    other = replace(key, request_id="other", context_id="ctx-other", invocation_id="other",
                    session_id="other-session")
    add_pending(runtime, other)
    cache.supports_beliefkv_overlap_prefetch = lambda: True
    runtime._parallel_wait_prefetch = parallel
    runtime._wait_prefetch_contexts = 2
    runtime._residency_budget = PrefetchResidencyBudget(slots, 10000)
    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity", return_value=opportunity), \
             patch.object(runtime, "_h2d_start_window", return_value=NS(
                 horizon_ms=1000., service_ms=10., enqueue_ms=0., prediction_early_ms=0.,
                 service_ceiling_bytes=None,
             )):
            runtime.dispatch_tool_prefetch()
        assert runtime.physical_ledger.pending_count == 1 + expected_new
        assert cache.prefetch_gpu_session_nodes.call_count == int(expected_new > 0)
        assert runtime._wait_prefetch_transfer_busy(other)
        assert not runtime.physical_disabled
    finally:
        runtime.close()


def test_same_context_is_not_reloaded_before_its_first_transfer_finishes():
    runtime, key, _, cache, _ = batch_fixture("tool_wait")
    cache.supports_beliefkv_overlap_prefetch = lambda: True
    runtime._parallel_wait_prefetch = True
    runtime._wait_prefetch_contexts = 2
    add_pending(runtime, key)
    try:
        assert runtime._wait_prefetch_transfer_busy(key)
        assert runtime._wait_prefetch_transfer_busy(replace(
            key, context_epoch=key.context_epoch + 1,
        ))
        assert not runtime._wait_prefetch_transfer_busy()
        cache.supports_beliefkv_overlap_prefetch = lambda: False
        assert runtime._wait_prefetch_transfer_busy()
    finally:
        runtime.close()

def test_parallel_restore_charges_pending_bytes_only_to_the_fixed_legacy_allowance():
    runtime, key, _, _, _ = batch_fixture("tool_wait")
    runtime._parallel_wait_prefetch = True
    runtime._wait_prefetch_contexts = 2
    add_pending(runtime, key)
    runtime._prefetch_steps.clear()
    try:
        assert runtime._prefetch_byte_allowance(PrefetchResidencyBudget(2, 100)) == 60
        assert runtime._prefetch_byte_allowance(PrefetchResidencyBudget(
            2, 100, source="native_next_prefill",
        )) == 100
        assert runtime._prefetch_byte_allowance(PrefetchResidencyBudget(2, 30)) == 0
        other = replace(key, context_id="other", session_id="other-session")
        assert not runtime._prefetch_slot_available(
            other, budget=PrefetchResidencyBudget(1, 100),
        )
    finally:
        runtime.close()

def test_large_native_budget_cannot_exceed_the_independent_context_limit():
    runtime, key, _, cache, _ = batch_fixture("tool_wait")
    runtime._parallel_wait_prefetch = True
    runtime._wait_prefetch_contexts = 2
    cache.supports_beliefkv_overlap_prefetch = lambda: True
    other = replace(key, context_id="other", session_id="other-session")
    third = replace(key, context_id="third", session_id="third-session")
    add_pending(runtime, key)
    add_pending(runtime, other, command="other-loading", node_id=91)
    try:
        assert runtime._wait_prefetch_transfer_busy()
        assert runtime._wait_prefetch_transfer_busy(third)
        assert not runtime._prefetch_slot_available(
            third, budget=PrefetchResidencyBudget(48, 1000000),
        )
    finally:
        runtime.close()


def test_two_full_batch_restore_frontiers_reserve_growth_without_allocating_request_rows():
    budget = native_residency_budget(**capacity(
        running_requests=48, available_request_rows=0, restore_frontier_slots=2,
    ))
    assert budget.request_slots == 2
    assert budget.reserve_full_tokens == 8192 + 16 * 50
    assert budget.reserve_mamba_slots == 2
    assert native_residency_budget(**capacity(
        running_requests=48, available_request_rows=0, restore_frontier_slots=2,
        prefill_slots=1,
    )).request_slots == 1


def test_join_minimum_advance_does_not_authorize_work_forecast_without_eos():
    runtime, _, opportunity, cache, _ = semantic_join_batch_fixture(
        center_tokens=32., lower_tokens=32., lead_ms=1000.,
    )
    runtime.join_prefetch_min_advance_ms = 1000.
    forecast = runtime._semantic_forecasts["child"]
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
                   return_value=forecast.observation.observed_ts_ms / 1000.), \
             patch.object(runtime, "inspect_context_h2d_opportunity", return_value=opportunity):
            runtime.dispatch_join_prefetch()
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime.physical_ledger.pending_count == 0
        assert not runtime.graph.joins["join"].satisfied
    finally:
        runtime.close()


def test_bookkeeping_sweep_coalesces_without_extending_action_authorization():
    runtime, key, _, _, _ = batch_fixture("tool_wait")
    hint = runtime.tool_wait_hints[key.context_id]
    runtime._prediction_maintenance_interval_ms = 100.
    begin = hint.issued_monotonic_ms
    runtime.tool_wait_hints[key.context_id] = replace(hint, expires_monotonic_ms=begin + 20.)
    try:
        runtime._maintain_prediction_state(begin)
        runtime._maintain_prediction_state(begin + 30.)
        assert key.context_id in runtime.tool_wait_hints
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
                   return_value=(begin + 30.) / 1000.):
            assert runtime._live_tool_hint(key) is None
        runtime._maintain_prediction_state(begin + 100.)
        assert key.context_id not in runtime.tool_wait_hints
        assert "prediction_maintenance_deferred" not in runtime.counts
        assert runtime.counts["prediction_maintenance_sweeps"] == 2
    finally:
        runtime.close()


def test_graph_change_wakes_prediction_maintenance_before_its_timer():
    runtime = NativeAdmissionRuntime()
    runtime._prediction_maintenance_interval_ms = 100.
    runtime.tool_wait_hints["live"] = NS(key=None, live=lambda key, now_ms: True)
    try:
        runtime._maintain_prediction_state(1000.)
        runtime.on_events((event(1, RuntimeEventKind.WORKFLOW_START),))
        runtime._maintain_prediction_state(1001.)
        assert runtime.counts["prediction_maintenance_sweeps"] == 2
    finally:
        runtime.close()

def test_lease_scan_coalesces_but_never_passes_the_earliest_expiry():
    runtime, _, _, action = leased_runtime()
    runtime._prediction_maintenance_interval_ms = 100.
    lease = runtime._prefetch_service_leases[action.command_id]
    begin = lease.expires_at - .05
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
                   return_value=begin), \
             patch.object(runtime, "_prefetch_lease_invalid_reason", wraps=(
                 runtime._prefetch_lease_invalid_reason
             )) as validate:
            runtime._refresh_prefetch_service_leases()
            runtime._refresh_prefetch_service_leases()
            assert validate.call_count == 1
        assert "prefetch_lease_maintenance_deferred" not in runtime.counts
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
                   return_value=lease.expires_at):
            runtime._refresh_prefetch_service_leases()
        assert not runtime._prefetch_service_leases
        assert runtime.counts["prefetch_residency_released:service_window_expired"] == 1
    finally:
        runtime.close()

def test_graph_change_invalidates_a_coalesced_lease_scan():
    runtime, hint, _, action = leased_runtime()
    runtime._prediction_maintenance_interval_ms = 100.
    try:
        runtime._refresh_prefetch_service_leases()
        runtime.on_events((event(
            4, RuntimeEventKind.RETURN, invocation_id=hint.key.invocation_id,
        ),))
        runtime._refresh_prefetch_service_leases()
        assert action.command_id not in runtime._prefetch_service_leases
    finally:
        runtime.close()


@pytest.mark.parametrize("setting,value", [
    ("BELIEFKV_JOIN_PREFETCH_MIN_ADVANCE_MS", "-1"),
    ("BELIEFKV_TOOL_PREFETCH_MIN_ADVANCE_MS", "nan"),
    ("BELIEFKV_PREDICTION_MAINTENANCE_INTERVAL_MS", "inf"),
    ("BELIEFKV_PARALLEL_WAIT_PREFETCH", "2"),
    ("BELIEFKV_WAIT_PREFETCH_CONTEXTS", "3"),
])
def test_invalid_runtime_settings_fail_before_starting_workers(monkeypatch, setting, value):
    monkeypatch.setenv(setting, value)
    with pytest.raises(ValueError):
        NativeAdmissionRuntime()
