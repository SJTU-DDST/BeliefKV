from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command

from beliefkv.experiments.agent_protocol import LoopGuardPolicy, analyze_agent_history
from beliefkv.runtime.agent_safety import literal_shell_print
from beliefkv.runtime.langchain_tool_safety import ToolCircuitBreakerMiddleware


def circuit(events):
    return ToolCircuitBreakerMiddleware(
        state_epoch=lambda: 0,
        suppress_repeats=False,
        scope="autonomous:supervisor",
        audit=SimpleNamespace(emit=lambda event, **fields: events.append((event, fields))),
    )


def request(name, args):
    return ToolCallRequest(
        tool_call={"name": name, "args": args, "id": "bookkeeping"},
        tool=None,
        state={"messages": [HumanMessage(content="Complete the assigned work.")]},
        runtime=SimpleNamespace(config={}),
    )


@pytest.mark.parametrize("command", (
    'echo "done"',
    "cd /workspace && echo done && printf '%s' finished",
    "echo '' && echo 'all done' && echo 'report ready'",
))
def test_literal_print_chains_are_bookkeeping_instead_of_fresh_progress(command):
    assert literal_shell_print(command)
    messages = []
    for index in range(5):
        call = {"name": "execute", "args": {"command": command}, "id": str(index)}
        messages.extend((
            AIMessage(content="", tool_calls=[call]),
            ToolMessage(content="All task requirements are completed.", tool_call_id=str(index),
                        name="execute"),
        ))
    assert analyze_agent_history(messages, LoopGuardPolicy()).progress_keys == ()


@pytest.mark.parametrize("command", (
    "cd /workspace && echo start && python -m pytest",
    "echo done > /workspace/result.txt",
    "echo done | tee /workspace/result.txt",
    "echo $(cat /workspace/proof)",
    "echo done &&",
    "printf -v evidence '%s' done",
))
def test_nonliteral_or_effectful_commands_are_not_labeled_bookkeeping(command):
    assert not literal_shell_print(command)
    events = []
    req = request("execute", {"command": command})
    result = circuit(events).wrap_tool_call(
        req, lambda current: ToolMessage(
            content="Observed result", tool_call_id=current.tool_call["id"], name="execute",
        ),
    )
    assert result.content == "Observed result"
    assert not events


@pytest.mark.parametrize("asynchronous", (False, True))
def test_literal_feedback_preserves_execution_source_and_tool_status(asynchronous):
    events, executed = [], []
    req = request("execute", {"command": "cd /workspace && echo done && echo finished"})
    source = "done\nfinished\n\n[Command succeeded with exit code 0]"

    def handler(current):
        executed.append(current.tool_call)
        return ToolMessage(content=source, name="execute", tool_call_id="bookkeeping")

    async def ahandler(current):
        return handler(current)

    middleware = circuit(events)
    result = (
        asyncio.run(middleware.awrap_tool_call(req, ahandler))
        if asynchronous else middleware.wrap_tool_call(req, handler)
    )
    assert executed == [req.tool_call]
    assert result.status == "success"
    assert result.content.startswith(source)
    assert "did not inspect files or validate the task" in result.content
    assert "no ordinary tool calls" in result.content
    assert result.additional_kwargs["beliefkv_source_result_sha256"] == hashlib.sha256(
        source.encode()
    ).hexdigest()
    assert result.additional_kwargs["beliefkv_source_result_chars"] == len(source.strip())
    assert result.additional_kwargs["beliefkv_physical_execution"]
    assert not result.additional_kwargs["beliefkv_suppressed_repeat_intent"]
    assert [event for event, _ in events] == ["agent_completion_bookkeeping_feedback"]


@pytest.mark.parametrize("source,status,workspace_changes", (
    ([{"type": "text", "text": "done"}], "success", False),
    ("The command failed.", "error", False),
    ("done", "success", True),
))
def test_print_feedback_leaves_nontext_failures_and_real_changes_unclassified(
    source, status, workspace_changes,
):
    epoch, events, executed = [0], [], []
    middleware = ToolCircuitBreakerMiddleware(
        state_epoch=lambda: epoch[0], scope="edge", suppress_repeats=False,
        audit=SimpleNamespace(emit=lambda event, **fields: events.append((event, fields))),
    )
    req = request("execute", {"command": "echo done"})

    def handler(current):
        executed.append(current.tool_call)
        epoch[0] += int(workspace_changes)
        return ToolMessage(content=source, status=status, name="execute",
                           tool_call_id="bookkeeping")

    result = middleware.wrap_tool_call(req, handler)
    assert executed == [req.tool_call]
    assert result.content == source
    assert result.status == status
    assert not result.additional_kwargs.get("beliefkv_completion_bookkeeping_feedback")
    assert not events


@pytest.mark.parametrize("asynchronous", (False, True))
@pytest.mark.parametrize("completed", (False, True))
def test_todo_command_feedback_preserves_state_and_routing(asynchronous, completed):
    events = []
    todos = [{"content": "Validate the patch", "status": "completed" if completed else "in_progress"}]
    req = request("write_todos", {"todos": todos})
    original = Command(
        update={
            "todos": todos, "other_state": "preserved",
            "messages": [ToolMessage(content="Updated todo list.", tool_call_id="bookkeeping")],
        },
        goto="continue_after_tool",
    )

    def handler(current):
        assert current is req
        return original

    async def ahandler(current):
        return handler(current)

    middleware = circuit(events)
    result = (
        asyncio.run(middleware.awrap_tool_call(req, ahandler))
        if asynchronous else middleware.wrap_tool_call(req, handler)
    )
    assert result.goto == original.goto
    assert result.update["todos"] is todos
    assert result.update["other_state"] == "preserved"
    assert original.update["messages"][0].content == "Updated todo list."
    message = result.update["messages"][0]
    assert ("final report" in message.content) is completed
    assert ("which continues the workflow" in message.content) is completed
    assert message.status == "success"
    assert bool(events) is completed


class BoundFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.mark.parametrize("asynchronous", (False, True))
def test_native_agent_returns_after_todo_feedback_without_forced_completion(asynchronous):
    events, executed = [], []

    @tool
    def inspect_evidence() -> str:
        """Inspect the assigned implementation."""
        executed.append("inspect")
        return "The requested implementation and focused validation are present."

    agent = create_agent(
        model=BoundFakeModel(responses=[
            AIMessage(content="", tool_calls=[{
                "name": "inspect_evidence", "args": {}, "id": "inspect",
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "write_todos", "args": {"todos": [
                    {"content": "Inspect implementation evidence", "status": "completed"},
                ]}, "id": "bookkeeping",
            }]),
            AIMessage(content="Implementation inspected; the parent can integrate this evidence."),
        ]),
        tools=[inspect_evidence],
        middleware=[TodoListMiddleware(), circuit(events)],
    )
    inputs = {"messages": [HumanMessage(content="Inspect the implementation.")]}
    result = asyncio.run(agent.ainvoke(inputs)) if asynchronous else agent.invoke(inputs)
    messages = result["messages"]
    assert executed == ["inspect"]
    assert result["todos"][0]["status"] == "completed"
    todo = next(message for message in messages
                if isinstance(message, ToolMessage) and message.tool_call_id == "bookkeeping")
    assert "the parent, which continues the workflow" in todo.content
    assert not messages[-1].tool_calls
    assert messages[-1].content.startswith("Implementation inspected")
    assert not result.get("guard_forcing_completion")
    assert [event for event, _ in events] == ["agent_completion_bookkeeping_feedback"]
