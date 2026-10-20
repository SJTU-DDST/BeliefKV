"""Critical capacity preparation follows a proven JOIN into client reentry."""

from dataclasses import replace
from unittest.mock import Mock, patch
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind
from tests.test_join_confirmed_reentry import (
    client_submit, complete_join, continuing_join_fixture,
)
from tests.test_sglang_v0520_runtime import event, req


def handoff_fixture(phase):
    runtime, key, opportunity, cache, _ = continuing_join_fixture()
    runtime._borrowable_parent_headroom = True
    runtime.enable_prepare_host = True
    runtime.on_events((event(
        18, RuntimeEventKind.INVOCATION_CREATE, invocation_id="donor",
        context_id="ctx-donor", agent_definition_id="child", agent_instance_id="donor",
    ),))
    donor = req("donor")
    donor.session_id, donor.session_generation = "donor-session", 1
    runtime.register_visible_request(donor)
    runtime.on_events((event(
        19, RuntimeEventKind.TOOL_START, invocation_id="donor",
        attributes={"tool_run_id": "current-tool"},
    ),))
    runtime._set_critical_parent_demand(key, 4104, 1, 101.)
    if phase == "client_coalesced":
        runtime.on_events((
            event(20, RuntimeEventKind.RETURN, invocation_id="child"),
            event(21, RuntimeEventKind.JOIN_SATISFIED, join_id="join"),
            client_submit(),
        ))
    elif phase != "waiting":
        complete_join(runtime)
        if phase == "client":
            runtime.on_events((client_submit(),))
    return runtime, key, opportunity, cache, runtime.context_sessions["ctx-donor"]


@pytest.mark.parametrize("phase", ("ready", "client", "client_coalesced"))
def test_confirmed_join_keeps_capacity_and_unhinted_tool_donor_until_native_arrival(phase):
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=100.):
        runtime, origin, _, cache, donor = handoff_fixture(phase)
        try:
            ticket = runtime._confirmed_join_tickets["join"]
            assert runtime._live_join_ticket(ticket)
            demand = runtime._live_critical_parent_demand()
            assert demand == (ticket.key, 4104, 1, 101.)
            assert runtime._critical_tool_donor(donor)
            assert not runtime.tool_wait_hints
            if phase != "ready":
                assert runtime._terminal(origin)
                assert ticket.checkpoint_key == origin
                assert demand[0].request_id == "parent-next"
                assert runtime.counts["critical_parent_demand_client_bound"] == 1
                runtime._live_critical_parent_demand()
                assert runtime.counts["critical_parent_demand_client_bound"] == 1
            assert not runtime.physical_ledger.pending_count
            cache.prefetch_gpu_session_nodes.assert_not_called()
        finally:
            runtime.close()


@pytest.mark.parametrize("phase", ("ready", "client", "client_coalesced"))
def test_confirmed_capacity_reclaims_before_another_backup_with_the_current_restore_key(phase):
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=100.):
        runtime, _, opportunity, cache, _ = handoff_fixture(phase)
        cache.reclaim_beliefkv_handoff_capacity = Mock(return_value={"kv": 4})
        cache.beliefkv_handoff_last_victims = ()
        cache.beliefkv_handoff_last_capacity = {}
        try:
            ticket = runtime._confirmed_join_tickets["join"]
            with patch(
                "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
                return_value=NS(
                    observable=True, device_full_free_tokens=0, device_mamba_free_slots=2,
                ),
            ), patch.object(
                runtime, "_wait_prefetch_opportunity", return_value=opportunity,
            ) as inspect, patch.object(
                runtime, "_publish_parent_pressure_candidates",
            ) as publish:
                runtime._reclaim_critical_parent_headroom()
            inspect.assert_called_once_with(
                ticket.key, include_checkpoint_plan=True, apply_capacity_budget=False,
            )
            excluded = tuple(node.node_id for node in opportunity.candidate.nodes)
            publish.assert_called_once_with(exclude_node_ids=excluded)
            cache.reclaim_beliefkv_handoff_capacity.assert_called_once_with(
                full_tokens=4104, mamba_slots=1,
                exclude_node_ids=excluded, collect_diagnostics=True,
            )
            assert not runtime.physical_ledger.pending_count
        finally:
            runtime.close()


@pytest.mark.parametrize("phase", ("ready", "client"))
@pytest.mark.parametrize("invalid", (
    "demand_expired", "ticket_expired", "ticket_missing", "ticket_other_origin",
    "ticket_other_session", "session_replaced", "generation_replaced", "compact",
    "advance", "cancel", "workflow_end", "timeout", "noncontinuing", "native_arrival",
))
def test_capacity_handoff_requires_live_ticket_identity_and_ends_before_native_service(
    phase, invalid,
):
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=100.):
        runtime, origin, _, cache, donor = handoff_fixture(phase)
        ticket = runtime._confirmed_join_tickets["join"]
        try:
            assert runtime._live_join_ticket(ticket)
            if invalid == "demand_expired":
                runtime._critical_parent_demand = (origin, 4104, 1, 100.)
            elif invalid == "ticket_expired":
                ticket.expires_at = 100.
            elif invalid == "ticket_missing":
                runtime._discard_join_ticket("test_invalidation", ticket=ticket)
            elif invalid == "ticket_other_origin":
                runtime._critical_parent_demand = (
                    replace(origin, attempt_id=origin.attempt_id + 1), 4104, 1, 101.,
                )
            elif invalid == "ticket_other_session":
                ticket.key = replace(ticket.key, session_id="different")
            elif invalid in ("session_replaced", "generation_replaced"):
                runtime.context_sessions[origin.context_id] = replace(
                    origin, **(
                        {"session_id": "different"} if invalid == "session_replaced"
                        else {"session_generation": origin.session_generation + 1}
                    ),
                )
            elif invalid in ("compact", "advance"):
                runtime.on_events((event(
                    23, RuntimeEventKind.CONTEXT_COMPACT if invalid == "compact"
                    else RuntimeEventKind.CONTEXT_ADVANCE,
                    invocation_id="parent", context_id="ctx-parent", context_epoch=2,
                ),))
            elif invalid == "cancel":
                runtime.on_events((event(
                    23, RuntimeEventKind.INVOCATION_CANCEL, invocation_id="parent",
                ),))
            elif invalid == "workflow_end":
                runtime.on_events((event(23, RuntimeEventKind.WORKFLOW_END),))
            elif invalid == "timeout":
                runtime.on_events((event(23, RuntimeEventKind.JOIN_TIMEOUT, join_id="join"),))
            elif invalid == "noncontinuing":
                runtime._noncontinuing_joins.add("join")
            elif invalid == "native_arrival":
                if phase == "ready":
                    runtime.on_events((client_submit(),))
                target = req("parent-next")
                target.beliefkv_metadata.update(
                    invocation_id="parent", context_id="ctx-parent", context_epoch=1,
                )
                target.session_id, target.session_generation = (
                    origin.session_id, origin.session_generation,
                )
                runtime.register_visible_request(target)
            assert runtime._live_critical_parent_demand() is None
            assert not runtime._critical_tool_donor(donor)
            assert runtime._critical_parent_demand is None
            assert not runtime.physical_ledger.pending_count
            cache.prefetch_gpu_session_nodes.assert_not_called()
        finally:
            runtime.close()


def test_capacity_handoff_uses_the_earlier_ticket_deadline_without_refreshing_it():
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=100.):
        runtime, _, _, _, _ = handoff_fixture("client")
        ticket = runtime._confirmed_join_tickets["join"]
        ticket.expires_at = 100.5
        try:
            assert runtime._live_critical_parent_demand() == (ticket.key, 4104, 1, 100.5)
            runtime.on_events((client_submit(),))
            assert runtime._live_critical_parent_demand() == (ticket.key, 4104, 1, 100.5)
        finally:
            runtime.close()


@pytest.mark.parametrize("tool_start_position", ("before_submit", "after_submit"))
def test_coalesced_return_tool_start_and_submit_rearm_after_ticket_binding(tool_start_position):
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=100.):
        runtime, _, _, _, donor = handoff_fixture("waiting")
        runtime._prepare_probe_after_ms[donor] = 200_000.
        runtime._tool_prepare_next_ms = 200_000.
        start = event(
            23, RuntimeEventKind.TOOL_START, invocation_id="donor",
            attributes={"tool_run_id": "new-tool"},
        )
        events = [
            event(20, RuntimeEventKind.TOOL_END, invocation_id="donor",
                  attributes={"tool_run_id": "current-tool"}),
            event(21, RuntimeEventKind.RETURN, invocation_id="child"),
            event(22, RuntimeEventKind.JOIN_SATISFIED, join_id="join"),
        ]
        events.extend(
            (start, client_submit(sequence=24)) if tool_start_position == "before_submit"
            else (client_submit(sequence=23), replace(start, event_id="fresh-tool-start", ts_ms=24.)),
        )
        try:
            runtime.on_events(tuple(events))
            ticket = runtime._confirmed_join_tickets["join"]
            assert runtime._live_join_ticket(ticket)
            assert runtime._live_critical_parent_demand() == (ticket.key, 4104, 1, 101.)
            assert runtime._critical_tool_donor(donor)
            assert donor not in runtime._prepare_probe_after_ms
            assert runtime._tool_prepare_next_ms == 0.
            assert runtime.counts["critical_tool_boundary_rearmed"] == 1
            assert not runtime.physical_ledger.pending_count
        finally:
            runtime.close()
