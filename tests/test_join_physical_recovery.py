from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.core.events import RelationType, RuntimeEventKind as Kind
from beliefkv.runtime.join_blocked_handoff import JoinBlockedHandoff
from tests.test_full_only_native_lifecycle import full_native
from tests.test_layer_handoff import native_handoff
from tests.test_tool_policy_adaptation import demand_runtime
from tests.test_tool_predictive_transfers import event


def observed_join_wait(runtime, invocation, child, *, start):
    runtime.on_events((
        event(start, Kind.INVOCATION_CREATE, invocation_id=child, context_id=f"ctx-{child}",
              parent_invocation_id=invocation, relation_type=RelationType.SPAWN),
        event(start + 1, Kind.JOIN_CREATE, join_id=f"join-{invocation}",
              member_invocation_ids=(child,)),
        event(start + 2, Kind.JOIN_WAIT, invocation_id=invocation, join_id=f"join-{invocation}"),
    ))


def bridge(f, *, eos=False):
    runtime, a = f.runtime, f.runtime._agent_admission
    runtime.on_events((event(
        10, Kind.TOOL_END, invocation_id="tool", attributes={"tool_run_id": "long"},
    ),))
    observed_join_wait(runtime, "tool", "tool-child", start=11)
    observed_join_wait(runtime, "consumer", "consumer-child", start=20)
    key = runtime.visible[f.request.rid]
    donor_key = runtime.context_sessions["ctx-tool"]
    if eos:
        a.policy.programs["consumer"].pending = False
        a.policy.programs["consumer"].acting = True
        a.requests.pop(f.request.rid)
        a._live_requests.pop(f.request.rid)
        runtime._join_ticket = NS(key=key)
        runtime._live_join_ticket = Mock(return_value=True)
        runtime._join_ticket_has_native_eos = Mock(return_value=True)
    else:
        runtime.on_events((event(30, Kind.RETURN, invocation_id="consumer-child"),))
        assert runtime._ready_join_parent(f.request, key)
    controller = JoinBlockedHandoff(a).recovery
    controller.remember(key, "join-consumer", [a.policy.programs["tool"]])
    return controller, key, donor_key


@pytest.mark.parametrize("backed", (False, True))
def test_same_join_donor_advances_actual_selected_parent_without_phantom_capacity(
    native_handoff, backed,
):
    f = native_handoff
    controller, key, donor = bridge(f)
    if backed:
        f.donor.component_data[0].host_value = f.donor.component_data[0].value.clone()
        f.cache.tree_core._update_duplicate_tracking(f.donor)
    before = f.allocator.available_size()
    assert controller.advance(key, request=f.request)
    assert f.donor.component_data[0].value is None
    assert f.donor.component_data[0].host_value is not None
    assert f.allocator.available_size() == (before + 32 if backed else before)
    assert f.runtime.counts["join_planned_physical_release_tokens"] == (32 if backed else 0)
    assert f.runtime.counts["join_planned_reserved_handoff_tokens"] == (0 if backed else 32)
    if not backed:
        assert f.consumer.component_data[0].value is not None
        assert f.cache._beliefkv_layer_handoff is not None


@pytest.mark.parametrize("cause", (
    "returned", "pending", "protected", "stale", "fits", "non_join_consumer", "host_locked",
))
def test_planned_join_physical_actions_revalidate_donor_consumer_and_actual_deficit(
    native_handoff, cause,
):
    f = native_handoff
    controller, key, donor = bridge(f)
    if cause == "returned":
        f.runtime.on_events((event(40, Kind.RETURN, invocation_id="tool-child"),))
    elif cause == "pending":
        f.runtime._agent_admission.policy.programs["tool"].pending = True
    elif cause == "protected":
        f.runtime._prefetch_service_leases["test"] = NS(key=donor)
    elif cause == "stale":
        f.runtime.context_sessions[donor.context_id] = NS()
    elif cause == "fits":
        f.runtime._request_restore_cost = Mock(return_value=(0, 1))
        f.runtime._read_request_reentry = Mock(return_value={"missing_full_tokens": 0})
    elif cause == "host_locked":
        data = f.donor.component_data[0]
        data.host_value = data.value.clone()
        data.host_lock_ref += 1
        f.cache.tree_core._update_duplicate_tracking(f.donor)
    else:
        f.runtime._join_priority_pending.pop(key.invocation_id)
    try:
        assert not controller.advance(key, request=f.request)
        assert f.allocator.available_size() == 4
        assert f.donor.component_data[0].value is not None
        assert getattr(f.cache, "_beliefkv_layer_handoff", None) is None
    finally:
        f.runtime._prefetch_service_leases.clear()


@pytest.mark.parametrize("observed_eos", (False, True))
def test_unsubmitted_parent_uses_only_actual_eos_authority_without_inventing_input(
    native_handoff, observed_eos,
):
    f = native_handoff
    controller, key, donor = bridge(f, eos=True)
    f.runtime._join_ticket_has_native_eos.return_value = observed_eos
    anchors = f.runtime._snapshot_key_session_anchors(
        f.cache, key, reusable_input_tokens=None,
    )
    assert anchors is not None
    opportunity = NS(anchors=anchors, required_full_tokens=16)
    try:
        assert controller.advance(key, opportunity=opportunity) is observed_eos
        assert f.allocator.available_size() == 4
        assert f.runtime.counts["layer_handoff_submitted"] == int(observed_eos)
        if observed_eos:
            assert f.consumer.component_data[0].value is not None
            assert f.runtime.counts["join_planned_reserved_handoff_tokens"] == 32
    finally:
        f.runtime._join_ticket = None


def test_service_consumes_join_plan_without_forcing_eligibility_expiry(native_handoff):
    f = native_handoff
    controller, key, donor = bridge(f)
    a = f.runtime._agent_admission
    was_active = a.policy.programs[key.invocation_id].active
    controller.serviced(NS(reqs=[f.request]))
    assert not controller.plans
    assert a.policy.programs[key.invocation_id].active is was_active


@pytest.mark.parametrize("queue,kind", (("ack_write_queue", "d2h"), ("ack_load_queue", "h2d")))
def test_layer_handoff_can_coexist_with_proven_unrelated_outstanding_ack(
    native_handoff, queue, kind,
):
    f = native_handoff
    hot = next(iter(f.cache.components[0]._session_leaves["hot"]))
    cc = f.cache.cache_controller
    unrelated = NS(node_ids=[hot.id], finish_event=NS(query=lambda: False))
    getattr(cc, queue).append(unrelated)
    assert f.native.begin(f.cache, **f.args) is not None
    assert getattr(cc, queue)[0] is unrelated
    assert not unrelated.finish_event.query()
    assert f.allocator.available_size() == 4


@pytest.mark.parametrize("target", ("donor", "consumer", "unknown"))
def test_related_or_unknown_ack_still_refuses_layer_transaction(native_handoff, target):
    f = native_handoff
    node = f.donor if target == "donor" else f.consumer
    f.cache.cache_controller.ack_write_queue.append(NS(
        node_ids=[node.id if target != "unknown" else -1],
    ))
    assert f.native.begin(f.cache, **f.args) is None
    assert f.allocator.available_size() == 4
    assert f.donor.component_data[0].value is not None
    assert getattr(f.cache, "_beliefkv_layer_handoff", None) is None
