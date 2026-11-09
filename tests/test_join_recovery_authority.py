"""JOIN grants and retry wakes use live authority, with CPU pool stand-ins."""

from dataclasses import asdict, replace
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime.reference_thunder_admission import ReferenceThunderAdmission
from tests.test_join_blocked_handoff import blocked
from tests.test_join_confirmed_reentry import (
    client_submit, complete_join, continuing_join_fixture,
)
from tests.test_join_enqueue_client_handoff import rekey_opportunity
from tests.test_reference_thunder_admission import state
from tests.test_sglang_v0520_runtime import req


@pytest.fixture
def confirmed_parent():
    runtime, origin, opportunity, cache, _ = continuing_join_fixture()
    cache.tree_components = (0,)
    complete_join(runtime)
    runtime.agent_join_free_admission = True
    runtime.agent_join_blocked_handoff = True
    runtime.enable_frontier_restore = False
    runtime.enable_execution_handoff = False
    admission = ReferenceThunderAdmission(runtime)
    admission.attach(NS(
        tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=1000),
    ))
    runtime._agent_admission = admission
    admission.latest[origin.invocation_id] = origin
    admission.workflows[origin.invocation_id] = origin.root_workflow_id
    now = time.monotonic()
    admission.policy.arrive(origin.invocation_id, 200, now=now)
    admission.policy.completed(origin.invocation_id, 200, now=now)
    admission.policy._pause(admission.policy.programs[origin.invocation_id], now)
    try:
        yield NS(
            runtime=runtime, admission=admission, origin=origin,
            opportunity=opportunity, cache=cache,
            owner=admission.policy.programs[origin.invocation_id],
            recovery=admission._join_handoff.recovery,
        )
    finally:
        runtime.close()


def bind_client(f):
    f.runtime.on_events((client_submit(),))
    ticket = f.runtime._join_ticket
    assert ticket is not None and f.runtime._live_join_ticket(ticket)
    return ticket.key


@pytest.mark.parametrize("client_bound", (False, True))
def test_confirmed_paused_parent_claims_full_budget_and_journals_actual_consumer(
    confirmed_parent, client_bound,
):
    f = confirmed_parent
    key = bind_client(f) if client_bound else f.origin
    rows = []
    with patch.object(f.runtime, "_opportunity_writer", NS(record=rows.append)):
        assert f.admission.claim_restore(key, source="join_ticket")
        assert f.admission.claim_restore(key, source="join_ticket")
    assert f.owner.active
    assert f.admission.policy.remaining_capacity() == 700
    assert f.admission.latest[key.invocation_id] == f.origin
    grants = [row for row in rows if row["event"] == "agent_join_free_capacity_admitted"]
    assert len(grants) == 1
    assert grants[0]["programs"][0]["key"] == asdict(key)
    assert grants[0]["required_tokens"] == 300
    if client_bound:
        assert key.request_id not in f.runtime.visible
        assert f.runtime.context_sessions.get(key.context_id) is None
        assert not f.admission.allows(key)


def test_bound_parent_can_exchange_only_complete_blocked_donor_budget(confirmed_parent):
    f = confirmed_parent
    f.admission.policy.capacity_tokens = 700
    key = bind_client(f)
    donor = blocked(f.runtime)
    assert f.admission.policy.remaining_capacity() == 198
    assert f.admission.claim_restore(key, source="join_ticket")
    assert f.owner.active and not donor.active
    assert f.admission.policy.remaining_capacity() == 400
    assert f.admission.latest[key.invocation_id] == f.origin
    assert f.recovery.plans[key.invocation_id][0] == key
    assert f.admission.counts["join_blocked_handoff_grants"] == 1


@pytest.mark.parametrize("invalid", (
    "checkpoint", "generation", "epoch", "request", "session_rebound", "expired",
    "tool_blocker", "child_blocker", "terminal", "marked", "pending", "disabled",
    "attempt", "foreign_workflow",
))
def test_bound_parent_cannot_use_free_budget_without_live_checkpoint_authority(
    confirmed_parent, invalid,
):
    f = confirmed_parent
    key = bind_client(f)
    ticket = f.runtime._join_ticket
    if invalid == "checkpoint":
        ticket.checkpoint_key = replace(f.origin, request_id="wrong")
    elif invalid == "generation":
        key = ticket.key = replace(key, session_generation=key.session_generation + 1)
    elif invalid == "epoch":
        key = replace(key, context_epoch=key.context_epoch + 1)
    elif invalid == "request":
        key = replace(key, request_id="different")
    elif invalid == "attempt":
        key = ticket.key = replace(key, attempt_id=key.attempt_id + 1)
    elif invalid == "foreign_workflow":
        key = replace(key, root_workflow_id="foreign")
    elif invalid == "session_rebound":
        f.runtime.context_sessions[key.context_id] = f.origin
    elif invalid == "expired":
        ticket.expires_at = time.monotonic() - 1
    elif invalid == "tool_blocker":
        f.runtime.graph.invocations[key.invocation_id].active_tool_calls["other"] = {}
    elif invalid == "child_blocker":
        f.runtime.graph.invocations[key.invocation_id].blocking_child_ids.add("other")
    elif invalid == "terminal":
        f.runtime.graph.invocations[key.invocation_id].state = InvocationState.CANCELLED
    elif invalid == "marked":
        f.owner.marked_for_pause = True
    elif invalid == "pending":
        f.owner.pending = True
    else:
        f.runtime.physical_disabled = True
    before = state(f.admission)
    assert not f.admission.claim_restore(key, source="join_ticket")
    assert state(f.admission) == before
    f.owner.active = True
    f.recovery.consumer = None, key
    if invalid not in ("marked", "pending"):
        assert not f.recovery._consumer_live()


@pytest.mark.parametrize("client_bound", (False, True))
def test_confirmed_consumer_needs_no_provisional_eos_but_still_needs_active_owner(
    confirmed_parent, client_bound,
):
    f = confirmed_parent
    key = bind_client(f) if client_bound else f.origin
    f.recovery.consumer = None, key
    assert not f.runtime._join_ticket_has_native_eos(f.runtime._join_ticket)
    assert not f.recovery._consumer_live()
    assert f.admission.claim_restore(key, source="join_ticket")
    assert f.recovery._consumer_live()
    f.runtime._join_ticket.expires_at = time.monotonic() - 1
    assert not f.recovery._consumer_live()


def test_bound_paused_parent_obtains_budget_and_enqueues_confirmed_native_burst(confirmed_parent):
    f = confirmed_parent
    key = bind_client(f)
    opportunity = rekey_opportunity(f.opportunity, key)
    assert not f.owner.active
    assert not f.runtime._join_ticket_has_native_eos(f.runtime._join_ticket)
    with patch.object(f.runtime, "inspect_context_h2d_opportunity", return_value=opportunity):
        f.runtime.dispatch_join_prefetch()
    assert f.owner.active
    assert f.admission.policy.remaining_capacity() == 700
    assert f.admission.latest[key.invocation_id] == f.origin
    assert f.runtime.counts["join_prefetch_confirmed_issued"] == 4
    assert f.runtime.physical_ledger.pending_count == 4
    assert key.request_id not in f.runtime.visible


def test_bound_parent_already_granted_free_budget_can_plan_physical_join_donors(confirmed_parent):
    f = confirmed_parent
    key = bind_client(f)
    donor = blocked(f.runtime, tokens=100)
    assert f.admission.claim_restore(key, source="join_ticket")
    plan = f.recovery.plans[key.invocation_id]
    assert plan[0] == key and plan[1] == "join"
    assert plan[2] == (f.admission.latest[donor.identity],)
    assert donor.active
    assert f.admission.counts["join_physical_plans_without_logical_handover"] == 1


def empty_restore_probe(f):
    budget = NS(reserve_full_tokens=0, native_growth_full_tokens=0, native_prefill_full_tokens=0)
    observer = Mock(return_value=NS(observable=True, device_full_free_tokens=1000))
    return (
        observer,
        patch("beliefkv.runtime.join_physical_recovery.observe_static_full_mamba_headroom", observer),
        patch.object(f.runtime, "_current_residency_budget", return_value=budget),
    )


def test_new_bound_epoch_retries_once_without_inheriting_old_backoff(confirmed_parent):
    f = confirmed_parent
    key = bind_client(f)
    assert f.admission.claim_restore(key, source="join_ticket")
    f.recovery.plans[key.invocation_id] = (f.origin, "join", (), 100.2)
    opportunity = NS(anchors=NS(key=key), required_full_tokens=0)
    observer, headroom, budget = empty_restore_probe(f)
    with headroom, budget:
        for now, expected in ((100.05, 1), (100.06, 1), (100.21, 1), (100.26, 2)):
            with patch("time.monotonic", return_value=now):
                assert not f.recovery.advance(key, opportunity=opportunity)
            assert observer.call_count == expected
    assert f.recovery.plans[key.invocation_id][0] == key


def test_unproven_same_epoch_request_cannot_rearm_physical_plan(confirmed_parent):
    f = confirmed_parent
    key = bind_client(f)
    assert f.admission.claim_restore(key, source="join_ticket")
    plan = f.origin, "join", (), 100.2
    f.recovery.plans[key.invocation_id] = plan
    with patch("time.monotonic", return_value=100.05):
        assert not f.recovery.advance(replace(key, request_id="unbound"))
    assert f.recovery.plans[key.invocation_id] == plan
    assert not f.runtime.counts["join_physical_plan_attempts"]


def test_native_arrival_wakes_same_bound_key_and_reuses_existing_plan(confirmed_parent):
    f = confirmed_parent
    key = bind_client(f)
    assert f.admission.claim_restore(key, source="join_ticket")
    f.recovery.plans[key.invocation_id] = (key, "join", (), time.monotonic() + .2)
    target = req(key.request_id)
    target.beliefkv_metadata.update(
        invocation_id=key.invocation_id, context_id=key.context_id,
        context_epoch=key.context_epoch,
    )
    target.session_id, target.session_generation = key.session_id, key.session_generation
    target.origin_input_ids = [1] * 16
    f.runtime.register_visible_request(target)
    assert f.admission.latest[key.invocation_id] == key
    assert f.recovery.plans[key.invocation_id] == (key, "join", (), 0.)
    assert f.recovery._live_consumer(target, key)


def test_real_capacity_invalidation_wakes_affected_plan_with_bounded_retries(confirmed_parent):
    f = confirmed_parent
    key = bind_client(f)
    assert f.admission.claim_restore(key, source="join_ticket")
    f.recovery.remember(key, "join", ())
    opportunity = NS(anchors=NS(key=key), required_full_tokens=0)
    observer, headroom, budget = empty_restore_probe(f)
    with headroom, budget:
        with patch("time.monotonic", return_value=100.):
            f.recovery.advance(key, opportunity=opportunity)
            f.recovery.capacity_changed(context_id="unrelated")
            assert f.recovery.plans[key.invocation_id][3] == 100.2
            f.runtime._invalidate_wait_transfer_observation(key)
            assert f.recovery.plans[key.invocation_id][3] == 100.05
        for now, expected in ((100.04, 1), (100.051, 2), (100.052, 2)):
            with patch("time.monotonic", return_value=now):
                f.runtime._invalidate_wait_transfer_observation(key)
                f.recovery.advance(key, opportunity=opportunity)
            assert observer.call_count == expected
    assert f.recovery.plans[key.invocation_id][3] == 100.101


def test_unrelated_native_ack_does_not_wake_join_plan(confirmed_parent):
    f = confirmed_parent
    key = bind_client(f)
    f.recovery.plans[key.invocation_id] = (key, "join", (), 100.2)
    f.runtime.on_native_transfer_commit(NS(
        direction="d2h", status="completed", node_ids=(), child_commits=(),
    ))
    assert f.recovery.plans[key.invocation_id][3] == 100.2


def test_confirmed_prefetch_ack_wakes_only_matching_join_plan(confirmed_parent):
    f = confirmed_parent
    key = bind_client(f)
    assert f.admission.claim_restore(key, source="join_ticket")
    opportunity = rekey_opportunity(f.opportunity, key)
    with patch.object(f.runtime, "inspect_context_h2d_opportunity", return_value=opportunity):
        f.runtime.dispatch_join_prefetch()
    ticket = f.runtime._join_ticket
    assert len(ticket.command_ids) == 4
    f.recovery.plans[key.invocation_id] = (key, "join", (), 100.2)
    f.recovery._last_attempt[key.invocation_id] = 100.
    snapshot = Mock(return_value=opportunity.anchors.component_leaves)
    f.cache.session_refs = NS(
        snapshot_session_leaf_anchors=snapshot,
        snapshot_latest_session_leaf_anchors=snapshot,
    )
    completed = f.runtime.on_native_transfer_commit(NS(
        direction="h2d", status="completed", node_ids=(1,),
        num_tokens_by_pool=(("kv", 4),),
        child_commits=(NS(
            command_id=ticket.command_ids[0], anchor_node_id=1, published_node_ids=(1,),
            num_tokens_by_pool=(("kv", 4),), num_bytes=40,
        ),),
    ))
    assert len(completed) == 1
    assert f.recovery.plans[key.invocation_id][3] == 100.05
    assert not f.runtime.physical_disabled
