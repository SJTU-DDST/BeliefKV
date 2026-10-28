from types import SimpleNamespace as NS
from unittest.mock import Mock
from array import array

import pytest

from beliefkv.core.events import RelationType, RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.runtime.join_blocked_handoff import JoinBlockedHandoff
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_full_only_native_lifecycle import full_native
from tests.test_layer_handoff import native_handoff
from tests.test_tool_policy_adaptation import demand_runtime
from tests.test_tool_predictive_transfers import event
from tests.test_paper_baseline_runtime import request


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
    outcome = "native_pages_released" if backed else "layer_handoff_submitted"
    assert f.runtime.counts[f"join_physical_plan_outcome_{outcome}"] == 1
    assert sum(
        value for name, value in f.runtime.counts.items()
        if name.startswith("join_physical_plan_outcome_")
    ) == f.runtime.counts["join_physical_plan_attempts"] == 1
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
        assert sum(
            value for name, value in f.runtime.counts.items()
            if name.startswith("join_physical_plan_outcome_")
        ) == f.runtime.counts["join_physical_plan_attempts"] == 1
        if cause == "fits":
            assert f.runtime.counts["join_physical_plan_outcome_no_missing_prefix"] == 1
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


def test_join_attempt_journal_distinguishes_release_from_capacity_already_fitting(native_handoff):
    f = native_handoff
    controller, key, donor = bridge(f)
    f.runtime._opportunity_writer = writer = Mock()
    f.runtime._read_request_reentry = Mock(return_value={"missing_full_tokens": 1})
    f.runtime._request_restore_cost = Mock(return_value=(1, 0))
    f.runtime._current_residency_budget = Mock(return_value=NS(
        reserve_full_tokens=0, native_growth_full_tokens=0, native_prefill_full_tokens=0,
    ))
    assert not controller.advance(key, request=f.request)
    results = [
        call.args[0] for call in writer.record.call_args_list
        if call.args[0]["event"] == "agent_join_physical_plan_result"
    ]
    assert len(results) == 1
    assert results[0]["outcome"] == "physical_capacity_already_fits"
    assert results[0]["missing_full_tokens"] == 1
    assert results[0]["observed_device_free_tokens"] == 4
    assert results[0]["required_device_tokens"] == 2
    assert results[0]["released_device_tokens"] == results[0]["reserved_handoff_tokens"] == 0
    assert results[0]["checks"] == {}
    assert f.allocator.available_size() == 4
    assert f.donor.component_data[0].value is not None


def test_join_attempt_backoff_does_not_create_new_outcomes(native_handoff):
    f = native_handoff
    controller, key, donor = bridge(f)
    f.runtime._read_request_reentry = Mock(return_value={"missing_full_tokens": 0})
    f.runtime._request_restore_cost = Mock(return_value=(0, 1))
    assert not controller.advance(key, request=f.request)
    assert not controller.advance(key, request=f.request)
    assert f.runtime.counts["join_physical_plan_attempts"] == 1
    assert f.runtime.counts["join_physical_plan_outcome_no_missing_prefix"] == 1


def test_join_attempt_error_restores_controller_state_and_remains_an_error(native_handoff):
    f = native_handoff
    controller, key, donor = bridge(f)
    f.runtime._read_request_reentry = Mock(side_effect=ValueError("observation failure"))
    original = controller.consumer, controller._required_tokens, controller._released
    with pytest.raises(ValueError, match="observation failure"):
        controller.advance(key, request=f.request)
    assert (controller.consumer, controller._required_tokens, controller._released) == original
    assert f.runtime.counts["join_physical_plan_outcome_error"] == 1
    assert f.allocator.available_size() == 4


def test_join_attempt_writer_failure_cannot_leave_the_consumer_bound(native_handoff):
    f = native_handoff
    controller, key, donor = bridge(f)
    f.runtime._read_request_reentry = Mock(return_value={"missing_full_tokens": 0})
    f.runtime._request_restore_cost = Mock(return_value=(0, 1))
    original_writer = f.runtime._opportunity_writer
    f.runtime._opportunity_writer = NS(record=Mock(side_effect=ValueError("writer failure")))
    try:
        with pytest.raises(ValueError, match="writer failure"):
            controller.advance(key, request=f.request)
    finally:
        f.runtime._opportunity_writer = original_writer
    assert controller.consumer is None
    assert controller._required_tokens == 0
    assert controller._released == controller._reserved == {}
    assert f.allocator.available_size() == 4


def test_existing_logical_eligibility_can_create_a_physical_plan_without_new_grant(native_handoff):
    f = native_handoff
    controller, key, donor = bridge(f)
    controller.plans.clear()
    a = f.runtime._agent_admission
    remaining = a.policy.remaining_capacity()
    controller.ensure(key)
    assert controller.plans[key.invocation_id][1] == "join-consumer"
    assert a.counts["join_physical_plans_without_logical_handover"] == 1
    assert not a.counts["join_blocked_handoff_grants"]
    assert a.policy.remaining_capacity() == remaining
    assert f.allocator.available_size() == 4


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


@pytest.mark.parametrize("full_native", (512,), indirect=True)
def test_two_smaller_backed_join_donors_supply_real_combined_capacity(
    full_native, monkeypatch, tmp_path,
):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, *_ = full_native
    for name, value in arm_environment({
        "AGENT_ADMISSION": "thunderagent_reference", "EXECUTION_FRONTIER_RESTORE": "0",
    }, "full").items():
        monkeypatch.setenv(name, value)
    live = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "multi-donor.sock"))
    live.attach_native_cache(cache)
    try:
        live.on_events((event(0, Kind.WORKFLOW_START),))
        nodes, requests = {}, {}
        for index, (identity, size) in enumerate((("a", 32), ("b", 32), ("target", 68))):
            live.on_events((event(
                1 + index * 20, Kind.INVOCATION_CREATE, invocation_id=identity,
                context_id=f"{identity}-context",
            ),))
            tokens = list(range(1000 * (index + 1), 1000 * (index + 1) + size))
            node, generation = insert_session(cache, tokens, identity)
            req = request(identity, identity, tokens=size + 1, workflow="wf")
            req.origin_input_ids = array("q", (*tokens, 99999))
            req.session_id, req.session_generation = identity, generation
            req.extra_key = req.cache_salt = None
            req.kv = NS(req_pool_idx=None, kv_allocated_len=0, kv_committed_len=0)
            live.register_visible_request(req)
            nodes[identity], requests[identity] = node, req
            assert cache.prepare_beliefkv_spawn_full(
                session_id=identity, session_generation=generation,
            )["issued"]
            cache.writing_check(finish_count=1)
            if identity != "target":
                live.on_batch_completed(NS(reqs=[req]))
                observed_join_wait(live, identity, f"{identity}-child", start=6 + index * 20)
        cache._demote(nodes["target"].id, {})
        observed_join_wait(live, "target", "target-child", start=70)
        live.on_events((event(80, Kind.RETURN, invocation_id="target-child"),))
        a = live._agent_admission
        target = live.visible["target"]
        assert a.allows(target)
        assert allocator.alloc(allocator.available_size() - 8) is not None
        controller = JoinBlockedHandoff(a).recovery
        controller.remember(target, "join-target", (a.policy.programs["a"], a.policy.programs["b"]))
        assert controller.advance(target, request=requests["target"])
        assert allocator.available_size() == 72
        assert nodes["a"].component_data[0].value is None
        assert nodes["b"].component_data[0].value is None
        assert live.counts["join_planned_physical_release_tokens"] == 64
        assert live.counts["join_planned_reserved_handoff_tokens"] == 0
        result = cache.prefetch_gpu_session_nodes(
            session_id="target", session_generation=target.session_generation,
            leaf_node_id=nodes["target"].id, leaf_creation_time=float(nodes["target"].creation_time),
            nodes=((nodes["target"].id, float(nodes["target"].creation_time), False, "combined-restored"),),
            beliefkv_before_enqueue=lambda _operation: a.allows(target),
        )
        assert any(row.issued for row in result)
        cache.loading_check(finish_count=1)
        assert allocator.available_size() == 4
        assert nodes["target"].component_data[0].host_lock_ref == 0
        assert nodes["target"].component_data[0].value is not None
    finally:
        live.close()
