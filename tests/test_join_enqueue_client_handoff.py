from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.core.events import RuntimeEventKind
from tests.test_join_confirmed_reentry import (
    client_submit, complete_join, continuing_join_fixture,
)
from tests.test_sglang_v0520_runtime import event, req


def rekey_opportunity(opportunity, key):
    anchors = replace(opportunity.anchors, key=key)
    steps = tuple(replace(step, key=key) for step in opportunity.steps)
    return replace(
        opportunity, anchors=anchors,
        candidate=replace(opportunity.candidate, anchors=anchors),
        step=steps[0], steps=steps,
    )


@pytest.mark.parametrize("invalidation", (
    None, "native_arrival", "generation", "advance", "cancel",
    "no_checkpoint", "disabled", "second_drain_native_arrival",
))
def test_join_enqueue_replans_only_the_proven_client_handoff(invalidation):
    runtime, origin, opportunity, cache, _ = continuing_join_fixture()
    complete_join(runtime)
    records, requested_epochs, bound_keys = [], [], []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    if invalidation == "no_checkpoint":
        runtime._context_tokens.clear()

    def native_arrival():
        target = req("parent-next")
        target.beliefkv_metadata.update(
            invocation_id="parent", context_id="ctx-parent", context_epoch=1,
        )
        target.session_id, target.session_generation = origin.session_id, origin.session_generation
        runtime.register_visible_request(target)

    def drain(**kwargs):
        if not bound_keys:
            runtime.on_events((client_submit(),))
            ticket = runtime._join_ticket
            bound_keys.append(ticket.key if ticket is not None else None)
            if invalidation == "native_arrival":
                native_arrival()
            elif invalidation == "generation" and ticket is not None:
                ticket.key = replace(ticket.key, session_generation=origin.session_generation + 1)
            elif invalidation == "advance":
                runtime.on_events((event(
                    23, RuntimeEventKind.CONTEXT_ADVANCE,
                    invocation_id="parent", context_id="ctx-parent", context_epoch=2,
                ),))
            elif invalidation == "cancel":
                runtime.on_events((event(
                    23, RuntimeEventKind.INVOCATION_CANCEL, invocation_id="parent",
                ),))
            elif invalidation == "disabled":
                runtime.physical_disabled = True
        elif invalidation == "second_drain_native_arrival":
            native_arrival()
        return (NS(),)

    def inspect(**kwargs):
        requested_epochs.append(kwargs["context_epoch"])
        if kwargs["context_epoch"] == origin.context_epoch:
            return opportunity
        key = runtime._join_ticket.key
        assert key == bound_keys[0]
        assert key.context_epoch == origin.context_epoch + 1
        assert key.request_id == "parent-next"
        return rekey_opportunity(opportunity, key)

    runtime.event_server = NS(drain=Mock(side_effect=drain), close=lambda: None)
    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity", side_effect=inspect):
            runtime.dispatch_join_prefetch()
        success = invalidation is None
        retried = invalidation in (None, "second_drain_native_arrival")
        assert runtime.counts["join_prefetch_client_handoff_replanned"] == int(retried)
        assert runtime.event_server.drain.call_count == 1 + int(retried)
        assert requested_epochs[0] == 0
        if retried:
            assert 1 in requested_epochs
        if success:
            assert cache.prefetch_gpu_session_nodes.call_count == 1
            assert runtime.physical_ledger.pending_count == 4
            assert runtime.counts["join_prefetch_confirmed_issued"] == 4
            expectations = [
                runtime.physical_ledger.pending_expectation(command)
                for command in runtime._prefetch_steps
            ]
            assert all(
                value.context_epoch == 1
                and value.session_id == origin.session_id
                and value.session_generation == origin.session_generation
                for value in expectations
            )
            assert all(
                step.key == bound_keys[0]
                for step, _, _ in runtime._prefetch_steps.values()
            )
            issued = [row for row in records if row["event"] == "prefetch_native_issued"]
            assert issued and all(
                row["confirmed_parent_request_id"] == "parent-next"
                and row["pre_boundary_prediction"] is False
                for row in issued
            )
        else:
            cache.prefetch_gpu_session_nodes.assert_not_called()
            assert not runtime.physical_ledger.pending_count
        assert not runtime.completed_physical_actions
    finally:
        runtime.close()
