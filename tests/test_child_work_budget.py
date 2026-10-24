import asyncio
from types import SimpleNamespace as NS

from langchain.agents import create_agent
from langchain.agents.middleware.types import ExtendedModelResponse, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command
import pytest

from beliefkv.experiments.child_work_budget import ChildWorkBudgetMiddleware


class Audit:
    def __init__(self):
        self.rows = []

    def emit(self, event, **fields):
        self.rows.append({"event": event, **fields})


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def tool_message(index):
    return AIMessage(content="", tool_calls=[{
        "name": "evidence", "args": {"item": str(index)}, "id": f"call-{index}",
    }])


@pytest.mark.parametrize("asynchronous", [False, True])
def test_real_agent_can_finish_pending_work_after_soft_target_without_truncation(asynchronous):
    @tool
    def evidence(item: str) -> str:
        """Return observed repository evidence."""
        return "Observed evidence " + item

    audit = Audit()
    middleware = ChildWorkBudgetMiddleware(
        warning_calls=2, target_calls=3, audit=audit, scope="child",
    )
    report = "PARTIAL: The assigned invariant is reproduced; integration remains with the parent."
    agent = create_agent(
        model=ToolModel(responses=[
            tool_message(1), tool_message(2), tool_message(3), tool_message(4),
            AIMessage(content=report),
        ]),
        tools=[evidence], middleware=[middleware],
    )
    inputs = {"messages": [HumanMessage(content="Investigate one invariant.")]}
    result = asyncio.run(agent.ainvoke(inputs)) if asynchronous else agent.invoke(inputs)
    assert result["messages"][-1].content == report
    tools = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert len(tools) == 4
    assert tools[0].content == "Observed evidence 1"
    assert tools[3].content == "Observed evidence 4"
    assert "Aim to return" in tools[1].content
    assert "soft work target has been reached" in tools[2].content
    notices = [row for row in audit.rows if row["event"] == "child_work_budget_notice"]
    assert [row["model_calls"] for row in notices] == [2, 3]
    returns = [row for row in audit.rows if row["event"] == "child_work_budget_return"]
    assert returns == [{
        "event": "child_work_budget_return", "scope": "child",
        "model_calls": 5, "declared_status": "partial", "forced": False,
    }]


def test_parallel_tool_results_receive_only_one_persisted_notice():
    audit = Audit()
    middleware = ChildWorkBudgetMiddleware(
        warning_calls=2, target_calls=3, audit=audit, scope="child",
    )
    state = {"child_work_model_calls": 1}
    original = AIMessage(content="", tool_calls=[
        {"name": "inspect", "id": "a", "args": {}},
        {"name": "inspect", "id": "b", "args": {}},
    ])
    request = NS(state=state)
    response = middleware.wrap_model_call(
        request, lambda _request: ModelResponse(result=[original]),
    )
    assert request.state == {"child_work_model_calls": 1}
    state = {**state, **response.command.update}
    outputs = []
    for call_id in ("b", "a"):
        message = ToolMessage(content="Actual result", tool_call_id=call_id)
        wrapped = middleware.wrap_tool_call(
            NS(state=state, tool_call={"id": call_id}), lambda _request: message,
        )
        outputs.append(wrapped.content)
        assert message.content == "Actual result"
    assert sum("DELEGATED WORK BUDGET" in content for content in outputs) == 1
    assert len(audit.rows) == 1


def test_counter_merge_preserves_compaction_and_routing_from_inner_model_middleware():
    middleware = ChildWorkBudgetMiddleware(
        warning_calls=2, target_calls=3, audit=Audit(), scope="child",
    )
    event = {"context_epoch": 4, "summary": "preserved checkpoint"}
    inner = ExtendedModelResponse(
        model_response=ModelResponse(result=[tool_message(1)]),
        command=Command(update={"_summarization_event": event}, goto="tools"),
    )
    result = middleware.wrap_model_call(NS(state={}), lambda _request: inner)
    assert result.command.update == {
        "_summarization_event": event,
        "child_work_model_calls": 1, "child_work_notice": None,
    }
    assert result.command.goto == "tools"
    assert inner.command.update == {"_summarization_event": event}


def test_tool_command_preserves_private_updates_and_structured_content():
    middleware = ChildWorkBudgetMiddleware(
        warning_calls=2, target_calls=3, audit=Audit(), scope="child",
    )
    message = ToolMessage(
        content=[{"type": "text", "text": "Nested child report"}],
        tool_call_id="nested", status="error",
    )
    original = Command(update={"messages": [message], "other_state": 7}, goto="model")
    result = middleware.wrap_tool_call(
        NS(state={"child_work_notice": (3, "nested")}, tool_call={"id": "nested"}),
        lambda _request: original,
    )
    assert result.update["other_state"] == 7 and result.goto == "model"
    assert result.update["messages"][0].status == "error"
    assert result.update["messages"][0].content[0] == message.content[0]
    assert len(result.update["messages"][0].content) == 2
    assert len(message.content) == 1


@pytest.mark.parametrize("warning,target", [(0, 32), (32, 32), (40, 32), (True, 32)])
def test_invalid_soft_budget_is_rejected(warning, target):
    with pytest.raises(ValueError, match="warning"):
        ChildWorkBudgetMiddleware(
            warning_calls=warning, target_calls=target, audit=Audit(), scope="child",
        )
