from array import array
from copy import copy
from dataclasses import replace
import time
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.control.causal_graph import InvocationState
from beliefkv.policy.tool_boundary_preemption import KIND, pair_gain_ms
from beliefkv.runtime.tool_boundary_preemption import ToolBoundaryPreemption
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracle
from tests.test_full_only_native_lifecycle import full_native
from tests.test_model_work_combination_oracle import artifact
from tests.test_reference_tool_replacement import pair, run
from tests.test_tool_policy_adaptation import demand_runtime
from tests.test_tool_predictive_transfers import event
from tests.test_workflow_work_oracle import add_serial_graphs, oracle_file


def config():
    return {
        "kind": KIND, "margin_ms": 400.,
        "cooldown_ms": 2000., "ready_wait_protection_ms": 30_000.,
    }


def enabled(parts, tmp_path, *, donor_work=1000, consumer_work=10, hint=False):
    from test_beliefkv_terminal_capacity import insert_session

    runtime, admission, controller, adder = pair(parts)
    cache = parts[2]
    node, generation = insert_session(cache, [301, 302], "short-session")
    node.component_data[0].host_value = node.component_data[0].value.clone()
    cache.tree_core._update_duplicate_tracking(node)
    cache._demote(node.id, {})
    runtime.on_events((
        replace(event(20, Kind.WORKFLOW_START), workflow_id="short-work"),
        replace(event(
            21, Kind.INVOCATION_CREATE, invocation_id="short", context_id="ctx-short",
        ), workflow_id="short-work"),
        replace(event(
            22, Kind.LLM_SUBMIT, invocation_id="short", context_id="ctx-short",
            attributes={"request_id": "short"},
        ), workflow_id="short-work"),
    ))
    request = copy(parts[5])
    request.rid = "short"
    request.origin_input_ids = array("q", (301, 302, 303))
    request.session_id, request.session_generation = "short-session", generation
    request.beliefkv_metadata = {
        "root_workflow_id": "short-work", "invocation_id": "short",
        "context_id": "ctx-short", "context_epoch": 0,
    }
    runtime.register_visible_request(request)
    admission.policy.programs["short"].tokens = 30
    admission.policy.programs["short"].active = False
    admission._dispatch_pending.pop("consumer", None)
    admission._remember_dispatch(admission.latest["short"])
    runtime._capacity_waiting_queue = [request]
    donor = admission.latest["tool"]
    raw = artifact(tmp_path)
    raw.update(
        shortest_work_first_admission=True,
        tool_boundary_preemption=config(),
        requests={
            "future-donor": {
                "workflow_id": donor.root_workflow_id, "invocation_id": "tool",
                "context_id": donor.context_id, "prompt_tokens": 1, "input_increment": 1,
                "output_tokens": donor_work, "predecessor_request_id": None,
            },
            "future-consumer": {
                "workflow_id": "short-work", "invocation_id": "short",
                "context_id": "ctx-short", "prompt_tokens": 1, "input_increment": 1,
                "output_tokens": consumer_work, "predecessor_request_id": None,
            },
        },
        workflows={
            donor.root_workflow_id: {"total_work_units": 100 * donor_work + 1, "requests": 1},
            "short-work": {"total_work_units": 100 * consumer_work + 1, "requests": 1},
        },
    )
    add_serial_graphs(raw)
    admission._work = WorkflowWorkOracle(*oracle_file(tmp_path, raw))
    boundary = admission._tool_boundary_preemption = ToolBoundaryPreemption(admission, raw)
    boundary.events((event(23, Kind.TOOL_START, invocation_id="tool"),))
    if not hint:
        runtime.tool_wait_hints.clear()
    controller.rearm()
    return (runtime, admission, controller, adder), boundary, request


def test_serial_two_job_gain_charges_backup_twice_and_both_restores():
    gain = pair_gain_ms(
        30., 2., model_seconds_per_work_token=1.,
        backup_ms=100., consumer_restore_ms=100., donor_restore_ms=100., margin_ms=0.,
    )
    keep_sum = 30_000. + 32_100.
    switch_sum = 2200. + 32_300.
    assert gain == keep_sum - switch_sum == 27_600.


@pytest.mark.parametrize("field,value", (
    ("donor_work", float("nan")), ("consumer_work", -1.), ("backup_ms", True),
    ("donor_restore_ms", float("inf")), ("model_seconds_per_work_token", 0.),
))
def test_invalid_work_and_costs_do_not_produce_a_positive_score(field, value):
    values = {
        "donor_work": 30., "consumer_work": 2., "model_seconds_per_work_token": 1.,
        "backup_ms": 100., "consumer_restore_ms": 100.,
        "donor_restore_ms": 100., "margin_ms": 400.,
    }
    values[field] = value
    with pytest.raises(ValueError):
        pair_gain_ms(**values)


@pytest.mark.parametrize("hint", (False, True))
@pytest.mark.parametrize("through_plan", (False, True))
def test_real_native_release_can_admit_short_work_without_long_tool_forecast(
    demand_runtime, tmp_path, monkeypatch, hint, through_plan,
):
    old_hint = next(iter(demand_runtime[0].tool_wait_hints.values()))
    parts, boundary, request = enabled(demand_runtime, tmp_path)
    runtime, admission, controller, _adder = parts
    monkeypatch.setattr(
        "beliefkv.runtime.workflow_work_oracle.cache_remaining_paths",
        lambda *args, **kwargs: pytest.fail("tool-boundary policy scanned future paths"),
    )
    if hint:
        now = time.monotonic() * 1000.
        runtime.tool_wait_hints[old_hint.key.context_id] = replace(
            old_hint, wait_p10_ms=1., wait_p50_ms=2., wait_p90_ms=3.,
            issued_monotonic_ms=now, expires_monotonic_ms=now + 1000.,
            release_cdf=(), residual_duration_mass=(),
        )
    admission._tool_boundary_preemption = None
    assert not run(parts, through_plan=through_plan)
    assert demand_runtime[4].component_data[0].value is not None
    admission._tool_boundary_preemption = boundary
    controller.rearm()
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    assert run(parts, through_plan=through_plan)
    assert demand_runtime[4].component_data[0].value is None
    assert not admission.policy.programs["tool"].active
    assert admission.policy.programs["short"].active
    assert not admission.policy.programs["consumer"].active
    assert boundary.preempted["tool"]["grant_complete"]
    assert admission.counts["tool_boundary_replacement_grants"] == 1
    assert controller.action_detail["tool_boundary_preemption"]
    assert not runtime.enable_tool_prefetch and not runtime.enable_frontier_restore
    marker = next(row for row in records if row["event"] == "agent_predicted_tool_replacement")
    assert marker["tool_boundary_preemption"] and marker["consumer"]["request_id"] == request.rid
    assert marker["tool_boundary_original_forecast_allowed"] is False
    assert not runtime.physical_ledger.pending_count


@pytest.mark.parametrize("reason", ("not_shorter", "transfer_cost", "same_workflow"))
def test_work_order_and_switch_cost_must_justify_the_additional_path(
    demand_runtime, tmp_path, monkeypatch, reason,
):
    parts, boundary, _request = enabled(
        demand_runtime, tmp_path, donor_work=5 if reason == "not_shorter" else 1000,
    )
    runtime, admission, controller, _adder = parts
    if reason == "transfer_cost":
        monkeypatch.setattr(controller, "_service_ms", lambda *_: 100_000.)
    elif reason == "same_workflow":
        original = boundary.evaluate
        monkeypatch.setattr(boundary, "evaluate", lambda donor, consumer, **kwargs: original(
            donor, replace(consumer, root_workflow_id=donor.root_workflow_id), **kwargs,
        ))
    assert not run(parts)
    assert demand_runtime[4].component_data[0].value is not None
    assert admission.policy.programs["tool"].active
    assert not admission.policy.programs["short"].active
    assert not boundary.preempted


@pytest.mark.parametrize("guard", (
    "returned", "locked", "shared", "no_consumer", "allocated_consumer", "protected", "cooldown",
    "stale_generation", "pending_load", "prefetch_protected", "no_slot",
    "join_wait",
))
def test_forecast_relaxation_preserves_native_and_service_protection(
    demand_runtime, tmp_path, guard,
):
    parts, boundary, request = enabled(demand_runtime, tmp_path)
    runtime, admission, _controller, _adder = parts
    donor = admission.latest["tool"]
    if guard == "returned":
        runtime.graph.invocations["tool"].active_tool_calls.clear()
    elif guard == "locked":
        demand_runtime[4].component_data[0].lock_ref = 1
    elif guard == "shared":
        demand_runtime[4].component_data[0].session_ids.add("another")
    elif guard == "no_consumer":
        runtime._capacity_waiting_queue.clear()
    elif guard == "allocated_consumer":
        request.kv.req_pool_idx = 0
    elif guard == "protected":
        boundary.protected["tool"] = donor
    elif guard == "stale_generation":
        runtime.context_sessions[donor.context_id] = replace(
            donor, session_generation=donor.session_generation + 1,
        )
    elif guard == "pending_load":
        demand_runtime[4].load_back_pending_id = "loading"
    elif guard == "prefetch_protected":
        runtime._prefetch_service_leases["protected"] = NS(key=donor)
    elif guard == "no_slot":
        parts[3].max_running_requests = 0
    elif guard == "join_wait":
        invocation = runtime.graph.invocations["tool"]
        invocation.state = InvocationState.WAIT_JOIN
        invocation.join_id = "waiting-join"
        runtime.graph.joins["waiting-join"] = NS(
            satisfied=False, workflow_id=donor.root_workflow_id, waiter_invocation_ids={"tool"},
        )
        assert runtime._agent_observed_blocked_budget_key(donor)
        assert invocation.active_tool_calls and not invocation.blocking_child_ids
        assert not boundary.donor_candidate(donor)
    else:
        boundary.cooldowns[donor.root_workflow_id] = time.monotonic() * 1000. + 100_000.
    try:
        changed = run(parts)
    finally:
        if guard == "prefetch_protected":
            runtime._prefetch_service_leases.pop("protected", None)
    assert not changed
    assert demand_runtime[4].component_data[0].value is not None
    assert admission.policy.programs["tool"].active
    assert not admission.policy.programs["short"].active


def test_partial_physical_release_keeps_only_the_same_pair_authorized(
    demand_runtime, tmp_path, monkeypatch,
):
    parts, boundary, _request = enabled(demand_runtime, tmp_path)
    _runtime, admission, controller, _adder = parts
    original = controller._after_eviction
    observed = []

    def check(key, freed):
        original(key, freed)
        assert not admission.policy.programs["tool"].active
        assert boundary.donor_candidate(key)
        debt = boundary.preempted["tool"]
        assert not debt["grant_complete"]
        assert boundary.evaluate(
            key, replace(debt["consumer"], request_id="not-the-selected-consumer"),
            backup_ms=0., donor_restore_ms=0., consumer_restore_ms=0.,
        ) is None
        observed.append(key)

    monkeypatch.setattr(controller, "_after_eviction", check)
    assert run(parts)
    assert len(observed) == 1
    assert not boundary.donor_candidate(observed[0])


def test_long_ready_wait_protects_the_following_tool_even_without_new_preemption(
    demand_runtime, tmp_path, monkeypatch,
):
    parts, boundary, _request = enabled(demand_runtime, tmp_path)
    _runtime, admission, _controller, _adder = parts
    key = admission.latest["tool"]
    admission.requests[key.request_id] = key, 0.
    now = time.monotonic()
    boundary.ready_since[key] = (now - 31.) * 1000.
    monkeypatch.setattr(time, "monotonic", lambda: now)
    boundary.serviced(NS(reqs=[NS(rid=key.request_id)]))
    assert boundary.protected["tool"] == key
    assert not boundary.donor_candidate(key)
    assert not boundary.preempted
    boundary.forget("tool")
    assert not boundary.protected and not boundary.waiting


@pytest.mark.parametrize("race", (None, "tool_return", "consumer_disappears"))
def test_unbacked_donor_requires_actual_D2H_ACK_and_fresh_pair_validation(
    demand_runtime, tmp_path, race,
):
    from unittest.mock import Mock
    from tests.test_prepare_ack_allocation import native_write_start

    parts, boundary, _request = enabled(demand_runtime, tmp_path)
    runtime, admission, controller, _adder = parts
    cache, donor = demand_runtime[2], demand_runtime[4]
    native_write_start(cache)
    donor.component_data[0].host_value = None
    cache.tree_core._update_duplicate_tracking(donor)
    cache._remember_beliefkv_unbacked_full(donor)
    cache.cache_controller._num_tokens_by_pool = lambda op: {"kv": len(op.device_indices)}
    cache.cache_controller._transfer_num_bytes = lambda op: 64 * len(op.device_indices)
    cache.host_pool_group.alloc = Mock(side_effect=AssertionError("native stand-in owns Host indices"))
    cache.host_pool_group.free = Mock()
    cache.on_hicache_transfer_commit = runtime.on_native_transfer_commit
    assert not run(parts)
    assert runtime.physical_ledger.pending_action_count("PREPARE_HOST") == 1, (
        dict(runtime.counts), dict(admission.counts), controller.action_detail,
    )
    assert donor.component_data[0].value is not None and not boundary.preempted
    if race == "tool_return":
        runtime.graph.invocations["tool"].active_tool_calls.clear()
    elif race == "consumer_disappears":
        runtime._capacity_waiting_queue.clear()
    cache.writing_check(finish_count=1)
    assert not runtime.physical_ledger.pending_count
    assert controller.replacement.next_ms == 0.
    assert run(parts) is (race is None)
    assert admission.policy.programs["short"].active is (race is None)
