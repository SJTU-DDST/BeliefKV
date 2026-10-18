from dataclasses import replace
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.core.events import RuntimeEventKind
from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from beliefkv.runtime.sglang_v0520_physical import plan_prefetch_gpu_steps
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


def add_second_join(runtime, *, continuing=None):
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
              member_invocation_ids=("child2",),
              attributes=(
                  {"parent_prefix_continuation": continuing} if continuing is not None else {}
              )),
        event(13, RuntimeEventKind.JOIN_WAIT, invocation_id="parent2", join_id="join2"),
    ))
    return runtime.context_sessions["ctx-parent2"]


def parallel_confirmed_join_fixture(*, parallel=True, overlap=True, contexts=2):
    runtime, first_key, first, cache, _ = normal_join_fixture()
    second_key = add_second_join(runtime)
    runtime._parallel_wait_prefetch = parallel
    runtime._wait_prefetch_contexts = contexts
    runtime._residency_budget = PrefetchResidencyBudget(2, 360)
    cache.supports_beliefkv_overlap_prefetch = lambda: overlap
    anchors = replace(
        first.anchors, key=second_key,
        component_leaves=tuple(
            (component, tuple((node + 20, created) for node, created in leaves))
            for component, leaves in first.anchors.component_leaves
        ),
    )
    candidate = replace(
        first.candidate, anchors=anchors, nodes=tuple(
            NS(**{
                **vars(node), "node_id": node.node_id + 20,
                "parent_id": node.parent_id + 20 if node.parent_id is not None else None,
            }) for node in first.candidate.nodes
        ),
    )
    steps = plan_prefetch_gpu_steps(candidate)
    second = replace(first, anchors=anchors, candidate=candidate, step=steps[0], steps=steps)
    plans = {first_key.context_id: first, second_key.context_id: second}
    issued = {}

    def native_burst(**kwargs):
        outcomes = []
        for node, created, state, command in kwargs["nodes"]:
            operation = NS(
                beliefkv_command_id=command, node_ids=[node],
                host_indices=(1, 2, 3, 4), device_indices=(5, 6, 7, 8),
                pool_transfers=[NS(
                    name="mamba", indices_from_pool=None,
                    host_indices=(10,), device_indices=(11,),
                )] if state else None,
            )
            if not kwargs["beliefkv_before_enqueue"](operation):
                break
            issued[command] = node, state
            outcomes.append(NS(issued=True, node_id=node, load_started=True))
        return tuple(outcomes)

    cache.prefetch_gpu_session_nodes = Mock(side_effect=native_burst)

    def acknowledge(commands):
        for command in commands:
            node, state = issued[command]
            units = (("kv", 4),) + ((("mamba", 1),) if state else ())
            runtime.on_native_transfer_commit(NS(
                direction="h2d", status="completed", node_ids=(node,),
                num_tokens_by_pool=units,
                child_commits=(NS(
                    command_id=command, anchor_node_id=node, published_node_ids=(node,),
                    num_tokens_by_pool=units, num_bytes=40 + 20 * state,
                ),),
            ))

    complete_join(runtime)
    complete_join(runtime, sequence=22, child="child2", join="join2")
    return runtime, first_key, second_key, plans, cache, acknowledge


@pytest.mark.parametrize("parallel,overlap,contexts,expected_contexts", (
    (False, True, 2, 1), (True, False, 2, 1),
    (True, True, 1, 1), (True, True, 2, 2),
))
def test_confirmed_joins_fill_legal_context_slots_before_the_first_ack(
    parallel, overlap, contexts, expected_contexts,
):
    runtime, first, second, plans, cache, _ = parallel_confirmed_join_fixture(
        parallel=parallel, overlap=overlap, contexts=contexts,
    )
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ):
            for _ in range(3):
                runtime.dispatch_join_prefetch()
        assert cache.prefetch_gpu_session_nodes.call_count == expected_contexts
        assert set(runtime.physical_ledger.pending_h2d_contexts) == {
            (key.context_id, key.session_id, key.session_generation)
            for key in (first, second)[:expected_contexts]
        }
        assert runtime.physical_ledger.pending_count == 4 * expected_contexts
        assert runtime.counts["join_prefetch_confirmed_issued"] == 4 * expected_contexts
        assert runtime.counts["join_prefetch_acked"] == 0
        assert not runtime.completed_physical_actions
        assert not runtime.physical_disabled
    finally:
        runtime.close()


@pytest.mark.parametrize("request_slots,byte_limit,expected_nodes", (
    (0, 360, 0), (1, 360, 4), (2, 180, 4), (2, 360, 8),
))
def test_parallel_confirmed_joins_share_the_actual_slot_and_inflight_byte_budget(
    request_slots, byte_limit, expected_nodes,
):
    runtime, _, _, plans, _, _ = parallel_confirmed_join_fixture()
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
        assert not runtime.physical_disabled
    finally:
        runtime.close()


@pytest.mark.parametrize("partial_first_ack", (False, True))
def test_parallel_confirmed_joins_reconcile_out_of_order_acks_independently(partial_first_ack):
    runtime, first, second, plans, _, acknowledge = parallel_confirmed_join_fixture()
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ):
            runtime.dispatch_join_prefetch()
            runtime.dispatch_join_prefetch()
        tickets = dict(runtime._confirmed_join_tickets)
        first_commands = tickets["join"].command_ids
        second_commands = tickets["join2"].command_ids
        assert len(first_commands) == len(second_commands) == 4
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=None):
            if partial_first_ack:
                acknowledge(first_commands[:2])
                runtime.dispatch_join_prefetch()
                assert runtime.counts["join_prefetch_acked"] == 0
                assert runtime.physical_ledger.pending_count == 6
            acknowledge(second_commands)
            runtime.dispatch_join_prefetch()
            assert runtime.counts["join_prefetch_acked"] == 4
            assert runtime.physical_ledger.pending_count == (2 if partial_first_ack else 4)
            assert tickets["join"].key == first
            assert tickets["join"].command_ids == first_commands
            assert "join" in runtime._confirmed_join_tickets
            assert "join2" not in runtime._confirmed_join_tickets
            acknowledge(first_commands[2:] if partial_first_ack else first_commands)
            runtime.dispatch_join_prefetch()
        assert runtime.counts["join_prefetch_acked"] == 8
        assert runtime.counts["join_prefetch_lost_ack"] == 0
        assert not runtime._confirmed_join_tickets
        assert not runtime.physical_ledger.pending_count
        assert not runtime.physical_disabled
    finally:
        runtime.close()


def test_expired_parallel_join_bursts_never_receive_ack_credit():
    runtime, _, _, plans, _, _ = parallel_confirmed_join_fixture()
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ):
            runtime.dispatch_join_prefetch()
            runtime.dispatch_join_prefetch()
        runtime.physical_ledger.max_age_s = 1e-9
        assert len(runtime.physical_ledger.expire()) == 8
        runtime.dispatch_join_prefetch()
        assert runtime.counts["join_prefetch_acked"] == 0
        assert runtime.counts["join_prefetch_lost_ack"] == 2
        assert not runtime._confirmed_join_tickets
        assert not runtime.physical_ledger.pending_count
        assert not runtime.completed_physical_actions
        assert not runtime.physical_disabled
    finally:
        runtime.close()


def test_one_canceled_parallel_join_keeps_other_ack_and_pending_ownership_separate():
    runtime, _, _, plans, _, acknowledge = parallel_confirmed_join_fixture()
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ):
            runtime.dispatch_join_prefetch()
            runtime.dispatch_join_prefetch()
        first = runtime._confirmed_join_tickets["join"]
        second = runtime._confirmed_join_tickets["join2"]
        runtime.on_events((event(
            24, RuntimeEventKind.INVOCATION_CANCEL, invocation_id="parent",
        ),))
        assert "join" not in runtime._confirmed_join_tickets
        assert all(runtime.physical_ledger.is_pending(command) for command in first.command_ids)
        assert runtime._confirmed_join_tickets["join2"] is second
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=None):
            acknowledge(second.command_ids)
            runtime.dispatch_join_prefetch()
        assert runtime.counts["join_prefetch_acked"] == 4
        assert runtime.physical_ledger.pending_count == 4
        assert not runtime._confirmed_join_tickets
        assert not runtime.physical_disabled
    finally:
        runtime.close()


def test_saturated_join_frontier_does_not_scan_confirmed_waiters():
    runtime, _, _, plans, _, _ = parallel_confirmed_join_fixture()
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ):
            runtime.dispatch_join_prefetch()
            runtime.dispatch_join_prefetch()

        class NoWaiterScan(dict):
            def values(self):
                raise AssertionError("a saturated restore frontier must not scan waiters")

        runtime._confirmed_join_tickets = NoWaiterScan(runtime._confirmed_join_tickets)
        runtime.dispatch_join_prefetch()
        assert runtime.physical_ledger.pending_count == 8
        assert runtime.counts["join_prefetch_confirmed_issued"] == 8
        assert runtime.counts["join_prefetch_acked"] == 0
        assert not runtime.physical_disabled
    finally:
        runtime.close()


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


def client_submit(*, sequence=22, **changes):
    values = {
        "invocation_id": "parent", "context_id": "ctx-parent", "context_epoch": 1,
        "attributes": {"request_id": "parent-next"}, **changes,
    }
    return event(sequence, RuntimeEventKind.LLM_SUBMIT, **values)


def continuing_join_fixture():
    runtime, key, opportunity, cache, reserved = normal_join_fixture()
    runtime._continuing_joins.add("join")
    runtime._context_tokens["ctx-parent"] = (0, 17, 0, False)
    return runtime, key, opportunity, cache, reserved


@pytest.mark.parametrize("coalesced", (False, True))
def test_explicit_continuing_join_restores_for_bound_client_before_native_arrival(coalesced):
    runtime, key, opportunity, cache, _ = continuing_join_fixture()
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        if coalesced:
            runtime.on_events((
                event(20, RuntimeEventKind.RETURN, invocation_id="child"),
                event(21, RuntimeEventKind.JOIN_SATISFIED, join_id="join"),
                client_submit(),
            ))
        else:
            complete_join(runtime)
            runtime.on_events((client_submit(),))
        ticket = runtime._join_ticket
        assert ticket is not None
        assert ticket.client_request_id == "parent-next"
        assert runtime.context_sessions.get("ctx-parent") is None
        assert "ctx-parent" not in runtime._context_tokens
        assert ticket.checkpoint_key == key
        assert ticket.checkpoint_input_tokens == 16
        assert ticket.key.context_epoch == 1
        assert ticket.key.request_id == "parent-next"
        assert key.context_epoch == 0
        assert runtime._terminal(key)
        assert not runtime._terminal(ticket.key)
        assert "parent-next" not in runtime.visible
        assert runtime.graph.contexts["ctx-parent"].epoch == 1
        assert runtime._live_join_ticket()
        steps = tuple(replace(step, key=ticket.key) for step in opportunity.steps)
        anchors = replace(opportunity.anchors, key=ticket.key)
        opportunity = replace(
            opportunity, anchors=anchors,
            candidate=replace(opportunity.candidate, anchors=anchors),
            step=steps[0], steps=steps,
        )
        with patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=opportunity,
        ):
            runtime.dispatch_join_prefetch()
        assert cache.prefetch_gpu_session_nodes.call_count == 1
        assert runtime.physical_ledger.pending_count == 4
        assert all(
            runtime.physical_ledger.pending_expectation(command).context_epoch == 1
            for command in ticket.command_ids
        )
        issued = [row for row in records if row["event"] == "prefetch_native_issued"]
        assert len(issued) == 4
        assert all(row["confirmed_parent_request_id"] == "parent-next" for row in issued)
        assert all(
            row["timing_policy"] == "confirmed_join_client_bound_before_native_visibility"
            for row in issued
        )
        assert all(row["pre_boundary_prediction"] is False for row in issued)
    finally:
        runtime.close()


@pytest.mark.parametrize("invalidate", (
    "native_arrival", "session", "generation", "tokens_missing", "tokens_epoch",
    "next_request", "compact", "compact_without_context", "advance", "cancel",
    "workflow_end", "expiry", "prefix_unknown",
))
def test_bound_client_checkpoint_revalidates_identity_and_lifecycle_at_enqueue(invalidate):
    runtime, key, opportunity, cache, _ = continuing_join_fixture()
    try:
        complete_join(runtime)
        runtime.on_events((client_submit(),))
        ticket = runtime._join_ticket
        assert runtime._live_join_ticket()
        expires = ticket.expires_at
        if invalidate == "native_arrival":
            target = req("parent-next")
            target.beliefkv_metadata.update(
                invocation_id="parent", context_id="ctx-parent", context_epoch=1,
            )
            target.session_id, target.session_generation = key.session_id, key.session_generation
            runtime.register_visible_request(target)
        elif invalidate in ("session", "generation"):
            runtime.context_sessions["ctx-parent"] = replace(
                key, **(
                    {"session_id": "changed"} if invalidate == "session"
                    else {"session_generation": key.session_generation + 1}
                ),
            )
        elif invalidate.startswith("tokens_"):
            if invalidate == "tokens_missing":
                ticket.checkpoint_input_tokens = None
            else:
                ticket.checkpoint_key = replace(key, context_epoch=1)
        elif invalidate == "next_request":
            runtime.on_events((client_submit(
                sequence=23, context_epoch=2, attributes={"request_id": "different"},
            ),))
        elif invalidate.startswith("compact") or invalidate == "advance":
            runtime.on_events((event(
                23,
                RuntimeEventKind.CONTEXT_ADVANCE if invalidate == "advance"
                else RuntimeEventKind.CONTEXT_COMPACT,
                invocation_id="parent",
                context_id=None if invalidate == "compact_without_context" else "ctx-parent",
                context_epoch=2,
            ),))
        elif invalidate == "cancel":
            runtime.on_events((event(
                23, RuntimeEventKind.INVOCATION_CANCEL, invocation_id="parent",
            ),))
        elif invalidate == "workflow_end":
            runtime.on_events((event(23, RuntimeEventKind.WORKFLOW_END),))
        elif invalidate == "expiry":
            ticket.expires_at = time.monotonic() - 1
        elif invalidate == "prefix_unknown":
            runtime._continuing_joins.clear()
        with patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=opportunity,
        ):
            runtime.dispatch_join_prefetch()
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime.physical_ledger.pending_count == 0
        assert expires > 0
    finally:
        runtime.close()


@pytest.mark.parametrize("change", (
    {"attributes": {"request_id": "parent-next", "runtime_internal": True}},
    {"attributes": {}}, {"attributes": {"request_id": ""}},
    {"context_epoch": 2},
))
def test_unproven_client_request_cannot_extend_confirmed_checkpoint(change):
    runtime, _, opportunity, cache, _ = continuing_join_fixture()
    try:
        complete_join(runtime)
        runtime.on_events((client_submit(**change),))
        with patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=opportunity,
        ):
            runtime.dispatch_join_prefetch()
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


def test_compaction_between_coalesced_return_and_submit_cannot_bind_old_checkpoint():
    runtime, _, opportunity, cache, _ = continuing_join_fixture()
    try:
        runtime.on_events((
            event(20, RuntimeEventKind.RETURN, invocation_id="child"),
            event(21, RuntimeEventKind.JOIN_SATISFIED, join_id="join"),
            event(22, RuntimeEventKind.CONTEXT_COMPACT, invocation_id="parent",
                  context_id="ctx-parent", context_epoch=1),
            client_submit(sequence=23),
        ))
        with patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=opportunity,
        ):
            runtime.dispatch_join_prefetch()
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime._join_ticket is None
    finally:
        runtime.close()


def test_bound_client_replay_does_not_refresh_confirmed_deadline():
    runtime, _, _, _, _ = continuing_join_fixture()
    try:
        complete_join(runtime)
        runtime.on_events((client_submit(),))
        ticket = runtime._join_ticket
        expires = ticket.expires_at
        runtime.on_events((client_submit(),))
        assert runtime._join_ticket is ticket
        assert ticket.expires_at == expires
        assert runtime.counts["confirmed_join_client_bound"] == 1
    finally:
        runtime.close()


def test_client_bound_snapshot_uses_original_native_session_and_input_limit():
    runtime, key, _, cache, _ = continuing_join_fixture()
    snapshots = []

    def snapshot(session, generation, **kwargs):
        snapshots.append((session, generation, kwargs))
        return ((0, ((4, 5),)), (2, ((4, 5),)))

    cache.session_refs = NS(
        snapshot_latest_session_leaf_anchors=snapshot,
        snapshot_session_leaf_anchors=snapshot,
    )
    try:
        complete_join(runtime)
        runtime.on_events((client_submit(),))
        ticket = runtime._join_ticket
        anchors = runtime.snapshot_session_anchors(
            cache, context_id="ctx-parent", context_epoch=1,
        )
        assert anchors is not None
        assert anchors.key == ticket.key
        assert anchors.reusable_input_tokens == 16
        assert snapshots == [(key.session_id, key.session_generation, {"max_leaves": 8})]
        assert runtime.snapshot_session_anchors(
            cache, context_id="ctx-parent", context_epoch=0,
        ) is None
    finally:
        runtime.close()


@pytest.mark.parametrize("native_arrival", (False, True))
def test_client_bound_restore_ack_survives_before_or_after_native_registration(native_arrival):
    runtime, origin, opportunity, cache, _ = continuing_join_fixture()
    snapshots = []

    def snapshot(session, generation, **kwargs):
        snapshots.append((session, generation, kwargs))
        return opportunity.anchors.component_leaves

    cache.session_refs = NS(
        snapshot_latest_session_leaf_anchors=snapshot,
        snapshot_session_leaf_anchors=snapshot,
    )
    try:
        complete_join(runtime)
        runtime.on_events((client_submit(),))
        ticket = runtime._join_ticket
        steps = tuple(replace(step, key=ticket.key) for step in opportunity.steps)
        anchors = replace(opportunity.anchors, key=ticket.key)
        opportunity = replace(
            opportunity, anchors=anchors,
            candidate=replace(opportunity.candidate, anchors=anchors),
            step=steps[0], steps=steps,
        )
        with patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=opportunity,
        ):
            runtime.dispatch_join_prefetch()
        commands = ticket.command_ids
        assert len(commands) == 4
        assert runtime.context_sessions.get("ctx-parent") is None
        if native_arrival:
            target = req("parent-next")
            target.beliefkv_metadata.update(
                invocation_id="parent", context_id="ctx-parent", context_epoch=1,
            )
            target.session_id, target.session_generation = (
                origin.session_id, origin.session_generation,
            )
            assert runtime.register_visible_request(target)
        for node, command in enumerate(commands, 1):
            state = node == 4
            pools = (("kv", 4),) + ((("mamba", 1),) if state else ())
            completed = runtime.on_native_transfer_commit(NS(
                direction="h2d", status="completed", node_ids=(node,),
                num_tokens_by_pool=pools,
                child_commits=(NS(
                    command_id=command, anchor_node_id=node, published_node_ids=(node,),
                    num_tokens_by_pool=pools, num_bytes=40 + 20 * state,
                ),),
            ))
            assert len(completed) == 1
            assert completed[0].context_epoch == 1
            assert completed[0].source == "join_ticket"
            assert not runtime.physical_disabled
        assert runtime.physical_ledger.pending_count == 0
        assert runtime.counts["native_physical_completed"] == 4
        assert bool(snapshots) != native_arrival
        assert all(
            (session, generation) == (origin.session_id, origin.session_generation)
            for session, generation, _ in snapshots
        )
    finally:
        runtime.close()


@pytest.mark.parametrize("invalid", (
    "missing_ticket", "changed_session", "changed_generation", "missing_snapshot",
    "compact", "cancel", "damaged_receipt",
))
def test_bound_ack_does_not_accept_unproven_session_or_damaged_native_receipt(invalid):
    runtime, _, opportunity, cache, _ = continuing_join_fixture()
    cache.session_refs = NS(
        snapshot_latest_session_leaf_anchors=lambda *_args, **_kwargs: (
            None if invalid == "missing_snapshot" else opportunity.anchors.component_leaves
        ),
        snapshot_session_leaf_anchors=lambda *_args, **_kwargs: None,
    )
    try:
        complete_join(runtime)
        runtime.on_events((client_submit(),))
        ticket = runtime._join_ticket
        key = ticket.key
        from beliefkv.runtime.sglang_v0520_physical import (
            PhysicalActionExpectation, PhysicalChildExpectation,
        )
        runtime.physical_ledger.register(PhysicalActionExpectation(
            "ack-proof", "PREFETCH_GPU", key.context_id, key.context_epoch,
            (PhysicalChildExpectation(1, (1,), (("kv", 40),), 40),),
            (("kv", 10),), session_id=key.session_id,
            session_generation=key.session_generation,
        ))
        if invalid == "missing_ticket":
            runtime._discard_join_ticket("test_invalidated", ticket=ticket)
        elif invalid in ("changed_session", "changed_generation"):
            ticket.key = replace(
                key, **(
                    {"session_id": "different"} if invalid == "changed_session"
                    else {"session_generation": key.session_generation + 1}
                ),
            )
        elif invalid == "compact":
            runtime.on_events((event(
                23, RuntimeEventKind.CONTEXT_COMPACT, invocation_id="parent",
                context_id="ctx-parent", context_epoch=2,
            ),))
        elif invalid == "cancel":
            runtime.on_events((event(
                23, RuntimeEventKind.INVOCATION_CANCEL, invocation_id="parent",
            ),))
        completed = runtime.on_native_transfer_commit(NS(
            direction="h2d", status="completed", node_ids=(1,),
            num_tokens_by_pool=(("kv", 4),),
            child_commits=(NS(
                command_id="ack-proof", anchor_node_id=1, published_node_ids=(1,),
                num_tokens_by_pool=(("kv", 4),),
                num_bytes=41 if invalid == "damaged_receipt" else 40,
            ),),
        ))
        assert completed == ()
        assert runtime.physical_disabled
        assert runtime.counts["physical_receipt_failed"] == 1
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


def test_client_bound_live_planner_excludes_prior_decode_output_beyond_safe_input():
    from tests.test_sglang_v0520_physical import prepare_prefix_fixture

    runtime, _, _, _, _ = continuing_join_fixture()
    cache, original_anchors, nodes = prepare_prefix_fixture(backed=True)
    for node in nodes.values():
        for data in node.component_data.values():
            data.host_value = data.value
            data.value = None
    snapshot = lambda *_args, **_kwargs: original_anchors.component_leaves
    cache.session_refs = NS(
        snapshot_latest_session_leaf_anchors=snapshot,
        snapshot_session_leaf_anchors=snapshot,
    )
    runtime.attach_native_cache(cache)
    runtime._context_tokens["ctx-parent"] = (0, 5, 0, False)
    try:
        complete_join(runtime)
        runtime.on_events((client_submit(),))
        ticket = runtime._join_ticket
        result = runtime.inspect_context_h2d_opportunity(
            context_id="ctx-parent", context_epoch=1, max_steps=8,
        )
        assert result is not None
        assert result.anchors.key == ticket.key
        assert result.anchors.reusable_input_tokens == 4
        assert result.required_full_tokens == 4
        assert [step.node_id for step in result.steps] == [1, 2]
        assert 3 not in [step.node_id for step in result.steps]
    finally:
        runtime.close()


@pytest.mark.parametrize("proof", ("matching", "missing_generations", "new_generation", "closed"))
def test_client_bound_residency_retains_only_the_original_native_generation(proof):
    from beliefkv.runtime.sglang_v0520_runtime import _PrefetchServiceLease

    runtime, origin, _, cache, _ = continuing_join_fixture()
    try:
        complete_join(runtime)
        runtime.on_events((client_submit(),))
        ticket = runtime._join_ticket
        sessions = NS(
            _session_generations={origin.session_id: origin.session_generation},
            _closed_session_ids=set(),
        )
        if proof == "missing_generations":
            del sessions._session_generations
        elif proof == "new_generation":
            sessions._session_generations[origin.session_id] += 1
        elif proof == "closed":
            sessions._closed_session_ids.add(origin.session_id)
        cache.session_refs = sessions
        cache.tree_core = NS(node_by_id=lambda _: NS(
            creation_time=5, component_data={0: NS(value=range(16))},
        ))
        now = time.monotonic()
        lease = _PrefetchServiceLease(
            ticket.key, "h2d", 4, 5, "join_ticket", None, now, now + 2, (("kv", 160),),
        )
        reason = runtime._prefetch_lease_invalid_reason(lease)
        assert reason == {
            "matching": None,
            "missing_generations": "session_handoff_unproven",
            "new_generation": "session_handoff_unproven",
            "closed": "session_closed",
        }[proof]
    finally:
        runtime.close()


def test_reactive_coalesced_client_does_not_create_a_bound_restore_ticket():
    runtime, _, opportunity, cache, _ = continuing_join_fixture()
    runtime.enable_final_stage_prefetch = False
    try:
        runtime.on_events((
            event(20, RuntimeEventKind.RETURN, invocation_id="child"),
            event(21, RuntimeEventKind.JOIN_SATISFIED, join_id="join"),
            client_submit(),
        ))
        with patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=opportunity,
        ):
            runtime.dispatch_join_prefetch()
        assert runtime._join_ticket is None
        assert not runtime._confirmed_join_tickets
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime.counts["confirmed_join_client_bound"] == 0
    finally:
        runtime.close()


def test_completed_bound_restore_keeps_consumer_identity_and_yields_to_another_join():
    runtime, _, _, cache, _ = continuing_join_fixture()
    try:
        second_origin = add_second_join(runtime, continuing=True)
        assert "join2" in runtime._continuing_joins
        runtime._context_tokens["ctx-parent2"] = (0, 17, 0, False)
        complete_join(runtime)
        runtime.on_events((client_submit(),))
        first = runtime._join_ticket
        complete_join(runtime, sequence=24, child="child2", join="join2")
        runtime.on_events((client_submit(
            sequence=26, invocation_id="parent2", context_id="ctx-parent2",
            attributes={"request_id": "parent2-next"},
        ),))
        second = runtime._confirmed_join_tickets["join2"]
        assert second.checkpoint_key == second_origin
        first.issued_nodes = 1
        with patch.object(
            runtime, "_wait_prefetch_opportunity",
            return_value=NS(step=None, no_step_reason="no_host_backed_missing_copy"),
        ):
            runtime.dispatch_join_prefetch()
        assert first.restoration_complete
        assert runtime._confirmed_join_tickets["join"] is first
        assert runtime._pending_confirmed_join_submit(first.key)
        runtime._select_confirmed_join_ticket()
        assert runtime._join_ticket is second
        assert runtime._pending_confirmed_join_submit(first.key)
        cache.prefetch_gpu_session_nodes.assert_not_called()
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
