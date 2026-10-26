import asyncio
from types import SimpleNamespace as NS
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware.types import ExtendedModelResponse, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.tools import tool
from langgraph.types import Command
from pydantic import PrivateAttr
import pytest

from beliefkv.experiments.root_delegation_requirement import (
    ROOT_DELEGATION_PRIVATE_KEYS, RootDelegationRequirementMiddleware,
)
from beliefkv.runtime.deepagents_adapter import DeepAgentsRuntimeAdapter
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata
from beliefkv.runtime.subagent_state import strip_private_state_update
from tests.test_child_work_budget import Audit
from tests.test_deepagents_adapter import CollectingSink


class RecordingModel(FakeMessagesListChatModel):
    _choices: list = PrivateAttr(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        self._choices.append(kwargs.get("tool_choice"))
        return self


@pytest.mark.parametrize("asynchronous", [False, True])
def test_root_defers_only_undelegated_final_and_keeps_model_selected_task(asynchronous):
    joined = []
    selected = []

    @tool
    def inspect() -> str:
        """Inspect the repository before choosing an assignment."""
        return "A candidate invariant needs independent review."

    @tool
    def task(description: str) -> str:
        """Delegate a model-selected independent question."""
        selected.append(description)
        joined.append(description)
        return "Independent review: the candidate invariant holds."

    audit = Audit()
    model = RecordingModel(responses=[
        AIMessage(content="", tool_calls=[{"name": "inspect", "args": {}, "id": "inspect"}]),
        AIMessage(content="Candidate final answer before delegation."),
        AIMessage(content="", tool_calls=[{
            "name": "task", "args": {"description": "Review this specific invariant."},
            "id": "chosen-by-model",
        }, {
            "name": "task", "args": {"description": "Validate the independent regression."},
            "id": "second-chosen-by-model",
        }]),
        AIMessage(content="Final answer integrating the independent review."),
    ])
    agent = create_agent(
        model=model, tools=[inspect, task],
        middleware=[RootDelegationRequirementMiddleware(
            adapter=NS(has_completed_join=lambda *, minimum_members: len(joined) >= minimum_members),
            audit=audit,
        )],
    )
    inputs = {"messages": [HumanMessage(content="Complete the assignment as a team.")]}
    result = asyncio.run(agent.ainvoke(inputs)) if asynchronous else agent.invoke(inputs)
    assert result["messages"][-1].content == "Final answer integrating the independent review."
    assert sorted(selected) == [
        "Review this specific invariant.", "Validate the independent regression.",
    ]
    assert model._choices == [None, None, "task", None]
    assert any(
        isinstance(message, AIMessage) and message.content.startswith("Candidate final")
        for message in result["messages"]
    )
    assert [row["event"] for row in audit.rows] == [
        "root_delegation_return_deferred", "root_delegation_required_task_attempt",
    ]


def test_failed_delegations_have_bounded_retries_and_cannot_qualify_as_success():
    @tool
    def task(description: str) -> str:
        """Reject a task without creating a real child."""
        return "Task rejected: unavailable specialist."

    audit = Audit()
    model = RecordingModel(responses=[
        AIMessage(content="Premature final."),
        *[AIMessage(content="", tool_calls=[{
            "name": "task", "args": {"description": "Review the result."}, "id": f"retry-{i}",
        }]) for i in range(2)],
    ])
    agent = create_agent(
        model=model, tools=[task],
        middleware=[RootDelegationRequirementMiddleware(
            adapter=NS(has_completed_join=lambda **_: False), audit=audit,
        )],
    )
    with pytest.raises(RuntimeError, match="required model-selected delegation"):
        agent.invoke({"messages": [HumanMessage(content="Complete the assignment.")]})
    assert model._choices == [None, "task", "task"]
    assert audit.rows[-1]["event"] == "root_delegation_requirement_failed"


def test_completed_join_uses_actual_callback_lifecycle_not_declared_task_calls():
    adapter = DeepAgentsRuntimeAdapter(
        CollectingSink(), BeliefKVRequestMetadata("workflow", "root", "ctx-root", 0),
    )
    adapter.start()
    assert not adapter.has_completed_join()
    assert adapter.completed_join_count() == 0
    pending = adapter._declare_task_group("root", "model", [{
        "id": "child", "args": {
            "subagent_type": "repository-explorer", "description": "Review an invariant.",
        },
    }])[0]
    assert not adapter.has_completed_join()
    adapter._complete_task(pending.tool_call_id, cancelled=True, error=RuntimeError("failed"))
    assert not adapter.has_completed_join()
    pending = adapter._declare_task_group("root", "next-model", [{
        "id": "next-child", "args": {
            "subagent_type": "repository-explorer", "description": "Review another invariant.",
        },
    }])[0]
    adapter._complete_task(pending.tool_call_id, cancelled=False, error=None)
    assert not adapter.has_completed_join()
    pending = adapter._declare_task_group("root", "served-model", [{
        "id": "served-child", "args": {
            "subagent_type": "repository-explorer", "description": "Review actual evidence.",
        },
    }])[0]
    model_run = uuid4()
    adapter.on_chat_model_start(
        {}, [[HumanMessage(content="Review actual evidence.")]], run_id=model_run,
        metadata=adapter.invocation_scope_for_call(pending.tool_call_id),
    )
    adapter.on_llm_end(
        LLMResult(generations=[[ChatGeneration(message=AIMessage(content="Observed report."))]]),
        run_id=model_run,
    )
    adapter._complete_task(pending.tool_call_id, cancelled=False, error=None)
    assert adapter.has_completed_join()
    assert adapter.completed_join_count() == 1
    assert not adapter.has_completed_join(minimum_members=2)
    adapter._complete_task(pending.tool_call_id, cancelled=False, error=None)
    assert adapter.completed_join_count() == 1
    assert not adapter.has_completed_join(pending.child_invocation_id)
    adapter.finish(outcome="completed")


def test_root_requirement_preserves_context_updates_and_isolates_child_state():
    audit = Audit()
    middleware = RootDelegationRequirementMiddleware(
        adapter=NS(has_completed_join=lambda **_: False), audit=audit,
    )
    from langchain.agents.middleware.types import ModelRequest
    request = ModelRequest(
        model=NS(), state={"root_delegation_retry_pending": True},
        messages=[], tools=[NS(name="task")],
    )
    inner = ExtendedModelResponse(
        model_response=ModelResponse(result=[AIMessage(content="Attempted task.")]),
        command=Command(update={"context_epoch": 4}),
    )
    result = middleware.wrap_model_call(request, lambda _request: inner)
    assert result.command.update == {
        "context_epoch": 4, "root_delegation_required_task_attempts": 1,
    }
    assert inner.command.update == {"context_epoch": 4}
    isolated = strip_private_state_update(
        Command(update={
            "messages": ["child report"], "root_delegation_retry_pending": True,
            "root_delegation_required_task_attempts": 2,
        }), ROOT_DELEGATION_PRIVATE_KEYS,
    )
    assert isolated.update == {"messages": ["child report"]}


@pytest.mark.parametrize("retry_pending", [False, True])
def test_parallel_retry_bounds_preserve_tools_and_settings_without_bounding_initial_turn(
    retry_pending,
):
    from langchain.agents.middleware.types import ModelRequest

    middleware = RootDelegationRequirementMiddleware(
        adapter=NS(has_completed_join=lambda **_: False), audit=Audit(),
    )
    tools = [NS(name="task"), NS(name="execute")]
    settings = {"temperature": .4, "extra_body": {"seed": 21, "other": "unchanged"}}
    request = ModelRequest(
        model=NS(), state={"root_delegation_retry_pending": retry_pending},
        messages=[HumanMessage(content="Choose useful independent work.")],
        tools=tools, model_settings=settings,
    )
    updated, attempt = middleware._required_task_request(request)
    assert updated.tools == tools
    assert request.model_settings == settings
    if retry_pending:
        assert attempt == 1
        assert updated.tool_choice == "task"
        assert updated.model_settings == {
            **settings, "parallel_tool_calls": True,
            "extra_body": {
                **settings["extra_body"], "beliefkv_task_fanout": [2, 4],
            },
        }
    else:
        assert updated is request
        assert attempt is None


@pytest.mark.parametrize("field", ["guard_forcing_completion", "protocol_repair_active"])
def test_root_requirement_respects_safety_finalization_and_marks_unmet_contract(field):
    audit = Audit()
    middleware = RootDelegationRequirementMiddleware(
        adapter=NS(has_completed_join=lambda **_: False), audit=audit,
    )
    result = middleware.after_model({
        field: True, "messages": [AIMessage(content="Honest blocked report.")],
    }, None)
    assert result is None
    assert audit.rows[-1]["event"] == "root_delegation_requirement_failed"


@pytest.mark.parametrize("served_children", [0, 1, 2])
def test_parallel_requirement_counts_served_children_in_one_successful_join(served_children):
    adapter = DeepAgentsRuntimeAdapter(
        CollectingSink(), BeliefKVRequestMetadata("workflow", "root", "ctx-root", 0),
    )
    adapter.start()
    for round_index, size in enumerate((1, 1, 2)):
        pending = adapter._declare_task_group("root", f"model-{round_index}", [{
            "id": f"child-{round_index}-{index}", "args": {
                "subagent_type": "repository-explorer",
                "description": f"Review independent property {index}.",
            },
        } for index in range(size)])
        for index, child in enumerate(pending):
            if size == 1 or index < served_children:
                model_run = uuid4()
                adapter.on_chat_model_start(
                    {}, [[HumanMessage(content="Review actual evidence.")]], run_id=model_run,
                    metadata=adapter.invocation_scope_for_call(child.tool_call_id),
                )
                adapter.on_llm_end(LLMResult(generations=[[
                    ChatGeneration(message=AIMessage(content="Observed independent report.")),
                ]]), run_id=model_run)
            assert not adapter.has_completed_join(minimum_members=2)
            adapter._complete_task(child.tool_call_id, cancelled=False, error=None)
        assert adapter.has_completed_join(minimum_members=2) is (
            size == 2 and served_children == 2
        )
    adapter.finish(outcome="completed")


def test_cancelled_parallel_join_does_not_satisfy_requirement():
    adapter = DeepAgentsRuntimeAdapter(
        CollectingSink(), BeliefKVRequestMetadata("workflow", "root", "ctx-root", 0),
    )
    adapter.start()
    pending = adapter._declare_task_group("root", "model", [{
        "id": f"child-{index}", "args": {
            "subagent_type": "repository-explorer", "description": f"Inspect property {index}.",
        },
    } for index in range(2)])
    for index, child in enumerate(pending):
        run = uuid4()
        adapter.on_chat_model_start(
            {}, [[HumanMessage(content="Inspect.")]], run_id=run,
            metadata=adapter.invocation_scope_for_call(child.tool_call_id),
        )
        adapter.on_llm_end(LLMResult(generations=[[
            ChatGeneration(message=AIMessage(content="Report.")),
        ]]), run_id=run)
        adapter._complete_task(
            child.tool_call_id, cancelled=index == 1,
            error=RuntimeError("failed") if index else None,
        )
    assert not adapter.has_completed_join(minimum_members=2)
    adapter.finish(outcome="completed")
