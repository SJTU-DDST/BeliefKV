from dataclasses import replace
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.core.events import RuntimeEventKind
from tests.test_sglang_v0520_runtime import event, req
from tests.test_wait_prefetch_batch import (
    batch_fixture,
    provisional_join_batch_fixture,
)


def normal_join_fixture(*, provisional=False):
    fixture = (
        provisional_join_batch_fixture()
        if provisional else batch_fixture("join_ticket", completed_join=False)
    )
    runtime, key, opportunity, cache, reserved = fixture
    runtime.enable_admission_prefetch = False
    runtime.enable_final_stage_prefetch = True
    if not provisional:
        runtime._clear_final_stage("join")
        runtime._child_report_notices.clear()
        runtime._semantic_worker = NS(close=lambda: None, error="")
    return runtime, key, opportunity, cache, reserved


def complete_join(runtime, *, sequence=20, child="child", join="join"):
    runtime.on_events((
        event(sequence, RuntimeEventKind.RETURN, invocation_id=child),
        event(sequence + 1, RuntimeEventKind.JOIN_SATISFIED, join_id=join),
    ))


def add_second_join(runtime):
    parent = req("parent2")
    parent.session_id, parent.session_generation = "s2", 1
    runtime.register_visible_request(parent)
    runtime.on_events((
        event(10, RuntimeEventKind.INVOCATION_CREATE, invocation_id="parent2",
              context_id="ctx-parent2", agent_definition_id="root",
              agent_instance_id="parent2"),
        event(11, RuntimeEventKind.INVOCATION_CREATE, invocation_id="child2",
              context_id="ctx-child2", agent_definition_id="child",
              agent_instance_id="child2"),
        event(12, RuntimeEventKind.JOIN_CREATE, join_id="join2",
              member_invocation_ids=("child2",)),
        event(13, RuntimeEventKind.JOIN_WAIT, invocation_id="parent2", join_id="join2"),
    ))
    return runtime.context_sessions["ctx-parent2"]


def test_normal_mode_restores_confirmed_join_without_a_forecast():
    runtime, _, opportunity, cache, _ = normal_join_fixture()
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        complete_join(runtime)
        assert runtime._join_ticket is not None
        assert runtime._join_ticket.phase == "confirmed"
        assert runtime._live_join_ticket()
        assert not runtime._final_stages
        assert not runtime._h2d_samples
        with patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=opportunity,
        ), patch.object(runtime, "_h2d_start_window") as timing:
            runtime.dispatch_join_prefetch()
        timing.assert_not_called()
        assert cache.prefetch_gpu_session_nodes.call_count == 1
        assert runtime.physical_ledger.pending_count == 4
        assert runtime.counts["join_reentry_confirmed"] == 1
        assert runtime.counts["join_prefetch_confirmed_issued"] == 4
        issued = [row for row in records if row["event"] == "prefetch_native_issued"]
        assert len(issued) == 4
        assert all(row["phase"] == "confirmed" for row in issued)
        assert all(row["pre_boundary_prediction"] is False for row in issued)
        assert all(
            row["timing_policy"] == "confirmed_join_before_parent_request"
            for row in issued
        )
    finally:
        runtime.close()


def test_confirmed_join_keeps_the_same_inflight_burst_after_semantic_cleanup():
    runtime, _, opportunity, cache, _ = normal_join_fixture(provisional=True)
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=opportunity,
        ):
            runtime.dispatch_join_prefetch()
            ticket = runtime._join_ticket
            assert ticket is not None and ticket.phase == "provisional"
            commands = ticket.command_ids
            assert len(commands) == 4
            complete_join(runtime)
            assert runtime._join_ticket is ticket
            assert ticket.phase == "confirmed"
            assert ticket.command_ids == commands
            assert not runtime._final_stages
            runtime.dispatch_join_prefetch()
        assert cache.prefetch_gpu_session_nodes.call_count == 1
        assert runtime.physical_ledger.pending_count == 4
        assert set(runtime._prefetch_steps) == set(commands)
    finally:
        runtime.close()


def test_coalesced_parent_submit_does_not_restore_the_previous_epoch():
    runtime, _, opportunity, cache, _ = normal_join_fixture()
    try:
        runtime.on_events((
            event(20, RuntimeEventKind.RETURN, invocation_id="child"),
            event(21, RuntimeEventKind.JOIN_SATISFIED, join_id="join"),
            event(22, RuntimeEventKind.LLM_SUBMIT, invocation_id="parent",
                  context_id="ctx-parent", context_epoch=1,
                  attributes={"request_id": "parent-next"}),
        ))
        with patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=opportunity,
        ):
            runtime.dispatch_join_prefetch()
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime._join_ticket is None
        assert not runtime._confirmed_join_tickets
    finally:
        runtime.close()


@pytest.mark.parametrize("following", ("timeout", "cancel", "compact", "workflow_end"))
def test_confirmed_join_revalidates_parent_lifecycle_before_enqueue(following):
    runtime, _, opportunity, cache, _ = normal_join_fixture()
    changes = {
        "timeout": event(22, RuntimeEventKind.JOIN_TIMEOUT, join_id="join"),
        "cancel": event(22, RuntimeEventKind.INVOCATION_CANCEL, invocation_id="parent"),
        "compact": event(22, RuntimeEventKind.CONTEXT_COMPACT, invocation_id="parent",
                         context_id="ctx-parent", context_epoch=1),
        "workflow_end": event(22, RuntimeEventKind.WORKFLOW_END),
    }
    try:
        complete_join(runtime)
        assert runtime._join_ticket is not None
        runtime.event_server = NS(
            drain=lambda **_kwargs: runtime.on_events((changes[following],)),
            close=lambda: None,
        )
        with patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=opportunity,
        ):
            runtime.dispatch_join_prefetch()
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


def test_reactive_completion_does_not_enable_restoration():
    runtime, _, opportunity, cache, _ = normal_join_fixture()
    runtime.enable_final_stage_prefetch = False
    try:
        complete_join(runtime)
        with patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=opportunity,
        ):
            runtime.dispatch_join_prefetch()
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime._join_ticket is None
        assert not runtime._confirmed_join_tickets
    finally:
        runtime.close()


def test_two_confirmed_joins_keep_both_targets_and_skip_an_unrestorable_parent():
    runtime, _, opportunity, cache, _ = normal_join_fixture()
    try:
        second_key = add_second_join(runtime)
        complete_join(runtime)
        complete_join(runtime, sequence=22, child="child2", join="join2")
        assert set(runtime._confirmed_join_tickets) == {"join", "join2"}
        second_steps = tuple(replace(step, key=second_key) for step in opportunity.steps)
        second = replace(
            opportunity, anchors=replace(opportunity.anchors, key=second_key),
            step=second_steps[0], steps=second_steps,
        )
        with patch.object(
            runtime, "_wait_prefetch_opportunity",
            side_effect=lambda key, **_kwargs: (
                second if key == second_key else None
            ),
        ):
            runtime.dispatch_join_prefetch()
            runtime.dispatch_join_prefetch()
        assert cache.prefetch_gpu_session_nodes.call_count == 1
        assert cache.prefetch_gpu_session_nodes.call_args.kwargs["session_id"] == "s2"
        assert runtime._join_ticket.join_id == "join2"
        assert runtime.physical_ledger.pending_count == 4
    finally:
        runtime.close()


def test_repeated_join_confirmation_does_not_extend_the_restore_deadline():
    runtime, _, _, _, _ = normal_join_fixture()
    try:
        complete_join(runtime)
        ticket = runtime._join_ticket
        assert ticket is not None
        expires = ticket.expires_at
        runtime.on_events((event(22, RuntimeEventKind.JOIN_SATISFIED, join_id="join"),))
        assert runtime._join_ticket is ticket
        assert ticket.expires_at == expires
        assert runtime.counts["join_reentry_confirmed"] == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("invalidate", ("expiry", "native_arrival", "cancel"))
def test_queued_confirmation_rechecks_expiry_and_parent_identity(invalidate):
    runtime, _, opportunity, cache, _ = normal_join_fixture()
    try:
        add_second_join(runtime)
        complete_join(runtime)
        complete_join(runtime, sequence=22, child="child2", join="join2")
        second = runtime._confirmed_join_tickets["join2"]
        if invalidate == "expiry":
            second.expires_at = time.monotonic() - 1
        elif invalidate == "native_arrival":
            request = req("parent-next")
            request.beliefkv_metadata.update(
                invocation_id="parent2", context_id="ctx-parent2", context_epoch=1,
            )
            request.session_id, request.session_generation = "s2", 1
            runtime.register_visible_request(request)
        else:
            runtime.on_events((event(
                24, RuntimeEventKind.INVOCATION_CANCEL, invocation_id="parent2",
            ),))
        runtime._discard_join_ticket("test_first_target_done")
        with patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=opportunity,
        ):
            runtime.dispatch_join_prefetch()
        assert runtime._join_ticket is None
        assert not runtime._confirmed_join_tickets
        cache.prefetch_gpu_session_nodes.assert_not_called()
    finally:
        runtime.close()


def test_confirmation_waits_for_the_last_required_child():
    runtime, _, opportunity, cache, _ = normal_join_fixture()
    try:
        runtime.graph.joins["join"].member_invocation_ids.add("other")
        runtime._join_by_invocation.setdefault("other", set()).add("join")
        runtime.on_events((event(20, RuntimeEventKind.RETURN, invocation_id="child"),))
        with patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=opportunity,
        ):
            runtime.dispatch_join_prefetch()
            cache.prefetch_gpu_session_nodes.assert_not_called()
            assert runtime._join_ticket is None
            complete_join(runtime, sequence=21, child="other")
            runtime.dispatch_join_prefetch()
        assert cache.prefetch_gpu_session_nodes.call_count == 1
        assert runtime.counts["join_reentry_confirmed"] == 1
    finally:
        runtime.close()


def test_confirmed_completion_does_not_override_physical_capacity():
    runtime, _, opportunity, cache, _ = normal_join_fixture()
    try:
        complete_join(runtime)
        insufficient = replace(opportunity, fits_current_free_lists=False)
        with patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=insufficient,
        ), patch.object(
            runtime, "_reclaim_wait_prefetch_capacity", return_value=insufficient,
        ):
            runtime.dispatch_join_prefetch()
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("mode, expected", (("all", 0), ("any", 1)))
def test_failed_all_join_does_not_restore_and_any_mode_keeps_its_existing_semantics(
    mode, expected,
):
    runtime, _, opportunity, cache, _ = normal_join_fixture()
    try:
        join = runtime.graph.joins["join"]
        join.mode = type(join.mode)(mode)
        join.member_invocation_ids.add("other")
        runtime._join_by_invocation.setdefault("other", set()).add("join")
        runtime.on_events((
            event(20, RuntimeEventKind.INVOCATION_CANCEL, invocation_id="other"),
            event(21, RuntimeEventKind.RETURN, invocation_id="child"),
        ))
        with patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=opportunity,
        ):
            runtime.dispatch_join_prefetch()
        assert cache.prefetch_gpu_session_nodes.call_count == expected
    finally:
        runtime.close()
