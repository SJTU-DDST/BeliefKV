from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("deepagents")

from langchain.agents import create_agent
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import PrivateAttr

from beliefkv.runtime.langchain_tool_safety import (
    ToolCircuitBreakerMiddleware,
    ToolObservationBudgetMiddleware,
    ToolObservationBudgetPolicy,
)


ARGUMENTS = {
    "grep": {"pattern": "def update", "path": "/workspace/django"},
    "read_file": {"file_path": "/workspace/django/db/models/base.py"},
    "glob": {"pattern": "**/test_update.py", "path": "/workspace"},
    "ls": {"path": "/workspace/django"},
}


def request(index, name, *, conversation="child"):
    return ToolCallRequest(
        tool_call={"name": name, "args": ARGUMENTS[name], "id": f"call-{index}"},
        tool=None,
        state={"messages": [HumanMessage(content=conversation, id=conversation)]},
        runtime=SimpleNamespace(config={}),
    )


@pytest.mark.parametrize("name", ARGUMENTS)
@pytest.mark.parametrize("asynchronous", (False, True))
def test_read_results_get_history_without_blocking_or_replacing_physical_calls(
    name, asynchronous,
):
    executed, audit, censored = [], [], []
    circuit = ToolCircuitBreakerMiddleware(
        state_epoch=lambda: 0, suppress_repeats=False, scope="child",
        audit=SimpleNamespace(emit=lambda kind, **fields: audit.append((kind, fields))),
        censor_observer=censored.append,
    )

    def invoke(current):
        executed.append(current.tool_call["id"])
        return ToolMessage(
            content="django/db/models/base.py:42: def update",
            name=name, tool_call_id=current.tool_call["id"],
        )

    async def ainvoke(current):
        return invoke(current)

    results = [
        asyncio.run(circuit.awrap_tool_call(request(index, name), ainvoke))
        if asynchronous else circuit.wrap_tool_call(request(index, name), invoke)
        for index in range(3)
    ]
    assert executed == ["call-0", "call-1", "call-2"]
    assert censored == []
    assert [result.tool_call_id for result in results] == executed
    assert all(result.status == "success" for result in results)
    assert results[0].content == "django/db/models/base.py:42: def update"
    assert [result.additional_kwargs["beliefkv_identical_result_count"] for result in results] == [1, 2, 3]
    assert "identical tool call and visible result 3 times" in results[-1].content
    assert "This does not prove files or external state are unchanged" in results[-1].content
    assert "Shell success" not in results[-1].content
    assert [fields["identical_result_count"] for kind, fields in audit
            if kind == "agent_tool_repeated_result_feedback"] == [2, 3]


@pytest.mark.parametrize("changed", ("result", "epoch", "conversation", "outcome"))
def test_read_history_does_not_cross_changed_evidence_or_ownership(changed):
    epoch = [0]
    circuit = ToolCircuitBreakerMiddleware(
        state_epoch=lambda: epoch[0], suppress_repeats=False, scope="child", audit=None,
    )

    def invoke(index, *, content="same result", conversation="child", status="success"):
        return circuit.wrap_tool_call(
            request(index, "grep", conversation=conversation),
            lambda current: ToolMessage(
                content=content, name="grep", status=status,
                tool_call_id=current.tool_call["id"],
            ),
        )

    first, repeated = invoke(0), invoke(1)
    if changed == "epoch":
        epoch[0] += 1
    last = invoke(
        2, content="new result" if changed == "result" else "same result",
        conversation="other-child" if changed == "conversation" else "child",
        status="error" if changed == "outcome" else "success",
    )
    assert [item.additional_kwargs["beliefkv_identical_result_count"]
            for item in (first, repeated, last)] == [1, 2, 1]
    assert last.additional_kwargs["beliefkv_repeated_result_feedback"] is False


def test_real_read_tool_feedback_is_append_only_and_survives_observation_budget():
    class RecordingModel(FakeMessagesListChatModel):
        _observed: list = PrivateAttr(default_factory=list)

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, **kwargs):
            self._observed.append(list(messages))
            return super()._generate(messages, **kwargs)

    executed = []

    @tool
    def grep(pattern: str, path: str) -> str:
        """Search a synthetic source tree."""
        executed.append((pattern, path))
        return "django/db/models/base.py:42: def update\n" * 100

    calls = [request(index, "grep").tool_call for index in range(2)]
    model = RecordingModel(responses=[
        AIMessage(content="", tool_calls=[calls[0]]),
        AIMessage(content="", tool_calls=[calls[1]]),
        AIMessage(content="The observation is unchanged; reporting the finding."),
    ])
    circuit = ToolCircuitBreakerMiddleware(
        state_epoch=lambda: 0, suppress_repeats=False, scope="child", audit=None,
    )
    budget = ToolObservationBudgetMiddleware(
        policy=ToolObservationBudgetPolicy(total_chars_per_turn=1024, max_chars_per_result=1024),
        audit=None, scope="child",
    )
    agent = create_agent(
        model=model, tools=[grep], middleware=[budget, circuit],
        system_prompt="Find the update definition.",
    )
    result = agent.invoke(
        {"messages": [HumanMessage(content="Locate update", id="child")]},
        config={"recursion_limit": 20},
    )
    assert len(executed) == 2
    assert model._observed[-1][:len(model._observed[1])] == model._observed[1]
    tools = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert all(len(message.content) <= 1024 for message in tools)
    assert "identical tool call and visible result 2 times" in tools[-1].content
    assert tools[-1].content.endswith(tools[-1].additional_kwargs["beliefkv_execution_feedback_text"])
    assert tools[-1].additional_kwargs["beliefkv_physical_execution"] is True
