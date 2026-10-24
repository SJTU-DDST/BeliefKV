from __future__ import annotations

import asyncio
import json

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from beliefkv.experiments.agent_protocol import (
    AgentLoopGuardMiddleware,
    ChildCompletion,
    LoopGuardPolicy,
    WorkflowCompletion,
    analyze_agent_history,
)
from beliefkv.experiments.deepagents_swebench import (
    JsonlAudit,
    _planned_child_completion,
    summarize_agent_control,
)


class BoundFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@tool
def read_file(path: str) -> str:
    """Read a known fixture's evidence."""
    return f"Confirmed fixture evidence from {path}."


def guard(*, schema=ChildCompletion, natural=True, audit=None):
    return AgentLoopGuardMiddleware(
        policy=LoopGuardPolicy(enforce_graph_step_budget=False),
        completion_schema=schema,
        completion_instruction="Return the assigned result honestly.",
        audit=audit,
        scope="child" if schema is ChildCompletion else "parent",
        accept_natural_completion=natural,
    )


@pytest.mark.parametrize("asynchronous", (False, True))
@pytest.mark.parametrize("phase,attempt,allowed", (
    ("RECOVERY", 1, True), ("RECOVERY", 3, True),
    ("RECOVERY", 4, False), ("FINALIZE", 4, False),
))
def test_guard_executes_recovery_tools_but_rejects_terminal_tool_calls(
    asynchronous, phase, attempt, allowed,
):
    middleware = guard()
    call = {
        "name": "read_file", "args": {"path": "/workspace/proof"},
        "id": "proof", "type": "tool_call",
    }
    request = ModelRequest(
        model=BoundFakeModel(responses=[AIMessage(content="unused")]),
        messages=[],
        system_message=SystemMessage(content="Inspect the assigned file."),
        tools=[read_file],
        state={
            "guard_phase": phase, "guard_forcing_completion": True,
            "guard_reason": "repeated_tool_call", "guard_recovery_attempt": attempt,
        },
    )

    def handler(actual):
        assert actual.tools == ([read_file] if allowed else [])
        return ModelResponse(result=[AIMessage(content="", tool_calls=[call])])

    async def async_handler(actual):
        return handler(actual)

    response = (
        asyncio.run(middleware.awrap_model_call(request, async_handler))
        if asynchronous else middleware.wrap_model_call(request, handler)
    )
    assert response.result[0].tool_calls == ([call] if allowed else [])


@pytest.mark.parametrize("schema", (ChildCompletion, WorkflowCompletion))
def test_natural_guard_finalization_requests_an_honest_plain_report(schema):
    middleware = guard(schema=schema)
    request = middleware._guard_recovery_request(ModelRequest(
        model=BoundFakeModel(responses=[AIMessage(content="unused")]),
        messages=[], system_message=SystemMessage(content="Base instructions."),
        tools=[read_file],
        state={
            "guard_phase": "FINALIZE", "guard_forcing_completion": True,
            "guard_reason": "repeated_tool_call", "guard_recovery_attempt": 4,
        },
    ))
    assert request.tools == []
    assert "plain-text assistant report" in request.system_message.text
    assert "Do not emit JSON, tool-call markup" in request.system_message.text
    if schema is ChildCompletion:
        assert "the parent continues the workflow" in request.system_message.text


@pytest.mark.parametrize("command", (
    'echo "Task complete with all requirements fully implemented and validated"',
    'cd /workspace && echo "Task complete with all requirements implemented"',
    "printf '%s\\n' 'Task complete with all requirements fully implemented'",
))
def test_changed_literal_success_prints_do_not_reset_progress(command):
    messages = []
    for index in range(5):
        call = {
            "name": "execute", "args": {"command": f"{command} 'confirmation {index}'"},
            "id": f"print-{index}",
        }
        messages.extend((
            AIMessage(content="", tool_calls=[call]),
            ToolMessage(
                name="execute", tool_call_id=call["id"],
                content=f"Task complete with all requirements fully implemented; confirmation {index}",
            ),
        ))
    snapshot = analyze_agent_history(messages, LoopGuardPolicy())
    assert snapshot.consecutive_no_progress == 5
    assert snapshot.reason == "no_observable_progress"


@pytest.mark.parametrize("command", (
    'echo "$(cat /workspace/result)"',
    "echo /workspace/*.py",
    "echo starting && python -m pytest",
    "echo evidence | python inspect_evidence.py",
    "printf '%s' result > /workspace/result.txt",
))
def test_shell_expansion_commands_and_real_work_keep_their_evidence(command):
    messages = [
        AIMessage(content="", tool_calls=[{
            "name": "execute", "args": {"command": command}, "id": "evidence",
        }]),
        ToolMessage(
            name="execute", tool_call_id="evidence",
            content="A substantive previously unseen result with sufficient evidence detail.",
        ),
    ]
    assert analyze_agent_history(messages, LoopGuardPolicy()).consecutive_no_progress == 0


@pytest.mark.parametrize("makes_progress", (False, True))
def test_recovered_child_returns_partial_evidence_and_parent_keeps_executing(tmp_path, makes_progress):
    executed = []

    @tool
    def probe(path: str) -> str:
        """Read the repeated fixture."""
        executed.append("probe")
        return "unchanged"

    @tool
    def read_file(path: str) -> str:
        """Read independent fixture evidence."""
        executed.append("child_evidence")
        return "Confirmed the implementation path; parent validation is still required."

    @tool
    def verify_parent() -> str:
        """Validate the parent's remaining work."""
        executed.append("parent_validation")
        return "Parent validation completed after integrating the child report."

    audit_path = tmp_path / "audit.jsonl"
    audit = JsonlAudit(audit_path)
    child = create_agent(
        model=BoundFakeModel(responses=[
            *[AIMessage(content="", tool_calls=[{
                "name": "probe", "args": {"path": "/workspace/same"}, "id": f"probe-{i}",
            }]) for i in range(4)],
            *([AIMessage(content="", tool_calls=[{
                "name": "read_file", "args": {"path": "/workspace/implementation"}, "id": "proof",
            }])] if makes_progress else [
                AIMessage(content="", tool_calls=[{
                    "name": "probe", "args": {"path": "/workspace/same"}, "id": f"probe-{i}",
                }]) for i in range(4, 7)
            ]),
            AIMessage(content=(
                "Blocked: implementation path confirmed; parent validation remains."
                if makes_progress else
                "Blocked: repeated probes added no evidence; parent investigation remains."
            )),
        ]),
        tools=[probe, read_file],
        middleware=[guard(audit=audit)],
    )
    child_results = []

    @tool
    def task() -> str:
        """Run one bounded child investigation and return its report."""
        result = child.invoke(
            {"messages": [{"role": "user", "content": "Inspect this workstream."}]},
            config={"recursion_limit": 100},
        )
        child_results.append(result)
        completion = _planned_child_completion(result)
        assert completion.status == "blocked"
        return completion.model_dump_json()

    parent = create_agent(
        model=BoundFakeModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "task", "args": {}, "id": "child"}]),
            AIMessage(content="", tool_calls=[{"name": "verify_parent", "args": {}, "id": "verify"}]),
            AIMessage(content="Integrated the child evidence and completed the parent validation."),
        ]),
        tools=[task, verify_parent],
        middleware=[guard(schema=WorkflowCompletion, audit=audit)],
    )
    try:
        result = parent.invoke(
            {"messages": [{"role": "user", "content": "Coordinate and validate."}]},
            config={"recursion_limit": 100},
        )
    finally:
        audit.close()
    expected_child = ["probe"] * 4 + ["child_evidence"] if makes_progress else ["probe"] * 7
    assert executed == expected_child + ["parent_validation"]
    assert not result.get("guard_ever_intervened")
    assert result["messages"][-1].content.startswith("Integrated")
    events = [json.loads(line) for line in audit_path.read_text().splitlines()]
    returns = [row for row in events if row["event"] == "agent_natural_return"]
    assert [(row["agent_scope"], row["guard_intervened"]) for row in returns] == [
        ("child", True), ("parent", False),
    ]
    finalizations = [
        row for row in events
        if row["event"] == "agent_guard_finalization_attempt"
        and not row["recovery_active"]
    ]
    assert bool(finalizations) is not makes_progress
    assert all(row["agent_scope"] == "child" for row in finalizations)
    assert len(child_results) == 1
    summary = summarize_agent_control(audit_path)
    assert summary["guard_intervened_natural_language_returns"] == 1
