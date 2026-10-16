from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest

pytest.importorskip("deepagents")

from langchain.agents import create_agent
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import PrivateAttr

from beliefkv.experiments.agent_protocol import LoopGuardPolicy, analyze_agent_history
from beliefkv.runtime.langchain_tool_safety import (
    ToolCircuitBreakerMiddleware,
    ToolObservationBudgetMiddleware,
    ToolObservationBudgetPolicy,
    ToolOutcomeStatusMiddleware,
)


RAW_RESULT = "1\n\n[Command succeeded with exit code 0]"


def _request(index: int, *, conversation: str = "child", command: str = "python probe.py"):
    return ToolCallRequest(
        tool_call={"name": "execute", "args": {"command": command}, "id": f"call-{index}"},
        tool=None,
        state={"messages": [HumanMessage(content=conversation, id=conversation)]},
        runtime=SimpleNamespace(config={}),
    )


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("scope", ["autonomous:supervisor", "autonomous:test-analyst"])
def test_identical_results_receive_factual_feedback_without_suppressing_execution(asynchronous, scope):
    now = [0.0]
    executed = []
    censored = []
    events = []
    circuit = ToolCircuitBreakerMiddleware(
        state_epoch=lambda: 0, suppress_repeats=False, scope=scope,
        clock=lambda: now[0],
        audit=SimpleNamespace(emit=lambda event, **fields: events.append((event, fields))),
        censor_observer=censored.append,
    )

    def execute(request):
        executed.append(request.tool_call["id"])
        now[0] += 154.0
        return ToolMessage(
            content=RAW_RESULT, name="execute", tool_call_id=request.tool_call["id"],
        )

    async def aexecute(request):
        return execute(request)

    results = []
    for index in range(3):
        request = _request(index)
        results.append(
            asyncio.run(circuit.awrap_tool_call(request, aexecute))
            if asynchronous else circuit.wrap_tool_call(request, execute)
        )

    assert executed == ["call-0", "call-1", "call-2"]
    assert censored == []
    assert results[0].content == RAW_RESULT
    assert [result.additional_kwargs["beliefkv_identical_result_count"] for result in results] == [1, 2, 3]
    assert results[-1].content.startswith(RAW_RESULT)
    assert "total tool wall time 462.000s" in results[-1].content
    assert "Shell success alone does not establish task success" in results[-1].content
    assert "When the assigned deliverable is supported" in results[-1].content
    assert "to the parent" not in results[-1].content
    assert all(result.status == "success" for result in results)
    assert all(result.additional_kwargs["beliefkv_physical_execution"] for result in results)
    assert all(
        result.additional_kwargs["beliefkv_source_result_sha256"]
        == hashlib.sha256(RAW_RESULT.encode()).hexdigest()
        for result in results
    )
    assert len([event for event, _ in events if event == "agent_tool_repeated_result_feedback"]) == 2


def test_feedback_does_not_turn_numeric_results_into_new_progress():
    circuit = ToolCircuitBreakerMiddleware(
        state_epoch=lambda: 0, suppress_repeats=False, scope="child", audit=None,
    )
    messages = [HumanMessage(content="Reproduce the assigned behavior", id="child")]
    for index in range(3):
        request = _request(index)
        result = circuit.wrap_tool_call(
            request,
            lambda current: ToolMessage(
                content=RAW_RESULT, name="execute", tool_call_id=current.tool_call["id"],
            ),
        )
        messages.extend([AIMessage(content="", tool_calls=[request.tool_call]), result])

    snapshot = analyze_agent_history(messages, LoopGuardPolicy())
    assert snapshot.completed_tool_calls == 3
    assert snapshot.progress_keys == ()
    assert snapshot.reason == "repeated_tool_call"


def test_changed_results_outcomes_conversations_and_file_edits_reset_observations():
    epoch = [0]
    circuit = ToolCircuitBreakerMiddleware(
        state_epoch=lambda: epoch[0], suppress_repeats=False, scope="child", audit=None,
    )

    def invoke(
        index, *, content=RAW_RESULT, status="success", conversation="child",
        command="python probe.py",
    ):
        return circuit.wrap_tool_call(
            _request(index, conversation=conversation, command=command),
            lambda request: ToolMessage(
                content=content, status=status, name="execute",
                tool_call_id=request.tool_call["id"],
            ),
        )

    first = invoke(0)
    repeat = invoke(1)
    changed = invoke(2, content="different diagnostic")
    previous_again = invoke(3)
    other_child = invoke(4, conversation="another-child")
    different_command = invoke(5, command="python different_probe.py")
    different_status = invoke(6, status="error")
    epoch[0] += 1
    edited = invoke(7)
    assert [
        result.additional_kwargs["beliefkv_identical_result_count"]
        for result in (
            first, repeat, changed, previous_again, other_child, different_command,
            different_status, edited,
        )
    ] == [1, 2, 1, 1, 1, 1, 1, 1]


def test_observation_budget_keeps_feedback_and_source_identity_separate():
    circuit = ToolCircuitBreakerMiddleware(
        state_epoch=lambda: 0, suppress_repeats=False, scope="child", audit=None,
    )
    budget = ToolObservationBudgetMiddleware(
        policy=ToolObservationBudgetPolicy(total_chars_per_turn=1024, max_chars_per_result=1024),
        audit=None, scope="child",
    )
    raw = "diagnostic output " * 200

    def execute(request):
        return ToolMessage(content=raw, name="execute", tool_call_id=request.tool_call["id"])

    results = [
        budget.wrap_tool_call(
            _request(index),
            lambda current: circuit.wrap_tool_call(current, execute),
        )
        for index in range(2)
    ]
    assert all(len(result.content) <= 1024 for result in results)
    assert "identical command and visible result 2 times" in results[-1].content
    assert results[-1].content.endswith(results[-1].additional_kwargs["beliefkv_execution_feedback_text"])
    assert results[0].additional_kwargs["beliefkv_source_result_sha256"] == (
        results[-1].additional_kwargs["beliefkv_source_result_sha256"]
    )


@pytest.mark.parametrize("raw_result", (RAW_RESULT, "diagnostic output " * 200))
def test_real_agent_retains_feedback_without_rewriting_the_system_or_earlier_messages(raw_result):
    class RecordingModel(FakeMessagesListChatModel):
        _observed: list = PrivateAttr(default_factory=list)

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, **kwargs):
            self._observed.append(list(messages))
            return super()._generate(messages, **kwargs)

    executed = []

    @tool
    def execute(command: str) -> str:
        """Run the synthetic diagnostic."""
        executed.append(command)
        return raw_result

    calls = [_request(index).tool_call for index in range(2)]
    model = RecordingModel(responses=[
        AIMessage(content="", tool_calls=[calls[0]]),
        AIMessage(content="", tool_calls=[calls[1]]),
        AIMessage(content="Observed a repeated diagnostic; returning the evidence."),
    ])
    circuit = ToolCircuitBreakerMiddleware(
        state_epoch=lambda: 0, suppress_repeats=False, scope="child", audit=None,
    )
    budget = ToolObservationBudgetMiddleware(
        policy=ToolObservationBudgetPolicy(total_chars_per_turn=1024, max_chars_per_result=1024),
        audit=None, scope="child",
    )
    agent = create_agent(
        model=model, tools=[execute],
        middleware=[budget, circuit, ToolOutcomeStatusMiddleware()],
        system_prompt="Answer the delegated reproduction question.",
    )
    result = agent.invoke(
        {"messages": [HumanMessage(content="Reproduce the behavior", id="child")]},
        config={"recursion_limit": 20},
    )
    assert executed == ["python probe.py", "python probe.py"]
    assert model._observed[0][0] == model._observed[-1][0]
    assert model._observed[-1][:len(model._observed[1])] == model._observed[1]
    tool_results = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    if len(raw_result) <= 1024:
        assert tool_results[0].content == raw_result
    else:
        assert "BeliefKV observation truncated" in tool_results[0].content
    assert all(len(message.content) <= 1024 for message in tool_results)
    assert "identical command and visible result 2 times" in tool_results[-1].content
    assert tool_results[-1].content.endswith(
        tool_results[-1].additional_kwargs["beliefkv_execution_feedback_text"]
    )
    assert all(
        message.additional_kwargs["beliefkv_source_result_sha256"]
        == hashlib.sha256(raw_result.encode()).hexdigest()
        for message in tool_results
    )
    assert result["messages"][-1].tool_calls == []
