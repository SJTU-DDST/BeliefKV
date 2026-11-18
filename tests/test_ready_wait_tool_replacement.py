from array import array
from copy import copy
from dataclasses import replace
import time
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.ready_wait_priority import ReadyWaitPriority
from beliefkv.runtime.workflow_combination import WorkflowCombinationController
from tests.test_full_only_native_lifecycle import full_native
from tests.test_reference_tool_replacement import pair, run
from tests.test_shortest_work_admission import tool_controller
from tests.test_tool_policy_adaptation import demand_runtime
from tests.test_tool_predictive_transfers import event


def test_tool_consumer_order_uses_the_same_wait_priority_as_combination_admission():
    controller, admission, keys = tool_controller()
    original = controller.prediction
    controller.prediction = lambda workflow: (
        NS(evidence="model", effective_weight=.8) if workflow == keys[0].root_workflow_id
        else original(workflow)
    )
    admission._protected_join = lambda key: False
    priority = controller.ready_wait_priority = ReadyWaitPriority(admission, 30000)
    priority.begin([keys[0]], time.monotonic() - 150.)
    assert controller.order_tool_replacement(keys)[0] == keys[0]


def test_protected_join_still_precedes_an_aged_ordinary_tool_consumer():
    controller, admission, keys = tool_controller()
    priority = controller.ready_wait_priority = ReadyWaitPriority(admission, 30000)
    priority.begin([keys[0]], time.monotonic() - 150.)
    assert controller.order_tool_replacement(keys) == [keys[2], keys[1], keys[0]]


@pytest.mark.parametrize("wait_s", (150., 3600., 1e6))
def test_long_workflow_waiting_cannot_overturn_a_large_remaining_work_gap(wait_s):
    controller, admission, keys = tool_controller()
    admission._protected_join = lambda key: False
    priority = controller.ready_wait_priority = ReadyWaitPriority(admission, 120000)
    priority.begin([keys[0]], time.monotonic() - wait_s)
    assert controller.order_tool_replacement(keys) == [keys[1], keys[0], keys[2]]


def test_first_tool_pair_observation_does_not_inherit_an_old_request_epoch():
    controller, admission, keys = tool_controller()
    admission._protected_join = lambda key: False
    priority = controller.ready_wait_priority = ReadyWaitPriority(admission, 30000)
    priority.begin([keys[0]], time.monotonic() - 150.)
    updated = replace(keys[0], request_id="next-request", context_epoch=1)
    assert controller.order_tool_replacement([updated, keys[1]]) == [keys[1], updated]
    assert priority.since[updated.invocation_id][0] == updated


def test_tool_pair_journal_records_the_wait_adjustment_used_for_ordering():
    controller, admission, keys = tool_controller()
    records = []
    admission.runtime._opportunity_writer = NS(record=records.append)
    priority = controller.ready_wait_priority = ReadyWaitPriority(admission, 30000)
    priority.begin([keys[0]], time.monotonic() - 150.)
    controller.order_tool_replacement(keys)
    record = records[-1]
    assert record["ready_wait_priority"]["workflow_age_ms"][keys[0].root_workflow_id] >= 150000.
    assert record["ordered"][1]["key"]["invocation_id"] == keys[1].invocation_id
    assert record["ready_wait_priority"]["max_boost"] == 1.25
    assert not record["physical_capacity_credit"]


@pytest.mark.parametrize(
    "aging,long_weight", ((False, .2), (True, .2), (False, .8), (True, .8)),
    ids=("False", "True", "close-False", "close-True"),
)
def test_aged_tool_consumer_changes_real_handover_and_native_eligibility(
    demand_runtime, monkeypatch, aging, long_weight,
):
    from test_beliefkv_terminal_capacity import insert_session

    parts = pair(demand_runtime)
    runtime, admission, offload, _adder = parts
    cache = demand_runtime[2]
    node, generation = insert_session(cache, [301, 302], "other-session")
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
    other = copy(demand_runtime[5])
    other.rid = "short"
    other.beliefkv_metadata = {
        "root_workflow_id": "short-work", "invocation_id": "short",
        "context_id": "ctx-short", "context_epoch": 0,
    }
    other.origin_input_ids = array("q", (301, 302, 303))
    other.session_id, other.session_generation = "other-session", generation
    runtime.register_visible_request(other)
    for owner in ("consumer", "short"):
        admission.policy.programs[owner].tokens = 30
        admission.policy.programs[owner].active = False
        admission._remember_dispatch(admission.latest[owner])
    runtime._capacity_waiting_queue.append(other)
    selector = WorkflowCombinationController.__new__(WorkflowCombinationController)
    selector.admission, selector.shortest_work_first = admission, True
    monkeypatch.setattr(admission, "_supported", lambda value: value is not None, raising=False)
    monkeypatch.setattr(admission, "_protected_join", lambda key: False, raising=False)
    selector.prediction = lambda workflow: NS(
        evidence="model", effective_weight=.9 if workflow == "short-work" else long_weight,
    )
    if aging:
        selector.ready_wait_priority = ReadyWaitPriority(admission, 30000)
        selector.ready_wait_priority.begin([admission.latest["consumer"]], time.monotonic() - 150.)
    monkeypatch.setattr(admission, "order_tool_replacement", selector.order_tool_replacement)
    chosen, excluded = (
        ("consumer", "short") if aging and long_weight == .8 else ("short", "consumer")
    )
    candidates = run(parts, through_plan=True)
    assert admission.policy.programs[chosen].active
    assert not admission.policy.programs[excluded].active
    assert not admission.policy.programs["tool"].active
    assert [request.rid for request in candidates] == [admission.latest[chosen].request_id]
    assert admission.counts["tool_event_replacement_grants"] == 1
    assert not admission.counts["control_ticks"]
    assert not runtime.physical_ledger.pending_count
    assert not runtime.enable_tool_prefetch
