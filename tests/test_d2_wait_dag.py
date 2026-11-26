from dataclasses import replace

import pytest

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay import (
    CausalReplayError, CausalReplayPlan, ReplayNode, event_node_id, request_node_id,
)
from scripts.d2_wait_dag import replay_request_shifts, replay_wait_savings


def _event(workflow, name, kind, *parents):
    event_id = f"{workflow}:{name}"
    return ReplayNode(
        event_node_id(event_id), workflow, tuple(node.node_id for node in parents), 0.,
        event=RuntimeEvent(event_id, 0., kind, workflow),
    )


def _receipt(request, submit):
    return ReplayNode(
        request_node_id(request), submit.workflow_id, (submit.node_id,), 0.,
        request_id=request,
    )


def _plan(*observations):
    nodes = tuple(node for node, _ in reversed(observations))
    return (
        CausalReplayPlan(
            nodes, {node.request_id: (1, 1) for node in nodes if node.request_id},
            True, 0,
        ),
        {node.node_id: when for node, when in observations},
    )


@pytest.fixture
def serial():
    workflow = "serial"
    start = _event(workflow, "start", Kind.WORKFLOW_START)
    submit_a = _event(workflow, "submit-a", Kind.LLM_SUBMIT, start)
    receipt_a = _receipt("a", submit_a)
    result_a = _event(workflow, "result-a", Kind.LLM_RESULT, receipt_a)
    submit_b = _event(workflow, "submit-b", Kind.LLM_SUBMIT, result_a)
    receipt_b = _receipt("b", submit_b)
    result_b = _event(workflow, "result-b", Kind.LLM_RESULT, receipt_b)
    end = _event(workflow, "end", Kind.WORKFLOW_END, result_b)
    return _plan(
        (start, 101.), (submit_a, 103.), (receipt_a, 115.), (result_a, 117.),
        (submit_b, 123.), (receipt_b, 143.), (result_b, 148.), (end, 151.),
    )


def test_serial_receipt_advances_add(serial):
    plan, observed = serial
    assert replay_wait_savings(plan, observed, {}) == {"serial": 0.}
    assert replay_wait_savings(plan, observed, {"a": 3.25, "b": 5.5}) == {"serial": 8.75}
    assert replay_wait_savings(plan, observed, {"a": 12.}) == {"serial": 12.}


def test_small_advance_survives_large_clock_offset(serial):
    plan, observed = serial
    shifted = {key: when + 1e15 for key, when in observed.items()}
    assert replay_wait_savings(plan, shifted, {"a": .03125}) == {"serial": .03125}


def test_join_switches_to_the_later_reconstructed_branch():
    workflow = "fork"
    start = _event(workflow, "start", Kind.WORKFLOW_START)
    wait = _event(workflow, "wait", Kind.JOIN_WAIT, start)
    submit_a = _event(workflow, "submit-a", Kind.LLM_SUBMIT, start)
    submit_b = _event(workflow, "submit-b", Kind.LLM_SUBMIT, start)
    receipt_a = _receipt("a", submit_a)
    receipt_b = _receipt("b", submit_b)
    return_a = _event(workflow, "return-a", Kind.RETURN, receipt_a)
    return_b = _event(workflow, "return-b", Kind.RETURN, receipt_b)
    join = _event(workflow, "joined", Kind.JOIN_SATISFIED, wait, return_a, return_b)
    end = _event(workflow, "end", Kind.WORKFLOW_END, join)
    plan, observed = _plan(
        (start, 100.), (wait, 109.), (submit_a, 102.), (submit_b, 103.),
        (receipt_a, 120.), (receipt_b, 119.), (return_a, 124.), (return_b, 121.),
        (join, 126.), (end, 129.),
    )

    # Branch A originally returns last. After saving 8 ms it returns at 116,
    # leaving B's return at 121 to determine the JOIN and workflow end.
    assert replay_wait_savings(plan, observed, {"a": 8.}) == {"fork": 3.}
    assert replay_wait_savings(plan, observed, {"b": 4.}) == {"fork": 0.}
    assert replay_wait_savings(plan, observed, {"a": 8., "b": 2.}) == {"fork": 5.}
    assert replay_wait_savings(plan, observed, {"a": 18., "b": 16.}) == {"fork": 15.}
    assert replay_request_shifts(plan, observed, {"a": 8., "b": -5.}) == {"fork": -2.}
    assert replay_request_shifts(plan, observed, {"b": -2.}) == {"fork": 0.}


def test_independent_workflow_retains_its_jct(serial):
    plan, observed = serial
    start = _event("other", "start", Kind.WORKFLOW_START)
    end = _event("other", "end", Kind.WORKFLOW_END, start)
    plan = replace(plan, nodes=(*plan.nodes, end, start))
    observed.update({start.node_id: 110., end.node_id: 130.})
    assert replay_wait_savings(plan, observed, {"a": 3.}) == {"serial": 3., "other": 0.}


def test_requires_observed_receipt_keyed_by_node_id(serial):
    plan, observed = serial
    observed["a"] = observed.pop(request_node_id("a"))
    with pytest.raises(CausalReplayError, match="missing observed timestamp.*request:a"):
        replay_wait_savings(plan, observed, {})


@pytest.mark.parametrize("invalid", [-1., float("inf"), float("nan"), "115", True])
def test_rejects_malformed_observed_times(serial, invalid):
    plan, observed = serial
    observed[request_node_id("a")] = invalid
    with pytest.raises(CausalReplayError, match="timestamp.*finite and nonnegative"):
        replay_wait_savings(plan, observed, {})


def test_rejects_observed_receipt_before_its_submit(serial):
    plan, observed = serial
    observed[request_node_id("a")] = 102.5
    with pytest.raises(CausalReplayError, match="negative observed duration"):
        replay_wait_savings(plan, observed, {})


@pytest.mark.parametrize("invalid", [-1., float("inf"), float("nan"), "5", True])
def test_rejects_malformed_advances(serial, invalid):
    plan, observed = serial
    with pytest.raises(CausalReplayError, match="advance.*finite and nonnegative"):
        replay_wait_savings(plan, observed, {"a": invalid})


def test_rejects_advance_that_would_make_receipt_duration_negative(serial):
    plan, observed = serial
    with pytest.raises(CausalReplayError, match="advance exceeds observed duration"):
        replay_wait_savings(plan, observed, {"a": 12.5})


@pytest.mark.parametrize("unknown", ["missing", request_node_id("a")])
def test_rejects_unknown_requests_even_with_zero_advance(serial, unknown):
    plan, observed = serial
    with pytest.raises(CausalReplayError, match="unknown request"):
        replay_wait_savings(plan, observed, {unknown: 0.})


def test_rejects_cross_workflow_edges(serial):
    plan, observed = serial
    start = _event("other", "start", Kind.WORKFLOW_START)
    receipt = next(node for node in plan.nodes if node.request_id == "a")
    end = _event("other", "end", Kind.WORKFLOW_END, start, receipt)
    plan = replace(plan, nodes=(*plan.nodes, end, start))
    observed.update({start.node_id: 110., end.node_id: 130.})
    with pytest.raises(CausalReplayError, match="cross-workflow edge"):
        replay_wait_savings(plan, observed, {})


def test_rejects_missing_or_cyclic_predecessors(serial):
    plan, observed = serial
    end, *other_nodes = plan.nodes
    with pytest.raises(CausalReplayError, match="unknown replay predecessor"):
        broken_end = replace(end, dependencies=("missing",))
        replay_wait_savings(replace(plan, nodes=(broken_end, *other_nodes)), observed, {})
    with pytest.raises(CausalReplayError, match="cyclic replay dependencies"):
        cyclic_end = replace(end, dependencies=(end.node_id,))
        replay_wait_savings(replace(plan, nodes=(cyclic_end, *other_nodes)), observed, {})
