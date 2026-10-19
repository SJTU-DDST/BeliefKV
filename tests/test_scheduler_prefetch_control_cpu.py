from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_observer import StaticPoolHeadroomObservation
from beliefkv.runtime.sglang_v0520_physical import (
    PhysicalReceiptError, PhysicalTransactionLedger,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_sglang_v0520_physical import child, expected


@pytest.mark.parametrize("method", ("dispatch_join_prepare", "dispatch_tool_prepare"))
def test_empty_prepare_sources_skip_capacity_even_with_gpu_service_demand(method):
    runtime = NativeAdmissionRuntime()
    runtime.enable_prepare_host = True
    runtime.attach_native_cache(NS())
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
            side_effect=AssertionError("no source needs a capacity census"),
        ):
            getattr(runtime, method)([object()], running_batch=NS(reqs=[object()]))
        assert not runtime.physical_disabled
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


def test_join_prepare_still_retires_and_publishes_old_pressure_nodes_without_waiters():
    runtime = NativeAdmissionRuntime()
    runtime.enable_prepare_host = True
    cache = NS(tree_core=NS())
    runtime.attach_native_cache(cache)
    key = PrefillCandidateKey("r", "w", "i", "c", 0, 0, "s", 1)
    runtime._remember_parent_pressure_node(11, key, 1.)
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
            return_value=StaticPoolHeadroomObservation(
                True, device_full_free_tokens=0, device_mamba_free_slots=0,
                host_full_free_tokens=1000, host_mamba_free_slots=10,
            ),
        ) as observed:
            runtime.dispatch_join_prepare([object()])
        observed.assert_called_once_with(cache)
        assert not runtime._parent_pressure_candidates
        assert cache.beliefkv_join_pressure_candidates == ()
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


def test_no_critical_parent_skips_the_reclaim_clock():
    runtime = NativeAdmissionRuntime()
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
            side_effect=AssertionError("no reclaim deadline to read"),
        ):
            runtime._reclaim_critical_parent_headroom()
    finally:
        runtime.close()


def test_ack_lookup_stops_after_the_requested_recent_receipts():
    class UntouchedReceipt:
        @property
        def action(self):
            raise AssertionError("older receipts cannot add proof for this burst")

    runtime = NativeAdmissionRuntime()
    runtime.completed_physical_actions.extend([UntouchedReceipt()] * 120)
    runtime.completed_physical_actions.extend((
        NS(command_id="first", action="PREFETCH_GPU"),
        NS(command_id="prepare", action="PREPARE_HOST"),
        NS(command_id="second", action="PREFETCH_GPU"),
    ))
    try:
        assert runtime._physical_actions_completed(
            ("first", "second"), action="PREFETCH_GPU",
        )
        assert runtime._physical_actions_completed(("prepare",))
        assert runtime._physical_actions_completed(())
    finally:
        runtime.close()


@pytest.mark.parametrize("commands,action,completed", (
    (("load",), "PREFETCH_GPU", True),
    (("load", "load"), "PREFETCH_GPU", True),
    (("load", "prepare"), "PREFETCH_GPU", False),
    (("load", "prepare"), None, True),
    (("unknown",), None, False),
    (("old",), "PREFETCH_GPU", False),
))
def test_ack_lookup_preserves_pool_action_and_bounded_history(commands, action, completed):
    runtime = NativeAdmissionRuntime()
    runtime.completed_physical_actions.append(NS(command_id="old", action="PREFETCH_GPU"))
    runtime.completed_physical_actions.extend(
        NS(command_id=f"other-{index}", action="PREFETCH_GPU") for index in range(128)
    )
    runtime.completed_physical_actions.extend((
        NS(command_id="load", action="PREFETCH_GPU"),
        NS(command_id="prepare", action="PREPARE_HOST"),
    ))
    try:
        assert runtime._physical_actions_completed(commands, action=action) is completed
    finally:
        runtime.close()


@pytest.mark.parametrize("source", ("join", "tool"))
def test_a_pending_burst_cannot_be_reconciled_from_old_completion_history(source):
    runtime = NativeAdmissionRuntime()
    runtime.physical_ledger.register(expected(
        "load", action="PREFETCH_GPU", children=(child(11, (11,), 10, 0, 10),),
    ))
    runtime.completed_physical_actions.append(NS(command_id="load", action="PREFETCH_GPU"))
    ticket = NS(command_id="load", command_ids=("load",))
    try:
        assert getattr(runtime, f"_reconcile_{source}_prefetch_ticket")(ticket) is None
        assert runtime.counts[f"{source}_prefetch_acked"] == 0
        assert runtime.physical_ledger.is_pending("load")
    finally:
        runtime.close()


@pytest.mark.parametrize("published", ((True,), (11.0,), (12,), (-1,), (11, 11)))
def test_single_node_registration_keeps_native_identity_validation(published):
    ledger = PhysicalTransactionLedger()
    with pytest.raises(PhysicalReceiptError):
        ledger.register(expected(children=(child(11, published, 10, 0, 10),)))
    assert ledger.pending_count == 0
    assert not ledger._pending_nodes
    assert ledger.pending_action_bytes("PREPARE_HOST") == 0


@pytest.mark.parametrize("first_nodes,second_nodes", (
    ((11,), (12,)), ((11, 13), (12, 14)),
))
def test_single_and_split_node_commands_keep_byte_budgets_and_collision_ownership(
    first_nodes, second_nodes,
):
    ledger = PhysicalTransactionLedger()
    command = expected(children=(
        child(11, first_nodes, 10, 0, 10),
        child(12, second_nodes, 20, 0, 20),
    ))
    ledger.register(command)
    assert ledger.pending_action_bytes("PREPARE_HOST") == 30
    assert ledger._pending_nodes == set(first_nodes + second_nodes)
    with pytest.raises(PhysicalReceiptError, match="already owned"):
        ledger.register(replace(
            command, command_id="collision", children=(child(11, (11,), 10, 0, 10),),
        ))
    assert ledger.pending_action_bytes("PREPARE_HOST") == 30
    ledger.cancel_unsubmitted(command.command_id)
    assert not ledger._pending_nodes
    assert ledger.pending_action_bytes("PREPARE_HOST") == 0
