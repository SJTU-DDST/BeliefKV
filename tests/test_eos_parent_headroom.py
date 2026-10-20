"""Native EOS can prepare safe restore capacity without a report forecast."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.core.events import RuntimeEventKind
from tests.test_sglang_v0520_runtime import event, req
from tests.test_wait_prefetch_batch import provisional_join_batch_fixture


@pytest.mark.parametrize("capacity_blocked", (False, True))
def test_eos_prepares_checkpoint_capacity_even_without_supported_dma_timing(capacity_blocked):
    runtime, key, opportunity, cache, _ = provisional_join_batch_fixture()
    runtime._borrowable_parent_headroom = True
    ended_ms = runtime._semantic_finished["child"][0]
    if capacity_blocked:
        opportunity = replace(
            opportunity, step=None, steps=(),
            required_full_tokens=0, required_mamba_slots=0,
            fits_current_free_lists=False,
            no_step_reason="prefetch_full_capacity_budget",
        )
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
            return_value=ended_ms / 1000.,
        ), patch.object(
            runtime, "_wait_prefetch_opportunity", return_value=opportunity,
        ) as inspect, patch.object(
            runtime, "_current_residency_budget",
            return_value=NS(reserve_full_tokens=4104, reserve_mamba_slots=2),
        ), patch.object(
            runtime, "_estimate_h2d_service", return_value=None,
        ), patch.object(
            runtime, "_report_remaining_ms",
            side_effect=AssertionError("EOS must not require a report forecast"),
        ):
            assert runtime._roll_final_stage() is None
            inspect.assert_called_once_with(key, include_checkpoint_plan=True)
            parent, full, mamba, expires = runtime._live_critical_parent_demand()
            assert parent == key
            assert full == 16 + 4104 and mamba == 1 + 2
            assert expires <= (ended_ms + runtime.eos_prefetch_evidence_window_ms) / 1000.
        assert runtime.counts["observed_eos_capacity_preparation"] == 1
        assert runtime.counts["observed_eos_capacity_blocked"] == int(capacity_blocked)
        assert runtime.counts["observed_eos_no_host_target"] == 0
        assert runtime._join_ticket is None
        assert not runtime.physical_ledger.pending_count
        cache.prefetch_gpu_session_nodes.assert_not_called()
    finally:
        runtime.close()


@pytest.mark.parametrize("excluded", (
    "feature_disabled", "expired_eos", "invalid_eos", "join_satisfied",
    "no_restore_target",
))
def test_eos_capacity_requires_live_eos_parent_checkpoint_and_feature(excluded):
    runtime, key, opportunity, cache, _ = provisional_join_batch_fixture()
    runtime._borrowable_parent_headroom = excluded != "feature_disabled"
    ended_ms = runtime._semantic_finished["child"][0]
    if excluded == "expired_eos":
        runtime._semantic_finished["child"] = (
            ended_ms - runtime.eos_prefetch_evidence_window_ms - 1., 3,
        )
    elif excluded == "invalid_eos":
        runtime._semantic_eos_proofs["child"] = False
    elif excluded == "join_satisfied":
        runtime.on_events((event(8, RuntimeEventKind.RETURN, invocation_id="child"),))
    if excluded != "feature_disabled":
        opportunity = replace(
            opportunity, step=None, steps=(),
            required_full_tokens=0, required_mamba_slots=0, candidate=None,
        )
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
            return_value=ended_ms / 1000.,
        ), patch.object(
            runtime, "_wait_prefetch_opportunity", return_value=opportunity,
        ), patch.object(
            runtime, "_current_residency_budget",
            side_effect=AssertionError("no valid restore demand"),
        ), patch.object(
            runtime, "_estimate_h2d_service", return_value=None,
        ):
            assert runtime._roll_final_stage() is None
        assert runtime._critical_parent_demand is None
        assert not runtime.physical_ledger.pending_count
        cache.prefetch_gpu_session_nodes.assert_not_called()
    finally:
        runtime.close()


def test_eos_capacity_activates_a_just_started_unhinted_tool_donor():
    runtime, key, opportunity, _, _ = provisional_join_batch_fixture()
    runtime._borrowable_parent_headroom = True
    donor = req("other")
    donor.session_id, donor.session_generation = "donor", 1
    runtime.register_visible_request(donor)
    donor_key = runtime.context_sessions["ctx-other"]
    ended_ms = runtime._semantic_finished["child"][0]
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
            return_value=ended_ms / 1000.,
        ), patch.object(
            runtime, "_wait_prefetch_opportunity", return_value=opportunity,
        ), patch.object(
            runtime, "_current_residency_budget",
            return_value=NS(reserve_full_tokens=4104, reserve_mamba_slots=2),
        ), patch.object(runtime, "_estimate_h2d_service", return_value=None):
            runtime._roll_final_stage()
            runtime.on_events((event(
                8, RuntimeEventKind.TOOL_START, invocation_id="other",
                attributes={"tool_run_id": "fresh"},
            ),))
            assert runtime._critical_tool_donor(donor_key)
            assert runtime._long_tool_wait(donor_key)
            assert runtime._tool_prepare_next_ms == 0.
            assert runtime._critical_parent_demand[0] == key
        assert not runtime.tool_wait_hints
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()
