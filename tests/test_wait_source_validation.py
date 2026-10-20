"""Reuse only the pending JOIN ticket validated in the current source check."""

from dataclasses import replace
from unittest.mock import patch

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_wait_prefetch_dispatch_cpu import dispatch_fixture


def source_fixture(*, client_bound=False, source="join_ticket", depth=4):
    values = dispatch_fixture(
        NativeAdmissionRuntime, source=source, depth=depth, native_budget=True,
    )
    runtime, key, cache, _, actions, _, _, dispatch, stamp = values
    if client_bound:
        ticket = runtime._join_ticket
        runtime._confirmed_join_tickets[ticket.join_id] = ticket
        runtime._continuing_joins.add(ticket.join_id)
        runtime.on_events((RuntimeEvent(
            "test-client-submit", 999999., RuntimeEventKind.LLM_SUBMIT,
            key.root_workflow_id, invocation_id=key.invocation_id,
            context_id=key.context_id, context_epoch=key.context_epoch + 1,
            attributes={"request_id": "parent-next"},
        ),))
        key = runtime._join_ticket.key
        assert runtime._join_ticket.client_request_id == "parent-next"
    return runtime, key, cache, actions, dispatch, stamp


@pytest.mark.parametrize("client_bound", (False, True))
def test_each_source_check_validates_the_join_once_without_a_cross_call_cache(client_bound):
    runtime, key, _, _, _, stamp = source_fixture(client_bound=client_bound)
    try:
        with patch("time.monotonic", new=lambda: stamp), patch.object(
            runtime, "_live_join_ticket", wraps=runtime._live_join_ticket,
        ) as validate:
            assert runtime._live_wait_prefetch(key, "join_ticket")
            assert runtime._live_wait_prefetch(key, "join_ticket")
        assert validate.call_count == 2
    finally:
        runtime.close()


@pytest.mark.parametrize("client_bound", (False, True))
@pytest.mark.parametrize("change", (
    "missing_context", "advanced_context", "parent_ready_wrong_state",
    "wrong_session", "workflow_end", "physical_disabled", "expired_ticket",
    "wrong_key", "changed_members", "missing_ticket",
))
def test_each_join_check_retains_live_context_source_and_lifecycle_guards(client_bound, change):
    runtime, key, _, _, _, stamp = source_fixture(client_bound=client_bound)
    ticket = runtime._join_ticket
    try:
        with patch("time.monotonic", new=lambda: stamp):
            assert runtime._live_wait_prefetch(key, "join_ticket")
            if change == "missing_context":
                del runtime.graph.contexts[key.context_id]
            elif change == "advanced_context":
                runtime.graph.contexts[key.context_id].epoch += 1
            elif change == "parent_ready_wrong_state":
                runtime.graph.invocations[key.invocation_id].state = InvocationState.WAIT_TOOL
            elif change == "wrong_session":
                runtime.context_sessions[key.context_id] = replace(key, session_generation=99)
            elif change == "workflow_end":
                runtime.graph.workflows[key.root_workflow_id].end_ts_ms = 999999.
            elif change == "physical_disabled":
                runtime.physical_disabled = True
            elif change == "expired_ticket":
                ticket.expires_at = stamp
            elif change == "wrong_key":
                key = replace(key, request_id="other-request")
            elif change == "changed_members":
                runtime.graph.joins[ticket.join_id].member_invocation_ids.add("new-child")
            else:
                runtime._join_ticket = None
            assert not runtime._live_wait_prefetch(key, "join_ticket")
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ("missing_map_owner", "context_membership", "unrelated_pending"))
def test_client_bound_source_keeps_pending_ticket_ownership_requirements(change):
    runtime, key, _, _, _, stamp = source_fixture(client_bound=True)
    ticket = runtime._join_ticket
    if change == "missing_map_owner":
        runtime._confirmed_join_tickets.clear()
    elif change == "context_membership":
        runtime.graph.contexts[key.context_id].invocation_ids.clear()
    else:
        runtime._confirmed_join_tickets[ticket.join_id] = replace(
            ticket, key=replace(key, request_id="other-request"),
        )
    try:
        with patch("time.monotonic", new=lambda: stamp):
            assert runtime._live_join_ticket(ticket)
            assert not runtime._live_wait_prefetch(key, "join_ticket")
    finally:
        runtime.close()


def test_a_different_pending_ticket_does_not_prove_the_selected_ticket_live():
    runtime, key, _, _, _, stamp = source_fixture(client_bound=True)
    ticket = runtime._join_ticket
    runtime._confirmed_join_tickets[ticket.join_id] = replace(ticket)
    try:
        with patch("time.monotonic", new=lambda: stamp), patch.object(
            runtime, "_live_join_ticket", wraps=runtime._live_join_ticket,
        ) as validate:
            assert runtime._live_wait_prefetch(key, "join_ticket")
        assert validate.call_count == 2
        ticket.expires_at = stamp
        with patch("time.monotonic", new=lambda: stamp):
            assert runtime._pending_confirmed_join_submit(key)
            assert not runtime._live_wait_prefetch(key, "join_ticket")
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ("advanced_context", "completed_source", "removed_map_owner"))
def test_client_bound_burst_rechecks_source_before_every_node(change):
    runtime, key, cache, actions, dispatch, stamp = source_fixture(client_bound=True)
    native = cache.prefetch_gpu_session_nodes

    def interrupted(**kwargs):
        reserve = kwargs["beliefkv_before_enqueue"]

        def before_enqueue(operation):
            if operation.node_ids == [2]:
                if change == "advanced_context":
                    runtime.graph.contexts[key.context_id].epoch += 1
                elif change == "completed_source":
                    runtime.graph.invocations[key.invocation_id].state = InvocationState.DONE
                else:
                    runtime._confirmed_join_tickets.clear()
            return reserve(operation)

        return native(**{**kwargs, "beliefkv_before_enqueue": before_enqueue})

    cache.prefetch_gpu_session_nodes = interrupted
    try:
        with patch("time.monotonic", new=lambda: stamp):
            dispatch()
        assert [item[0] for item in actions] == [1]
        assert runtime.physical_ledger.pending_count == 1
        assert not runtime.completed_physical_actions
        assert not runtime.physical_disabled
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ("context_advanced", "hint_expired", "tool_returned"))
def test_tool_source_still_requires_current_context_and_live_hint(change):
    runtime, key, _, _, _, stamp = source_fixture(source="tool_wait")
    try:
        with patch("time.monotonic", new=lambda: stamp):
            runtime._roll_tool_prefetch()
            assert runtime._live_wait_prefetch(key, "tool_wait")
            if change == "context_advanced":
                runtime.graph.contexts[key.context_id].epoch += 1
            elif change == "hint_expired":
                hint = runtime.tool_wait_hints[key.context_id]
                runtime.tool_wait_hints[key.context_id] = replace(
                    hint, expires_monotonic_ms=stamp * 1000.,
                )
            else:
                runtime.graph.invocations[key.invocation_id].state = InvocationState.READY
            assert not runtime._live_wait_prefetch(key, "tool_wait")
    finally:
        runtime.close()
