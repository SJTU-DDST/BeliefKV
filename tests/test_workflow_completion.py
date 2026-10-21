from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.control.causal_graph import ExecutionMode, InvocationState as State
from beliefkv.policy.workflow_completion import WorkflowCompletionOrder
from tests.test_internal_comparison import join_fixture
from scripts.analyze_workflow_completion_tradeoff_cpu import simulate


def fixture(widths=(1, 3)):
    graph = NS(workflows={}, invocations={})
    pairs, keys = [], {}
    for workflow, width in enumerate(widths):
        name = str(workflow)
        identities = {f"{name}:{leaf}" for leaf in range(width)}
        graph.workflows[name] = NS(invocation_ids=identities, end_ts_ms=None)
        for identity in sorted(identities):
            graph.invocations[identity] = NS(
                state=State.READY, execution_mode=ExecutionMode.FOREGROUND,
                child_invocation_ids=set(),
            )
            index = len(pairs)
            pairs.append((index, identity))
            keys[index] = NS(root_workflow_id=name, invocation_id=identity)
    return WorkflowCompletionOrder(graph), pairs, keys


def test_focus_uses_visible_branches_and_preserves_work_conserving_fill():
    policy, pairs, keys = fixture((3, 1))
    result = policy.order(pairs, keys, attained={}, critical=lambda key: False)
    assert result[0] == pairs[-1]
    assert sorted(result) == sorted(pairs)
    assert policy.focus == "1" and policy.used == 0
    assert policy.order(pairs, keys, attained={}, critical=lambda key: False) == result
    assert policy.used == 0


def test_only_actual_admissions_charge_a_quantum_and_chunk_retries_are_coalesced():
    policy, pairs, keys = fixture()
    policy.order(pairs, keys, attained={}, critical=lambda key: False)
    for _ in range(8):
        policy.admitted("0", "same-chunked-request")
    assert policy.used == 1 and not policy.fair_turn
    policy.completed("same-chunked-request")
    assert not policy._admitted
    for index in range(3):
        policy.admitted("0", str(index))
    assert policy.fair_turn and policy.focus is None
    assert policy.order(list(reversed(pairs)), keys, attained={}, critical=lambda key: False) == list(reversed(pairs))
    policy.admitted("1", "fair-request")
    assert not policy.fair_turn


def test_one_batch_cannot_place_unbounded_focused_requests_ahead_of_ordinary_work():
    policy, pairs, keys = fixture((8, 1))
    policy.focus, policy.used = "0", 2
    result = policy.order(pairs, keys, attained={}, critical=lambda key: False)
    assert result[:3] == [pairs[0], pairs[1], pairs[-1]]


def test_join_override_cannot_consume_the_ordinary_fifo_fairness_turn():
    policy, pairs, keys = fixture()
    policy.fair_turn = True
    policy.admitted("0", "join-parent", priority_override=True)
    assert policy.fair_turn
    assert policy.order(pairs, keys, attained={}, critical=lambda _: False) == pairs
    policy.admitted("1", "ordinary")
    assert not policy.fair_turn


def test_blocked_focus_is_released_and_fairness_does_not_wait_for_future_requests():
    policy, pairs, keys = fixture()
    policy.focus, policy.used = "0", 1
    remaining = pairs[1:]
    assert policy.order(remaining, keys, attained={}, critical=lambda key: False) == remaining
    assert policy.focus == "1" and policy.used == 0


def test_width_counts_foreground_leaves_once_and_invalidation_is_per_workflow():
    policy, _, _ = fixture((3, 1))
    graph = policy.graph
    graph.invocations["0:0"].child_invocation_ids.add("0:1")
    assert policy.width("0") == 2
    assert policy.width("1") == 1
    graph.invocations["0:1"].state = State.DONE
    policy.invalidate("0")
    assert policy._widths == {"1": 1}
    assert policy.width("0") == 2
    graph.invocations["0:2"].execution_mode = ExecutionMode.BACKGROUND
    policy.invalidate("0")
    assert policy.width("0") == 1


def test_last_join_child_wins_within_the_selected_workflow_only():
    policy, pairs, keys = fixture((2, 2))
    result = policy.order(pairs, keys, attained={}, critical=lambda key: key.invocation_id.endswith(":1"))
    assert result[0] == pairs[1] and result[1] == pairs[0]


def test_completion_profile_keeps_simple_join_parent_priority_and_no_cache_scoring(monkeypatch):
    runtime, requests = join_fixture(monkeypatch, mode="full", scheduling="workflow_completion")
    runtime._read_request_reentry = Mock(side_effect=AssertionError("no cache scan"))
    try:
        assert runtime.plan_native_prefill(requests, running_batch=None, adder=None).prioritized[0].invocation_id == "parent"
        assert runtime._completion_order.used == 0
        assert not runtime.enable_resident_first
    finally:
        runtime.close()


def test_delayed_graph_observation_uses_native_order_without_guessing_a_tiny_workflow(monkeypatch):
    runtime, requests = join_fixture(monkeypatch, mode="full", scheduling="workflow_completion")
    runtime._join_priority_pending.clear()
    del runtime.graph.invocations["normal"]
    try:
        plan = runtime.plan_native_prefill(requests, running_batch=None, adder=None)
        assert [key.request_id for key in plan.prioritized] == [request.rid for request in requests]
        assert runtime._completion_order.focus is None
    finally:
        runtime.close()


@pytest.mark.parametrize("quantum", (0, -1, True, 2.5))
def test_bad_quantum_is_rejected(quantum):
    with pytest.raises(ValueError):
        WorkflowCompletionOrder(NS(), quantum=quantum)


def test_equal_work_exposes_the_request_weighting_and_the_width_counterexample():
    for work, improves in (
        ({"wide": (1.,) * 8, "narrow": (4.,)}, True),
        ({"wide": (1., 1.), "narrow": (100.,)}, False),
    ):
        request = simulate(work, policy="request_shortest")
        workflow = simulate(work, policy="workflow_completion")
        assert request["total_service"] == workflow["total_service"]
        assert request["request_count"] == workflow["request_count"]
        assert (workflow["mean_workflow_jct"] < request["mean_workflow_jct"]) is improves
        if improves:
            assert workflow["mean_request_ttft"] > request["mean_request_ttft"]
