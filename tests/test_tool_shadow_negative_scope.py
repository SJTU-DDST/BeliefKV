from array import array
from copy import copy
from dataclasses import replace
from unittest.mock import Mock

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.sglang_v0520_observer import observe_static_full_mamba_headroom
from tests.test_full_only_native_lifecycle import full_native
from tests.test_reference_tool_replacement import pair, run
from tests.test_tool_policy_adaptation import demand_runtime
from tests.test_tool_predictive_transfers import event


def two_consumers(parts):
    from test_beliefkv_terminal_capacity import insert_session

    runtime, admission, controller, adder = parts = pair(parts)
    cache = runtime._native_cache
    node, generation = insert_session(cache, [301, 302], "other-session")
    node.component_data[0].host_value = node.component_data[0].value.clone()
    cache.tree_core._update_duplicate_tracking(node)
    cache._demote(node.id, {})
    runtime.on_events((
        replace(event(20, Kind.WORKFLOW_START), workflow_id="other-work"),
        replace(event(21, Kind.INVOCATION_CREATE, invocation_id="other", context_id="ctx-other"),
                workflow_id="other-work"),
        replace(event(22, Kind.LLM_SUBMIT, invocation_id="other", context_id="ctx-other",
                      attributes={"request_id": "other"}), workflow_id="other-work"),
    ))
    request = copy(runtime._capacity_waiting_queue[0])
    request.rid = "other"
    request.origin_input_ids = array("q", [301, 302, 303])
    request.session_id, request.session_generation = "other-session", generation
    request.beliefkv_metadata = {
        "root_workflow_id": "other-work", "invocation_id": "other",
        "context_id": "ctx-other", "context_epoch": 0,
    }
    runtime.register_visible_request(request)
    for owner in ("consumer", "other"):
        admission.policy.programs[owner].tokens = 30
        admission.policy.programs[owner].active = False
        admission._remember_dispatch(admission.latest[owner])
    runtime._capacity_waiting_queue.append(request)
    controller.rearm()
    return parts


def test_native_locked_donor_miss_is_shared_only_between_consumers_of_one_pass(
    demand_runtime, monkeypatch,
):
    parts = two_consumers(demand_runtime)
    runtime, admission, controller, _adder = parts
    donor = demand_runtime[4]
    donor.component_data[0].lock_ref = 1
    inspect = Mock(wraps=runtime._refreshed_shadow_backup_selection)
    monkeypatch.setattr(runtime, "_refreshed_shadow_backup_selection", inspect)
    assert not run(parts)
    assert inspect.call_count == 1
    assert admission.counts["tool_event_replacement_shadow_misses_reused"] == 1
    assert controller.replacement._shadow_misses is None
    controller.rearm()
    assert not run(parts)
    assert inspect.call_count == 2
    donor.component_data[0].lock_ref = 0
    controller.rearm()
    assert run(parts)
    assert admission.counts["tool_event_replacement_grants"] == 1
    assert donor.component_data[0].value is None
    assert not runtime.physical_ledger.pending_count


@pytest.mark.parametrize("change", (
    "semantic", "graph", "session_index", "pressure_index", "headroom", "hint",
))
def test_same_pass_shadow_miss_is_invalidated_by_observed_state_change(
    demand_runtime, monkeypatch, change,
):
    parts = two_consumers(demand_runtime)
    runtime, admission, controller, _adder = parts
    donor = demand_runtime[4]
    donor.component_data[0].lock_ref = 1
    inspect = Mock(wraps=runtime._refreshed_shadow_backup_selection)
    monkeypatch.setattr(runtime, "_refreshed_shadow_backup_selection", inspect)
    demand = controller.replacement.demand

    def changed(request, key, **kwargs):
        if key.invocation_id == "other" and kwargs.get("refresh"):
            if change == "semantic":
                runtime.semantic_revision += 1
            elif change == "graph":
                runtime.graph.apply(replace(
                    event(100, Kind.WORKFLOW_START), workflow_id="unrelated-work",
                ))
            elif change == "session_index":
                runtime._context_sessions_revision += 1
            elif change == "pressure_index":
                runtime.counts["parent_pressure_index_updates"] += 1
            elif change == "headroom":
                assert demand_runtime[3].alloc(1) is not None
            else:
                key_hint = admission.latest["tool"].context_id
                runtime.tool_wait_hints[key_hint] = replace(runtime.tool_wait_hints[key_hint])
        return demand(request, key, **kwargs)

    monkeypatch.setattr(controller.replacement, "demand", changed)
    assert not run(parts)
    assert inspect.call_count == 2
    assert not admission.counts["tool_event_replacement_shadow_misses_reused"]
    assert donor.component_data[0].value is not None
    assert not admission.policy.programs["consumer"].active
    assert not admission.policy.programs["other"].active


@pytest.mark.parametrize("source", ("tool_wait", "tool_boundary"))
def test_successful_capture_and_consumer_dependent_boundary_capture_are_never_cached(
    demand_runtime, monkeypatch, source,
):
    runtime, _admission, controller, _adder = pair(demand_runtime)
    donor = demand_runtime[4]
    if source == "tool_wait":
        donor.component_data[0].host_value = None
        demand_runtime[2].tree_core._update_duplicate_tracking(donor)
    inspect = Mock(wraps=runtime._refreshed_shadow_backup_selection)
    monkeypatch.setattr(runtime, "_refreshed_shadow_backup_selection", inspect)
    replacement = controller.replacement
    replacement._shadow_misses = {}
    try:
        headroom = observe_static_full_mamba_headroom(runtime._native_cache)
        first = replacement._shadow_selection(runtime.context_sessions["ctx-tool"], headroom, source)
        second = replacement._shadow_selection(runtime.context_sessions["ctx-tool"], headroom, source)
        if source == "tool_wait":
            assert first is not None and second is not None
        assert inspect.call_count == 2
    finally:
        replacement._shadow_misses = None


def test_shadow_scope_is_discarded_when_native_inspection_raises(demand_runtime, monkeypatch):
    parts = two_consumers(demand_runtime)
    runtime, _admission, controller, _adder = parts
    demand_runtime[4].component_data[0].lock_ref = 1
    monkeypatch.setattr(
        runtime, "_refreshed_shadow_backup_selection",
        Mock(side_effect=RuntimeError("native inspection unavailable")),
    )
    with pytest.raises(RuntimeError, match="native inspection unavailable"):
        run(parts)
    assert controller.replacement._shadow_misses is None
    assert controller.replacement.consumer is None
    assert controller.replacement.donor is None
