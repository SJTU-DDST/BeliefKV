"""Confirmed JOINs with proven empty restores do not enter recovery selection."""

from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.core.events import RuntimeEventKind
from tests.test_join_confirmed_reentry import (
    client_submit,
    complete_join,
    continuing_join_fixture,
    parallel_join_fixture,
)
from tests.test_sglang_v0520_physical import prepare_prefix_fixture
from tests.test_sglang_v0520_runtime import event, req


def observable_parent(runtime, key, cache, state):
    native, anchors, nodes = prepare_prefix_fixture(input_tokens=16, backed=True)
    snapshots = Mock(return_value=anchors.component_leaves)
    cache.tree_core = native.tree_core
    cache.session_refs = NS(
        snapshot_latest_session_leaf_anchors=snapshots,
        snapshot_session_leaf_anchors=snapshots,
    )
    runtime._context_tokens[key.context_id] = (key.context_epoch, 17, 0, False)
    if state == "full":
        nodes[1].component_data[0].value = None
    elif state == "mamba":
        nodes[3].component_data[2].host_value = (0,)
        nodes[3].component_data[2].value = None
    elif state == "no_host":
        nodes[1].component_data[0].value = None
        nodes[1].component_data[0].host_value = None
    elif state == "historical_mamba":
        nodes[1].component_data[2].host_value = (0,)
        nodes[1].component_data[2].value = None
    elif state == "missing_anchors":
        snapshots.return_value = None
    elif state == "stale_anchors":
        snapshots.return_value = ((0, ((3, 99.),)), (2, ((3, 99.),)))
    elif state == "unobservable_tree":
        cache.tree_core.node_by_id = Mock(side_effect=KeyError)
    elif state != "resident":
        raise AssertionError(state)
    return nodes, snapshots


@pytest.mark.parametrize("state", ("resident", "no_host", "historical_mamba"))
@pytest.mark.parametrize("coalesced", (False, True))
def test_empty_return_and_client_submit_do_not_create_a_recovery_ticket(state, coalesced):
    runtime, key, _, cache, _ = continuing_join_fixture()
    _, snapshots = observable_parent(runtime, key, cache, state)
    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity") as planner:
            if coalesced:
                runtime.on_events((
                    event(20, RuntimeEventKind.RETURN, invocation_id="child"),
                    event(21, RuntimeEventKind.JOIN_SATISFIED, join_id="join"),
                    client_submit(),
                ))
            else:
                complete_join(runtime)
                assert runtime._empty_join_reentry_origins == {"join": key}
                assert snapshots.call_count == 1
                runtime.on_events((client_submit(),))
            runtime.dispatch_join_prefetch()
        planner.assert_not_called()
        assert snapshots.call_count == (1 if coalesced else 2)
        assert runtime.counts["confirmed_join_empty_restore_skipped"] == (1 if coalesced else 2)
        assert runtime.counts["join_reentry_confirmed"] == 0
        assert runtime._join_ticket is None
        assert not runtime._confirmed_join_tickets
        assert not runtime._empty_join_reentry_origins
        assert runtime.physical_ledger.pending_count == 0
        cache.prefetch_gpu_session_nodes.assert_not_called()
    finally:
        runtime.close()


@pytest.mark.parametrize("state", ("full", "mamba"))
@pytest.mark.parametrize("free_tokens", (0, 100))
def test_host_only_data_keeps_the_ticket_independently_of_free_capacity(state, free_tokens):
    runtime, key, _, cache, _ = continuing_join_fixture()
    observable_parent(runtime, key, cache, state)
    cache.token_to_kv_pool_allocator = NS(available_size=Mock(return_value=free_tokens))
    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity") as planner:
            complete_join(runtime)
        planner.assert_not_called()
        assert runtime._confirmed_join_tickets["join"].key == key
        assert runtime._live_join_ticket()
        assert runtime.counts["confirmed_join_empty_restore_skipped"] == 0
        cache.token_to_kv_pool_allocator.available_size.assert_not_called()
    finally:
        runtime.close()


@pytest.mark.parametrize("state", ("missing_anchors", "stale_anchors", "unobservable_tree"))
def test_unobservable_restore_does_not_become_a_zero_byte_proof(state):
    runtime, key, _, cache, _ = continuing_join_fixture()
    observable_parent(runtime, key, cache, state)
    try:
        complete_join(runtime)
        assert runtime._confirmed_join_tickets["join"].key == key
        assert runtime.counts["confirmed_join_empty_restore_skipped"] == 0
    finally:
        runtime.close()


def test_empty_confirmed_parent_leaves_the_first_selection_for_another_observed_eos():
    runtime, first, second, plans, cache, _ = parallel_join_fixture(provisional=True)
    _, snapshots = observable_parent(runtime, first, cache, "resident")
    captures = []

    def capture(**kwargs):
        captures.append(kwargs["context_id"])
        return plans[kwargs["context_id"]]

    try:
        complete_join(runtime)
        assert snapshots.call_count == 1
        assert "join" not in runtime._confirmed_join_tickets
        assert runtime._join_ticket is None
        with patch.object(runtime, "inspect_context_h2d_opportunity", side_effect=capture):
            runtime.dispatch_join_prefetch()
        assert captures == [second.context_id]
        assert cache.prefetch_gpu_session_nodes.call_count == 1
        assert cache.prefetch_gpu_session_nodes.call_args.kwargs["session_id"] == second.session_id
        assert runtime.physical_ledger.pending_count == 4
        assert runtime.counts["confirmed_join_yielded_to_observed_eos"] == 0
        assert runtime._join_ticket.join_id == "join2"
    finally:
        runtime.close()


@pytest.mark.parametrize("state", ("full", "mamba"))
def test_next_client_submit_rechecks_residency_without_polling_the_empty_parent(state):
    runtime, key, _, cache, _ = continuing_join_fixture()
    nodes, snapshots = observable_parent(runtime, key, cache, "resident")
    try:
        complete_join(runtime)
        assert runtime._join_ticket is None
        for _ in range(3):
            runtime.dispatch_join_prefetch()
        assert snapshots.call_count == 1
        component = nodes[1].component_data[0] if state == "full" else nodes[3].component_data[2]
        component.host_value = component.value
        component.value = None
        runtime.on_events((client_submit(),))
        ticket = runtime._confirmed_join_tickets["join"]
        assert snapshots.call_count == 2
        assert ticket.checkpoint_key == key
        assert ticket.checkpoint_input_tokens == 16
        assert ticket.client_request_id == "parent-next"
        assert runtime._live_join_ticket()
        assert not runtime._empty_join_reentry_origins
    finally:
        runtime.close()


def test_a_later_completion_event_rechecks_newly_missing_pages():
    runtime, key, _, cache, _ = continuing_join_fixture()
    nodes, snapshots = observable_parent(runtime, key, cache, "resident")
    try:
        runtime.on_events((event(20, RuntimeEventKind.RETURN, invocation_id="child"),))
        assert runtime._join_ticket is None
        nodes[1].component_data[0].value = None
        runtime.on_events((event(21, RuntimeEventKind.JOIN_SATISFIED, join_id="join"),))
        assert snapshots.call_count == 2
        assert runtime._confirmed_join_tickets["join"].key == key
        assert not runtime._empty_join_reentry_origins
    finally:
        runtime.close()


def test_unissued_confirmed_ticket_is_removed_when_client_submit_proves_restore_empty():
    runtime, key, _, cache, _ = continuing_join_fixture()
    try:
        complete_join(runtime)
        assert runtime._confirmed_join_tickets["join"].issued_nodes == 0
        observable_parent(runtime, key, cache, "resident")
        runtime.on_events((client_submit(),))
        assert runtime._join_ticket is None
        assert not runtime._confirmed_join_tickets
        assert not runtime._empty_join_reentry_origins
        assert runtime.counts["confirmed_join_client_bound"] == 0
        assert runtime.counts["confirmed_join_empty_restore_skipped"] == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("ack_before_return", (False, True))
def test_observed_empty_state_does_not_drop_an_issued_burst_or_its_client_binding(ack_before_return):
    runtime, first, _, plans, cache, acknowledge = parallel_join_fixture(provisional=True)
    runtime._continuing_joins.add("join")
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ):
            runtime.dispatch_join_prefetch()
        ticket = runtime._join_ticket
        assert ticket.key == first
        commands = ticket.command_ids
        assert len(commands) == 4
        _, snapshots = observable_parent(runtime, first, cache, "resident")
        if ack_before_return:
            acknowledge(commands)
        complete_join(runtime)
        runtime.on_events((client_submit(),))
        assert runtime._confirmed_join_tickets["join"] is ticket
        assert ticket.command_ids == commands
        assert ticket.checkpoint_key == first
        assert ticket.client_request_id == "parent-next"
        assert runtime._live_join_ticket()
        snapshots.assert_not_called()
        if not ack_before_return:
            acknowledge(commands)
        assert runtime.physical_ledger.pending_count == 0
        assert runtime.counts["native_physical_completed"] == 4
        assert not runtime.physical_disabled
    finally:
        runtime.close()


@pytest.mark.parametrize("invalidate", ("cancel", "compact", "workflow_end", "join_timeout"))
def test_empty_reentry_identity_is_removed_on_lifecycle_invalidation(invalidate):
    runtime, key, _, cache, _ = continuing_join_fixture()
    observable_parent(runtime, key, cache, "resident")
    changes = {
        "cancel": event(23, RuntimeEventKind.INVOCATION_CANCEL, invocation_id="parent"),
        "compact": event(
            23, RuntimeEventKind.CONTEXT_COMPACT, invocation_id="parent",
            context_id="ctx-parent", context_epoch=1,
        ),
        "workflow_end": event(23, RuntimeEventKind.WORKFLOW_END),
        "join_timeout": event(23, RuntimeEventKind.JOIN_TIMEOUT, join_id="join"),
    }
    try:
        complete_join(runtime)
        assert runtime._empty_join_reentry_origins == {"join": key}
        runtime.on_events((changes[invalidate],))
        assert not runtime._empty_join_reentry_origins
        assert not runtime._confirmed_join_tickets
    finally:
        runtime.close()


def test_an_old_satisfied_join_does_not_authorize_later_unrelated_client_submissions():
    runtime, key, _, cache, _ = continuing_join_fixture()
    nodes, snapshots = observable_parent(runtime, key, cache, "resident")
    try:
        complete_join(runtime)
        runtime.on_events((client_submit(),))
        target = req("parent-next")
        target.beliefkv_metadata.update(
            invocation_id="parent", context_id="ctx-parent", context_epoch=1,
        )
        target.session_id, target.session_generation = key.session_id, key.session_generation
        assert runtime.register_visible_request(target)
        runtime._context_tokens["ctx-parent"] = (1, 17, 0, False)
        runtime.on_events((event(
            23, RuntimeEventKind.LLM_RESULT, invocation_id="parent", context_id="ctx-parent",
            context_epoch=1,
        ),))
        nodes[1].component_data[0].value = None
        runtime.on_events((client_submit(
            sequence=24, context_epoch=2, attributes={"request_id": "unrelated-next"},
        ),))
        assert snapshots.call_count == 2
        assert not runtime._confirmed_join_tickets
        assert runtime._join_ticket is None
    finally:
        runtime.close()
