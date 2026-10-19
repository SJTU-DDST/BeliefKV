from types import SimpleNamespace as NS
import time
from unittest.mock import patch

import pytest

from beliefkv.core.events import RuntimeEventKind
from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from beliefkv.runtime.semantic_report_worker import SemanticReportInput, SemanticReportReply
from tests.test_join_confirmed_reentry import client_submit, complete_join, parallel_join_fixture
from tests.test_sglang_v0520_runtime import event


def provisional_fixture(*, signal="eos", **kwargs):
    runtime, first, second, plans, cache, acknowledge = parallel_join_fixture(
        provisional=True, **kwargs,
    )
    if signal == "work":
        now_ms = time.monotonic() * 1000.
        runtime.semantic_work_statistic = "center"
        runtime._native_running_batch = NS(reqs=[NS(rid=rid) for rid in ("child", "child2")])
        for rid, join_id in (("child", "join"), ("child2", "join2")):
            key = runtime.visible[rid]
            runtime._ensure_observed_final_stage(key)
            stage = runtime._final_stages[join_id]
            stage.generated_tokens, stage.tokens_per_second = 120, 40.
            runtime._semantic_forecasts[rid] = SemanticReportReply(
                SemanticReportInput(
                    key, now_ms, 120, 256, "Evidence complete.", True, 500, 1, 2,
                ), .99, 4., 16., 80., 5.,
            )
        runtime._semantic_eos_proofs.clear()
        runtime._semantic_finished.clear()
        runtime._eos_prefetch_keys.clear()
    return runtime, first, second, plans, cache, acknowledge


@pytest.mark.parametrize("signal", ("eos", "work"))
@pytest.mark.parametrize("parallel,overlap,contexts,expected_contexts", (
    (False, True, 2, 1), (True, False, 2, 1),
    (True, True, 1, 1), (True, True, 2, 2),
))
def test_independent_provisional_joins_fill_legal_slots_before_return_or_ack(
    signal, parallel, overlap, contexts, expected_contexts,
):
    runtime, first, second, plans, cache, _ = provisional_fixture(
        signal=signal, parallel=parallel, overlap=overlap, contexts=contexts,
    )
    if signal == "work":
        expected_contexts = 0
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ):
            for _ in range(3):
                runtime.dispatch_join_prefetch()
        assert cache.prefetch_gpu_session_nodes.call_count == expected_contexts
        assert runtime.physical_ledger.pending_count == 4 * expected_contexts
        assert set(runtime.physical_ledger.pending_h2d_contexts) == {
            (key.context_id, key.session_id, key.session_generation)
            for key in (first, second)[:expected_contexts]
        }
        assert runtime.counts["join_prefetch_provisional_issued"] == 4 * expected_contexts
        assert runtime.counts["join_prefetch_confirmed_issued"] == 0
        assert runtime.counts["join_prefetch_acked"] == 0
        assert not runtime.completed_physical_actions
        assert not runtime._confirmed_join_tickets
        assert not any(join.satisfied for join in runtime.graph.joins.values())
        assert not runtime.physical_disabled
    finally:
        runtime.close()


@pytest.mark.parametrize("partial_ack", (False, True))
def test_provisional_joins_keep_independent_partial_and_out_of_order_ack_ownership(partial_ack):
    runtime, first, second, plans, _, acknowledge = provisional_fixture()
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ):
            runtime.dispatch_join_prefetch()
            runtime.dispatch_join_prefetch()
        first_ticket = runtime._join_inflight_tickets["join"]
        second_ticket = runtime._join_ticket
        assert second_ticket.key == second
        first_commands, second_commands = first_ticket.command_ids, second_ticket.command_ids
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=None):
            if partial_ack:
                acknowledge(first_commands[:2])
                runtime.dispatch_join_prefetch()
                assert runtime.counts["join_prefetch_acked"] == 0
                assert runtime.physical_ledger.pending_count == 6
            acknowledge(second_commands)
            runtime.dispatch_join_prefetch()
            assert runtime.counts["join_prefetch_acked"] == 4
            assert first_ticket.key == first
            assert first_ticket.command_ids == first_commands
            assert runtime.physical_ledger.pending_count == (2 if partial_ack else 4)
            acknowledge(first_commands[2:] if partial_ack else first_commands)
            runtime.dispatch_join_prefetch()
        assert runtime.counts["join_prefetch_acked"] == 8
        assert runtime.counts["join_prefetch_lost_ack"] == 0
        assert not runtime._join_inflight_tickets
        assert not runtime.physical_ledger.pending_count
        assert not runtime.physical_disabled
    finally:
        runtime.close()


def test_return_promotes_the_same_parked_provisional_burst_and_preserves_ack_identity():
    runtime, first, _, plans, _, acknowledge = provisional_fixture()
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ):
            runtime.dispatch_join_prefetch()
            runtime.dispatch_join_prefetch()
        ticket = runtime._join_inflight_tickets["join"]
        commands = ticket.command_ids
        assert len(commands) == 4
        complete_join(runtime)
        assert "join" not in runtime._join_inflight_tickets
        assert runtime._confirmed_join_tickets["join"] is ticket
        assert ticket.phase == "confirmed" and ticket.key == first
        assert ticket.command_ids == commands
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=None):
            acknowledge(commands)
            runtime.dispatch_join_prefetch()
        assert runtime.counts["join_prefetch_acked"] == 4
        assert runtime.counts["join_prefetch_confirmed_issued"] == 0
        assert runtime.physical_ledger.pending_count == 4
        assert not runtime.physical_disabled
    finally:
        runtime.close()


@pytest.mark.parametrize("request_slots,byte_limit,expected_nodes", (
    (0, 360, 0), (1, 360, 4), (2, 180, 4), (2, 360, 8),
))
def test_provisional_joins_share_global_slot_and_inflight_byte_limits(
    request_slots, byte_limit, expected_nodes,
):
    runtime, _, _, plans, _, _ = provisional_fixture()
    runtime._residency_budget = PrefetchResidencyBudget(request_slots, byte_limit)
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ):
            for _ in range(3):
                runtime.dispatch_join_prefetch()
        assert runtime.physical_ledger.pending_count == expected_nodes
        assert runtime.physical_ledger.pending_action_bytes("PREFETCH_GPU") == (
            180 * (expected_nodes // 4)
        )
        assert not any(join.satisfied for join in runtime.graph.joins.values())
        assert not runtime.physical_disabled
    finally:
        runtime.close()


def test_coalesced_return_and_client_submit_preserve_the_parked_transfer_receipts():
    runtime, first, _, plans, cache, acknowledge = provisional_fixture()
    runtime._continuing_joins.add("join")
    runtime._context_tokens[first.context_id] = (0, 17, 0, False)
    anchors = plans[first.context_id].anchors.component_leaves
    snapshot = lambda *_args, **_kwargs: anchors
    cache.session_refs = NS(
        snapshot_latest_session_leaf_anchors=snapshot,
        snapshot_session_leaf_anchors=snapshot,
        _session_generations={first.session_id: first.session_generation},
        _closed_session_ids=set(),
    )
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ):
            runtime.dispatch_join_prefetch()
            runtime.dispatch_join_prefetch()
        ticket = runtime._join_inflight_tickets["join"]
        commands = ticket.command_ids
        runtime.on_events((
            event(20, RuntimeEventKind.RETURN, invocation_id="child"),
            event(21, RuntimeEventKind.JOIN_SATISFIED, join_id="join"),
            client_submit(),
        ))
        assert "join" not in runtime._join_inflight_tickets
        assert runtime._confirmed_join_tickets["join"] is ticket
        assert ticket.phase == "confirmed"
        assert ticket.client_request_id == "parent-next"
        assert ticket.checkpoint_key == first
        assert ticket.command_ids == commands
        assert ticket.key.context_epoch == 1
        assert all(
            runtime.physical_ledger.pending_expectation(command).context_epoch == 0
            for command in commands
        )
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=None):
            acknowledge(commands)
            runtime.dispatch_join_prefetch()
        assert runtime.counts["join_prefetch_acked"] == 4
        assert runtime.counts["native_physical_completed"] == 4
        assert ticket.restoration_complete
        assert runtime._pending_confirmed_join_submit(ticket.key)
        assert not runtime.physical_disabled
    finally:
        runtime.close()


@pytest.mark.parametrize("invalidate", ("parent_cancel", "child_cancel", "compact", "timeout"))
def test_parked_provisional_identity_invalidation_keeps_native_ownership_until_receipt(invalidate):
    runtime, _, _, plans, _, acknowledge = provisional_fixture()
    changes = {
        "parent_cancel": event(20, RuntimeEventKind.INVOCATION_CANCEL, invocation_id="parent"),
        "child_cancel": event(20, RuntimeEventKind.INVOCATION_CANCEL, invocation_id="child"),
        "compact": event(20, RuntimeEventKind.CONTEXT_COMPACT, invocation_id="parent",
                         context_id="ctx-parent", context_epoch=1),
        "timeout": event(20, RuntimeEventKind.JOIN_TIMEOUT, join_id="join"),
    }
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ):
            runtime.dispatch_join_prefetch()
            runtime.dispatch_join_prefetch()
        first = runtime._join_inflight_tickets["join"]
        second_commands = runtime._join_ticket.command_ids
        runtime.on_events((changes[invalidate],))
        assert "join" not in runtime._join_inflight_tickets
        assert all(runtime.physical_ledger.is_pending(command) for command in first.command_ids)
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=None):
            acknowledge(second_commands)
            runtime.dispatch_join_prefetch()
        assert runtime.counts["join_prefetch_acked"] == 4
        assert runtime.physical_ledger.pending_count == 4
        assert not runtime.physical_disabled
    finally:
        runtime.close()


def test_expired_provisional_commands_never_receive_ack_credit():
    runtime, _, _, plans, _, _ = provisional_fixture()
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ):
            runtime.dispatch_join_prefetch()
            runtime.dispatch_join_prefetch()
        runtime.physical_ledger.max_age_s = 1e-9
        assert len(runtime.physical_ledger.expire()) == 8
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=None):
            runtime.dispatch_join_prefetch()
        assert runtime.counts["join_prefetch_acked"] == 0
        assert runtime.counts["join_prefetch_lost_ack"] == 2
        assert not runtime._join_inflight_tickets
        assert not runtime.physical_ledger.pending_count
        assert not runtime.completed_physical_actions
        assert not runtime.physical_disabled
    finally:
        runtime.close()


def test_saturated_provisional_frontier_does_not_traverse_parked_tickets():
    runtime, _, _, plans, _, _ = provisional_fixture()
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ):
            runtime.dispatch_join_prefetch()
            runtime.dispatch_join_prefetch()

        class NoTicketScan(dict):
            def items(self):
                raise AssertionError("a saturated frontier must not traverse parked tickets")

        runtime._join_inflight_tickets = NoTicketScan(runtime._join_inflight_tickets)
        runtime.dispatch_join_prefetch()
        assert runtime.physical_ledger.pending_count == 8
        assert runtime.counts["join_prefetch_provisional_issued"] == 8
        assert not runtime.physical_disabled
    finally:
        runtime.close()


def test_legacy_probabilistic_ticket_keeps_its_existing_serial_dispatch():
    runtime, _, _, plans, _, _ = provisional_fixture()
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ):
            runtime.dispatch_join_prefetch()
            ticket = runtime._join_ticket
            ticket.phase = "probabilistic"
            runtime.dispatch_join_prefetch()
        assert runtime._join_ticket is ticket
        assert runtime.physical_ledger.pending_count == 4
        assert not runtime._join_inflight_tickets
        assert runtime.counts["join_prefetch_acked"] == 0
        assert not runtime.physical_disabled
    finally:
        runtime.close()
