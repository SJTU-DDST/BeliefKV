"""An unproductive confirmed JOIN must leave room for an observed EOS."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from tests.test_join_confirmed_reentry import parallel_join_fixture, complete_join


def competing_joins():
    runtime, first, second, plans, cache, acknowledge = parallel_join_fixture(provisional=True)
    complete_join(runtime)
    return runtime, first, second, plans, cache, acknowledge


@pytest.mark.parametrize("reason", (
    "no_host_backed_missing_copy", "already_device_resident",
    "no_reusable_input_restore_step", "prefetch_capacity_budget",
    "closure_unobservable", None,
))
def test_confirmed_empty_plan_does_not_block_another_parents_eos(reason):
    runtime, first, second, plans, cache, _ = competing_joins()
    confirmed = runtime._confirmed_join_tickets["join"]
    plans[first.context_id] = (
        replace(
            plans[first.context_id], step=None, steps=(),
            required_full_tokens=0, required_mamba_slots=0,
            no_step_reason=reason,
        ) if reason is not None else None
    )
    captures = []

    def capture(**kwargs):
        captures.append(kwargs["context_id"])
        return plans[kwargs["context_id"]]

    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity", side_effect=capture):
            runtime.dispatch_join_prefetch()
        assert captures == [first.context_id, second.context_id]
        cache.prefetch_gpu_session_nodes.assert_called_once()
        assert cache.prefetch_gpu_session_nodes.call_args.kwargs["session_id"] == second.session_id
        assert runtime.physical_ledger.pending_count == 4
        assert runtime._join_ticket.join_id == "join2"
        assert runtime._confirmed_join_tickets["join"] is confirmed
        assert not confirmed.restoration_complete
        assert confirmed.issued_nodes == 0
        assert runtime._live_join_ticket(confirmed)
        assert runtime.counts["confirmed_join_yielded_to_observed_eos"] == 1
        assert not runtime.physical_disabled
    finally:
        runtime.close()


def test_confirmed_capacity_block_yields_without_claiming_its_restoration_complete():
    runtime, first, second, plans, cache, _ = competing_joins()
    confirmed = runtime._confirmed_join_tickets["join"]
    plans[first.context_id] = replace(plans[first.context_id], fits_current_free_lists=False)
    try:
        with patch.object(
            runtime, "_wait_prefetch_opportunity",
            side_effect=lambda key, **kwargs: plans[key.context_id],
        ):
            runtime.dispatch_join_prefetch()
        cache.prefetch_gpu_session_nodes.assert_called_once()
        assert cache.prefetch_gpu_session_nodes.call_args.kwargs["session_id"] == second.session_id
        assert confirmed.command_id is None
        assert not confirmed.restoration_complete
        assert runtime._confirmed_join_tickets["join"] is confirmed
        assert runtime.counts["semantic_h2d_no_free_capacity"] == 1
        assert runtime.counts["confirmed_join_yielded_to_observed_eos"] == 1
    finally:
        runtime.close()


def test_declined_confirmed_enqueue_gives_eos_one_native_attempt():
    runtime, first, second, plans, cache, _ = competing_joins()
    native_issue = runtime._issue_wait_prefetch_steps
    keys = []

    def issue(key, *args, **kwargs):
        keys.append(key)
        return () if key == first else native_issue(key, *args, **kwargs)

    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ), patch.object(runtime, "_issue_wait_prefetch_steps", side_effect=issue):
            runtime.dispatch_join_prefetch()
        assert keys == [first, second]
        cache.prefetch_gpu_session_nodes.assert_called_once()
        assert runtime._join_ticket.join_id == "join2"
        assert runtime._confirmed_join_tickets["join"].issued_nodes == 0
        assert runtime.counts["confirmed_join_yielded_to_observed_eos"] == 1
    finally:
        runtime.close()


def test_eos_without_an_actionable_plan_is_tried_once_without_recursive_loop():
    runtime, first, second, plans, cache, _ = competing_joins()
    captures = []
    empty = NS(step=None, no_step_reason="no_host_backed_missing_copy")

    def capture(**kwargs):
        captures.append(kwargs["context_id"])
        return empty

    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity", side_effect=capture):
            runtime.dispatch_join_prefetch()
        assert captures == [first.context_id, second.context_id]
        assert runtime.counts["confirmed_join_yielded_to_observed_eos"] == 1
        assert runtime._join_ticket is None
        assert runtime._confirmed_join_tickets["join"].issued_nodes == 0
        assert runtime.physical_ledger.pending_count == 0
        cache.prefetch_gpu_session_nodes.assert_not_called()
    finally:
        runtime.close()


def test_no_eos_does_not_add_scans_or_change_the_confirmed_ticket():
    runtime, first, _, _, cache, _ = competing_joins()
    runtime._eos_prefetch_keys.clear()
    confirmed = runtime._confirmed_join_tickets["join"]
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            return_value=NS(step=None, no_step_reason="no_host_backed_missing_copy"),
        ) as capture:
            runtime.dispatch_join_prefetch()
        capture.assert_called_once()
        assert capture.call_args.kwargs["context_id"] == first.context_id
        assert runtime._join_ticket is confirmed
        assert runtime.counts["confirmed_join_yielded_to_observed_eos"] == 0
        cache.prefetch_gpu_session_nodes.assert_not_called()
    finally:
        runtime.close()


def test_productive_confirmed_keeps_priority_and_does_not_speculatively_yield():
    runtime, first, _, plans, cache, _ = competing_joins()
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ) as capture:
            runtime.dispatch_join_prefetch()
        capture.assert_called_once()
        assert capture.call_args.kwargs["context_id"] == first.context_id
        assert cache.prefetch_gpu_session_nodes.call_args.kwargs["session_id"] == first.session_id
        assert runtime._join_ticket.phase == "confirmed"
        assert runtime.counts["confirmed_join_yielded_to_observed_eos"] == 0
    finally:
        runtime.close()


def test_retained_confirmed_can_restore_after_the_eos_transfer_is_acked():
    runtime, first, second, plans, cache, acknowledge = competing_joins()
    original = plans[first.context_id]
    plans[first.context_id] = replace(
        original, step=None, steps=(), required_full_tokens=0, required_mamba_slots=0,
        no_step_reason="no_host_backed_missing_copy",
    )
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ):
            runtime.dispatch_join_prefetch()
            eos_commands = runtime._join_ticket.command_ids
            assert eos_commands
            acknowledge(eos_commands)
            plans[first.context_id] = original
            plans[second.context_id] = replace(
                plans[second.context_id], step=None, steps=(),
                required_full_tokens=0, required_mamba_slots=0,
                no_step_reason="no_host_backed_missing_copy",
            )
            runtime.dispatch_join_prefetch()
            runtime.dispatch_join_prefetch()
        assert cache.prefetch_gpu_session_nodes.call_count == 2
        assert cache.prefetch_gpu_session_nodes.call_args.kwargs["session_id"] == first.session_id
        assert runtime._confirmed_join_tickets["join"].command_id is not None
        assert runtime.counts["native_physical_completed"] == 4
        assert not runtime.physical_disabled
    finally:
        runtime.close()
