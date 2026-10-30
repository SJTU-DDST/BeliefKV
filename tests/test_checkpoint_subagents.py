import asyncio
from copy import deepcopy
import json
import threading

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError
from langgraph.types import Command
import pytest

from beliefkv.control.causal_graph import RuntimeCausalContextGraph
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.checkpoint_subagents import (
    CheckpointSubAgentMiddleware, CheckpointTaskSchema, ReadOnlyCheckpointTaskSchema,
    ParallelCheckpointSchema, ReadOnlyParallelCheckpointSchema,
    _parallel_reports,
)
from beliefkv.runtime.deepagents_adapter import DeepAgentsRuntimeAdapter
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata
from tests.test_child_work_budget import ToolModel
from tests.test_deepagents_adapter import CollectingSink


def assignment():
    return {
        "description": "Check the serialisation invariant.",
        "subagent_type": "checker", "known_paths": ["/workspace/src/model.py"],
        "existing_evidence": "The parent observed one payload with a missing field.",
        "finish_condition": "Return the field mapping or one concrete blocking observation.",
        "write_paths": [],
    }


@pytest.mark.parametrize("asynchronous", (False, True))
@pytest.mark.parametrize("invalid_first", (False, True))
def test_real_task_schema_preserves_parallel_callbacks_and_rejects_phantom_children(
    asynchronous, invalid_first,
):
    seen = []
    child = create_agent(model=ToolModel(responses=[AIMessage(content="Bounded child evidence.")]))
    observed_child = RunnableLambda(
        lambda state: (seen.append(state["messages"][0].content), state)[1]
    ) | child
    middleware = CheckpointSubAgentMiddleware(
        backend=object(), private_state_keys=frozenset({"child_private"}),
        subagents=[{"name": "checker", "description": "Check one invariant.", "runnable": observed_child}],
    )
    # The base framework rebuilds its task tool when graph-private keys change.
    middleware.private_state_keys = frozenset({"child_work_model_calls"})
    assert middleware.tools[0].args_schema is CheckpointTaskSchema
    parameters = convert_to_openai_tool(middleware.tools[0])["function"]["parameters"]
    assert set(parameters["required"]) == set(assignment())
    assert not {"runtime", "_func", "_coroutine"} & parameters["properties"].keys()
    calls = [{
        "name": "task", "id": f"valid-{index}", "args": assignment(),
    } for index in range(2)]
    responses = [AIMessage(content="", tool_calls=calls), AIMessage(content="Integrated evidence.")]
    if invalid_first:
        missing = assignment()
        missing.pop("finish_condition")
        responses.insert(0, AIMessage(content="", tool_calls=[
            {"name": "task", "id": "invalid", "args": missing},
        ]))
    agent = create_agent(model=ToolModel(responses=responses), middleware=[middleware])
    trace = CollectingSink()
    adapter = DeepAgentsRuntimeAdapter(
        trace, BeliefKVRequestMetadata("w", "root", "ctx-root", 0),
        task_arguments_schema=CheckpointTaskSchema,
    )
    adapter.start()
    inputs, config = {"messages": [HumanMessage(content="Check two independent properties.")]}, {
        "callbacks": [adapter],
    }
    result = (
        asyncio.run(agent.ainvoke(inputs, config=config)) if asynchronous
        else agent.invoke(inputs, config=config)
    )
    adapter.finish(outcome="completed")
    assert result["messages"][-1].content == "Integrated evidence."
    graph = RuntimeCausalContextGraph()
    for event in trace.events:
        graph.apply(event)
    creates = [event for event in trace.events if event.kind is Kind.INVOCATION_CREATE]
    joins = [event for event in trace.events if event.kind is Kind.JOIN_CREATE]
    assert len(creates) == 3
    assert len(joins) == 1 and len(joins[0].member_invocation_ids) == 2
    assert len([event for event in trace.events if event.kind is Kind.LLM_RESULT]) == (
        5 if invalid_first else 4
    )
    assert len([event for event in trace.events if event.kind is Kind.RETURN]) == 3
    assert len(seen) == 2
    assert all(
        text.count("Question:\n") == 1
        and assignment()["finish_condition"] in text
        and "/workspace/src/model.py" in text for text in seen
    )


def test_checkpoint_input_is_explicit_and_does_not_mutate_parent_arguments():
    payload = assignment()
    before = deepcopy(payload)
    parsed = CheckpointTaskSchema.model_validate(payload)
    description = parsed.child_description()
    assert payload == before
    assert parsed.known_paths == ["src/model.py"]
    assert "/workspace/src/model.py" in description
    assert payload["existing_evidence"] in description and payload["finish_condition"] in description
    assert "Write scope:\nread-only" in description
    assert "PARTIAL:/BLOCKED:" in description


@pytest.mark.parametrize("variant", (
    "missing_evidence", "blank_question", "blank_finish", "traversal",
    "foreign_absolute_path", "duplicate_path", "undeclared_write", "three_write_files",
))
def test_invalid_checkpoint_does_not_silently_become_an_unbounded_assignment(variant):
    payload = assignment()
    if variant == "missing_evidence":
        payload.pop("existing_evidence")
    elif variant == "blank_question":
        payload["description"] = "  "
    elif variant == "blank_finish":
        payload["finish_condition"] = "  "
    elif variant == "traversal":
        payload["known_paths"] = ["../elsewhere.py"]
    elif variant == "foreign_absolute_path":
        payload["known_paths"] = ["/other/src/model.py"]
    elif variant == "duplicate_path":
        payload["known_paths"] = ["src/model.py", "/workspace/src/model.py"]
    elif variant == "undeclared_write":
        payload["write_paths"] = ["src/other.py"]
    else:
        payload["known_paths"] = payload["write_paths"] = ["a.py", "b.py", "c.py"]
    with pytest.raises(ValidationError):
        CheckpointTaskSchema.model_validate(payload)


def test_read_only_workflow_schema_rejects_edit_assignments():
    payload = assignment()
    assert ReadOnlyCheckpointTaskSchema.model_validate(payload).write_paths == []
    payload["write_paths"] = ["src/model.py"]
    with pytest.raises(ValidationError):
        ReadOnlyCheckpointTaskSchema.model_validate(payload)


@pytest.mark.parametrize("asynchronous", (False, True))
def test_parallel_api_runs_real_children_concurrently_with_one_parent_tool_result(asynchronous):
    seen, barrier = [], threading.Barrier(2)
    def observe(state):
        seen.append(state["messages"][0].content)
        barrier.wait(timeout=5.)
        return state
    child = RunnableLambda(observe) | create_agent(
        model=ToolModel(responses=[AIMessage(content="Observed child evidence.")]),
    )
    middleware = CheckpointSubAgentMiddleware(
        backend=object(), private_state_keys=frozenset({"child_work_model_calls"}),
        subagents=[{"name": "checker", "description": "One evidence checkpoint.", "runnable": child}],
    )
    middleware.private_state_keys = frozenset({"child_work_model_calls"})
    assert [tool.name for tool in middleware.tools] == ["task", "parallel_tasks"]
    choices = [assignment(), {**assignment(), "description": "Check an independent representation."}]
    model = ToolModel(responses=[
        AIMessage(content="", tool_calls=[{
            "name": "parallel_tasks", "id": "parallel-call",
            "args": {"assignments": choices},
        }]),
        AIMessage(content="Integrated both reports."),
    ])
    adapter = DeepAgentsRuntimeAdapter(
        CollectingSink(), BeliefKVRequestMetadata("w", "root", "ctx-root", 0),
        task_arguments_schema=CheckpointTaskSchema,
        parallel_task_arguments_schema=ParallelCheckpointSchema,
    )
    agent = create_agent(model=model, middleware=[middleware])
    adapter.start()
    inputs, config = {"messages": [HumanMessage(content="Check independent properties.")]}, {
        "callbacks": [adapter],
    }
    result = (
        asyncio.run(agent.ainvoke(inputs, config=config)) if asynchronous
        else agent.invoke(inputs, config=config)
    )
    adapter.finish(outcome="completed")
    assert len(seen) == 2
    reports = [message for message in result["messages"] if getattr(message, "tool_call_id", "") == "parallel-call"]
    assert len(reports) == 1
    parsed = json.loads(reports[0].content)["checkpoint_reports"]
    assert len(parsed) == 2 and all(report["status"] == "returned" for report in parsed)
    graph = RuntimeCausalContextGraph()
    for event in adapter.trace_sink.events:
        graph.apply(event)
    joins = [event for event in adapter.trace_sink.events if event.kind is Kind.JOIN_CREATE]
    assert len(joins) == 1 and len(joins[0].member_invocation_ids) == 2
    assert len([event for event in adapter.trace_sink.events if event.kind is Kind.LLM_RESULT]) == 4
    assert adapter.has_completed_join(minimum_members=2)
    children = [
        event for event in adapter.trace_sink.events
        if event.kind is Kind.INVOCATION_CREATE and event.invocation_id != "root"
    ]
    assert len(children) == 2
    assert {event.attributes["parallel_member_index"] for event in children} == {0, 1}
    assert all(event.attributes["parallel_source_tool_call_id"] == "parallel-call" for event in children)


@pytest.mark.parametrize("variant", ("singleton", "overlapping_writes", "duplicate", "read_only_write"))
def test_parallel_api_rejects_invalid_groups_before_physical_dependency_declaration(variant):
    choices = [assignment(), {**assignment(), "description": "Check a distinct property."}]
    schema = ParallelCheckpointSchema
    if variant == "singleton":
        choices.pop()
    elif variant == "duplicate":
        choices[1] = assignment()
    elif variant == "read_only_write":
        schema = ReadOnlyParallelCheckpointSchema
        choices[0]["write_paths"] = ["src/model.py"]
    else:
        for choice in choices:
            choice["write_paths"] = ["src/model.py"]
    adapter = DeepAgentsRuntimeAdapter(
        CollectingSink(), BeliefKVRequestMetadata("w", "root", "ctx-root", 0),
        task_arguments_schema=CheckpointTaskSchema,
        parallel_task_arguments_schema=schema,
    )
    call = {"name": "parallel_tasks", "id": "group", "args": {"assignments": choices}}
    assert adapter._executable_task_calls(call) == []
    with pytest.raises(ValidationError):
        schema.model_validate(call["args"])


def test_parallel_reports_preserve_native_file_updates_deletions_and_member_errors():
    from deepagents.middleware.filesystem import _file_data_reducer

    choices = [CheckpointTaskSchema.model_validate(assignment()) for _ in range(2)]
    first = Command(update={
        "messages": [ToolMessage("Evidence A", tool_call_id="a")],
        "files": {"remove": None, "new-a": {"content": ["A"]}},
    })
    second = Command(update={
        "messages": [ToolMessage("Evidence B", tool_call_id="b")],
        "files": {"new-b": {"content": ["B"]}},
    })
    combined = _parallel_reports(choices, [first, second], "group")
    assert _file_data_reducer(
        {"remove": {"content": ["old"]}, "keep": {"content": ["kept"]}},
        combined.update["files"],
    ) == {
        "keep": {"content": ["kept"]},
        "new-a": {"content": ["A"]}, "new-b": {"content": ["B"]},
    }
    assert combined.update["messages"][0].tool_call_id == "group"
    failed = _parallel_reports(choices, [first, RuntimeError("member failed")], "failed-group")
    message = failed.update["messages"][0]
    assert message.status == "error"
    assert [item["status"] for item in json.loads(message.content)["checkpoint_reports"]] == [
        "returned", "error",
    ]
