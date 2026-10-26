import time
from types import SimpleNamespace as NS

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind as Kind
from tests.test_agent_working_set_runtime import runtime
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request
from tests.test_workflow_agent_service_runtime import runtime as workflow_runtime


STRATEGIES = ("working_set", "thunderagent", "thunderagent_retained", "thunderagent_workflow")


def waiting_parent(monkeypatch, tmp_path, strategy):
    live = (
        workflow_runtime(monkeypatch, tmp_path, capacity=2000)
        if strategy == "thunderagent_workflow"
        else runtime(monkeypatch, tmp_path, capacity=2000, strategy=strategy)
    )
    live.on_events((
        observed(0, Kind.WORKFLOW_START),
        observed(1, Kind.INVOCATION_CREATE, invocation_id="parent", context_id="parent-context"),
        observed(2, Kind.INVOCATION_CREATE, invocation_id="child", context_id="child-context",
                 parent_invocation_id="parent"),
        observed(3, Kind.JOIN_CREATE, join_id="join", member_invocation_ids=("child",)),
    ))
    first = request("parent-one", "parent", tokens=200, workflow="wf")
    live.register_visible_request(first)
    live.on_prefill_candidate_result(first, admitted=True, result="CONTINUE")
    live.on_batch_completed(NS(reqs=[first]))
    live.on_events((observed(4, Kind.JOIN_WAIT, invocation_id="parent", join_id="join"),))
    return live


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("ending", (Kind.JOIN_SATISFIED, Kind.JOIN_TIMEOUT))
def test_join_release_keeps_the_parent_blocked_until_its_other_tools_finish(
    monkeypatch, tmp_path, strategy, ending,
):
    live = waiting_parent(monkeypatch, tmp_path, strategy)
    try:
        live.on_events((
            observed(5, Kind.TOOL_START, invocation_id="parent",
                     attributes={"tool_run_id": "first"}),
            observed(6, Kind.TOOL_START, invocation_id="parent",
                     attributes={"tool_run_id": "last"}),
            observed(7, ending, join_id="join"),
        ))
        parent = live._agent_admission.policy.agents["parent"]
        assert live.graph.invocations["parent"].state is InvocationState.WAIT_TOOL
        assert parent.blocked and not parent.critical
        live.on_events((observed(
            8, Kind.TOOL_END, invocation_id="parent", attributes={"tool_run_id": "first"},
        ),))
        assert parent.blocked and not parent.critical
        live.on_events((observed(
            9, Kind.TOOL_END, invocation_id="parent", attributes={"tool_run_id": "last"},
        ),))
        assert live.graph.invocations["parent"].state is InvocationState.READY
        assert parent.blocked is None
        assert not parent.critical
    finally:
        live.close()


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("ending", (Kind.JOIN_SATISFIED, Kind.JOIN_TIMEOUT))
def test_join_release_does_not_clear_a_still_running_foreground_child(
    monkeypatch, tmp_path, strategy, ending,
):
    live = waiting_parent(monkeypatch, tmp_path, strategy)
    try:
        live.on_events((
            observed(5, Kind.INVOCATION_CREATE, invocation_id="foreground", context_id="fg-context",
                     parent_invocation_id="parent"),
            observed(6, Kind.CALL, invocation_id="parent", target_invocation_id="foreground"),
            observed(7, ending, join_id="join"),
        ))
        parent = live._agent_admission.policy.agents["parent"]
        assert live.graph.invocations["parent"].state is InvocationState.WAIT_CHILD
        assert parent.blocked and not parent.critical
        live.on_events((observed(8, Kind.RETURN, invocation_id="foreground"),))
        assert live.graph.invocations["parent"].state is InvocationState.READY
        successor = request("parent-two", "parent", tokens=210, workflow="wf")
        live.register_visible_request(successor)
        live._agent_admission.tick(now=time.monotonic() + 5.1)
        assert live._agent_admission.allows(live.visible[successor.rid])
    finally:
        live.close()


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("new_join_pending", (False, True))
def test_an_old_join_completion_cannot_clear_or_reprioritize_a_later_join(
    monkeypatch, tmp_path, strategy, new_join_pending,
):
    live = waiting_parent(monkeypatch, tmp_path, strategy)
    try:
        live.on_events((observed(5, Kind.JOIN_TIMEOUT, join_id="join"),))
        second = request("parent-two", "parent", tokens=210, workflow="wf")
        live.register_visible_request(second)
        live.on_prefill_candidate_result(second, admitted=True, result="CONTINUE")
        live.on_batch_completed(NS(reqs=[second]))
        live.on_events((
            observed(6, Kind.INVOCATION_CREATE, invocation_id="new-child", context_id="new-context",
                     parent_invocation_id="parent"),
            observed(7, Kind.JOIN_CREATE, join_id="new-join", member_invocation_ids=("new-child",)),
            observed(8, Kind.JOIN_WAIT, invocation_id="parent", join_id="new-join"),
        ))
        if not new_join_pending:
            live.on_events((observed(9, Kind.RETURN, invocation_id="new-child"),))
            successor = request("parent-three", "parent", tokens=220, workflow="wf")
            live.register_visible_request(successor)
            live.on_prefill_candidate_result(successor, admitted=True, result="CONTINUE")
            live.on_batch_completed(NS(reqs=[successor]))
        parent = live._agent_admission.policy.agents["parent"]
        assert not parent.critical
        blocked = parent.blocked
        live.on_events((observed(10, Kind.JOIN_SATISFIED, join_id="join"),))
        assert parent.blocked == blocked
        assert not parent.critical
    finally:
        live.close()


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("ending", (Kind.JOIN_SATISFIED, Kind.JOIN_TIMEOUT))
def test_current_join_with_no_remaining_dependency_keeps_existing_resume_priority(
    monkeypatch, tmp_path, strategy, ending,
):
    live = waiting_parent(monkeypatch, tmp_path, strategy)
    try:
        live.on_events((observed(5, ending, join_id="join"),))
        parent = live._agent_admission.policy.agents["parent"]
        assert live.graph.invocations["parent"].state is InvocationState.READY
        assert parent.blocked is None and parent.critical
    finally:
        live.close()
