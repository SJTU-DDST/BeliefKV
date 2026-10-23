from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from beliefkv.control.causal_graph import InvocationState, RuntimeCausalContextGraph
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.delegation_policy import (
    REUSABLE_NESTED_IN_GRAPH_PROFILE, delegation_contract,
)
from beliefkv.runtime.deepagents_adapter import DeepAgentsRuntimeAdapter
from beliefkv.runtime.context_lifecycle import ContextCompactionRecord
from beliefkv.runtime.reusable_subagent_middleware import ReusableSubAgentMiddleware
from beliefkv.runtime.reusable_subagents import (
    ReusableSubagentRegistry, SubagentSessionError, reuse_features,
)
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata
from beliefkv.runtime.sglang_v0520_sessions import NativeRadixSessionLeases
from tests.test_deepagents_adapter import CollectingSink


def reserve(registry, call="c1", handle=None, *, parent="root-context", role="specialist"):
    return registry.reserve(
        call_id=call, invocation_id=f"inv-{call}", owner_context_id=parent,
        role=role, description=f"assignment {call}", agent_id=handle,
    )


def commit(registry, activation):
    _, state = registry.claim(
        activation.call_id, role=activation.role,
        description=f"assignment {activation.call_id}",
        agent_id=activation.handle if activation.resumed else None,
    )
    state["messages"] += [
        HumanMessage(content=f"assignment {activation.call_id}"),
        AIMessage(content=f"evidence {activation.call_id}"),
    ]
    state["_summarization_event"] = {"cursor": activation.call_id}
    state["todos"] = [{"content": "follow up", "status": "pending"}]
    state["guard_forcing_completion"] = False
    registry.note_epoch(activation.invocation_id, activation.initial_epoch + 2)
    registry.complete(activation.call_id, state, "unresolved follow-up")
    return state


def test_private_transcript_survives_physical_cache_loss_and_new_activation():
    registry = ReusableSubagentRegistry("wf")
    first = reserve(registry)
    old = commit(registry, first)
    second = reserve(registry, "c2", first.handle)
    assert second.handle == first.handle and second.context_id == first.context_id
    assert second.invocation_id != first.invocation_id
    assert second.initial_epoch == 3
    _, restored = registry.claim(
        "c2", role="specialist", description="assignment c2", agent_id=first.handle,
    )
    assert restored["messages"] == old["messages"]
    assert restored["messages"] is not old["messages"]
    assert restored["_summarization_event"] == old["_summarization_event"]
    assert restored["todos"] == old["todos"]
    assert "guard_forcing_completion" not in restored


@pytest.mark.parametrize("violation", ("unknown", "cross_root", "parent", "role", "closed", "busy"))
def test_invalid_or_concurrent_resume_is_rejected(violation):
    registry = ReusableSubagentRegistry("wf")
    first = reserve(registry)
    commit(registry, first)
    handle, target, parent, role = first.handle, registry, "root-context", "specialist"
    if violation == "unknown":
        handle = "missing"
    elif violation == "cross_root":
        target = ReusableSubagentRegistry("other")
    elif violation == "parent":
        parent = "unrelated-parent"
    elif violation == "role":
        role = "reviewer"
    elif violation == "closed":
        registry.close(handle, owner_context_id=parent)
    else:
        reserve(registry, "already-active", handle)
    with pytest.raises(SubagentSessionError):
        reserve(target, "c2", handle, parent=parent, role=role)


def test_concurrent_resumes_have_one_writer():
    registry = ReusableSubagentRegistry("wf")
    first = reserve(registry)
    commit(registry, first)

    def attempt(index):
        try:
            return reserve(registry, f"call-{index}", first.handle)
        except SubagentSessionError:
            return None

    with ThreadPoolExecutor(8) as executor:
        results = list(executor.map(attempt, range(8)))
    assert sum(result is not None for result in results) == 1


def test_abort_and_workflow_end_revoke_handles_and_clear_transcripts():
    registry = ReusableSubagentRegistry("wf")
    first = reserve(registry)
    commit(registry, first)
    second = reserve(registry, "c2", first.handle)
    registry.abort(second.call_id)
    assert registry.return_attributes(second.call_id) == {}
    with pytest.raises(SubagentSessionError):
        reserve(registry, "c3", first.handle)
    registry.finish()
    assert all(not session.state for session in registry._sessions.values())
    with pytest.raises(SubagentSessionError):
        reserve(registry, "fresh-after-end")


class ReuseModel(BaseChatModel):
    root: bool = False
    seen: list = Field(default_factory=list)
    close_after: bool = False

    @property
    def _llm_type(self):
        return "reusable-session-cpu-model"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        if self.root:
            reports = [message for message in messages if isinstance(message, ToolMessage)]
            if not reports:
                response = AIMessage(content="", tool_calls=[{
                    "name": "task", "id": "first", "type": "tool_call",
                    "args": {"description": "inspect module", "subagent_type": "specialist"},
                }])
            elif len(reports) == 1:
                handle = reports[0].text.splitlines()[0].split(": ", 1)[1]
                response = AIMessage(content="", tool_calls=[{
                    "name": "task", "id": "second", "type": "tool_call",
                    "args": {
                        "description": "verify with new test evidence",
                        "subagent_type": "specialist", "agent_id": handle,
                    },
                }])
            elif self.close_after and len(reports) == 2:
                handle = reports[1].text.splitlines()[0].split(": ", 1)[1]
                response = AIMessage(content="", tool_calls=[{
                    "name": "close_agent", "id": "close", "type": "tool_call",
                    "args": {"agent_id": handle},
                }])
            else:
                response = AIMessage(content="integrated")
        else:
            response = AIMessage(content=f"private evidence round {len(self.seen)}")
        return ChatResult(generations=[ChatGeneration(message=response)])


class NestedReuseModel(ReuseModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        reports = [message for message in messages if isinstance(message, ToolMessage)]
        follow_up = len([message for message in messages if isinstance(message, HumanMessage)]) > 1
        if not reports:
            response = AIMessage(content="", tool_calls=[{
                "name": "task", "id": "leaf-first", "type": "tool_call",
                "args": {"description": "inspect leaf", "subagent_type": "leaf"},
            }])
        elif follow_up and reports[-1].tool_call_id == "leaf-first":
            handle = reports[-1].text.splitlines()[0].split(": ", 1)[1]
            response = AIMessage(content="", tool_calls=[{
                "name": "task", "id": "leaf-second", "type": "tool_call",
                "args": {
                    "description": "verify leaf", "subagent_type": "leaf", "agent_id": handle,
                },
            }])
        else:
            response = AIMessage(content="specialist synthesis")
        return ChatResult(generations=[ChatGeneration(message=response)])


def graph_fixture(*, close_after=False):
    trace, closed = CollectingSink(), []
    root = BeliefKVRequestMetadata(
        "wf", "root", "root-context", 0, full_prompt_replay_guaranteed=True,
    )
    adapter = DeepAgentsRuntimeAdapter(
        trace, root, reusable_subagents=ReusableSubagentRegistry("wf"),
        native_radix_sessions=NativeRadixSessionLeases(closed.append),
    )
    child_model = ReuseModel()
    child = create_agent(child_model, system_prompt="specialist")
    middleware = ReusableSubAgentMiddleware(
        backend=object(), adapter=adapter,
        subagents=[{"name": "specialist", "description": "test", "runnable": child}],
    )
    root_model = ReuseModel(root=True, close_after=close_after)
    agent = create_agent(root_model, middleware=[middleware])
    adapter.start()
    return agent, adapter, trace, child_model, closed


@pytest.mark.parametrize("configured", (False, True))
def test_reusable_task_preserves_configured_delegation_description_and_role_roster(configured):
    _, adapter, _, _, _ = graph_fixture()
    prompt = (
        "Delegate substantial independent repository work after new test evidence.\n"
        "Available child types:\n{available_agents}"
    )
    middleware = ReusableSubAgentMiddleware(
        backend=object(), adapter=adapter,
        task_description=prompt if configured else None,
        subagents=[{
            "name": "specialist", "description": "Inspect assigned modules.",
            "runnable": create_agent(ReuseModel(), system_prompt="specialist"),
        }],
    )
    tool = next(tool for tool in middleware.tools if tool.name == "task")
    description = tool.description
    assert "specialist: Inspect assigned modules." in description
    assert description.count("specialist: Inspect assigned modules.") == 1
    assert "{available_agents}" not in description
    assert "agent_id" in description and "related follow-up" in description
    assert "Each agent invocation is stateless" not in description
    if configured:
        assert "Delegate substantial independent repository work after new test evidence." in description
    middleware.private_state_keys = frozenset({"private"})
    rebuilt = next(tool for tool in middleware.tools if tool.name == "task")
    assert rebuilt.description == description
    assert "agent_id" in rebuilt.args


@pytest.mark.parametrize("asynchronous", (False, True))
def test_real_graph_fresh_then_resume_has_new_join_and_identical_history(asynchronous):
    import asyncio

    agent, adapter, trace, model, closed = graph_fixture()
    inputs = {"messages": [HumanMessage(content="root-only project snapshot")]}
    config = {"callbacks": [adapter]}
    if asynchronous:
        result = asyncio.run(agent.ainvoke(inputs, config))
    else:
        result = agent.invoke(inputs, config)
    assert result["messages"][-1].text == "integrated"
    assert len(model.seen) == 2
    first_private = model.seen[0][1:]
    assert model.seen[1][1:1 + len(first_private)] == first_private
    assert [message.text for message in model.seen[1] if isinstance(message, HumanMessage)] == [
        "inspect module", "verify with new test evidence",
    ]
    assert "root-only project snapshot" not in repr(model.seen)
    creates = [
        event for event in trace.events if event.kind is Kind.INVOCATION_CREATE
        and event.invocation_id != "root"
    ]
    assert len(creates) == 2
    assert creates[0].context_id == creates[1].context_id
    assert creates[0].agent_instance_id == creates[1].agent_instance_id
    assert creates[0].invocation_id != creates[1].invocation_id
    assert creates[0].join_id != creates[1].join_id
    assert creates[1].context_epoch >= creates[0].context_epoch
    submits = [
        event for event in trace.events if event.kind is Kind.LLM_SUBMIT
        and event.context_id == creates[0].context_id
    ]
    assert submits[1].context_epoch > submits[0].context_epoch
    returns = [event for event in trace.events if event.kind is Kind.RETURN]
    assert len(returns) == 2
    assert all(event.attributes["context_retired"] is False for event in returns)
    graph = RuntimeCausalContextGraph()
    graph.apply_batch(tuple(trace.events))
    assert all(graph.invocations[event.invocation_id].state is InvocationState.DONE for event in creates)
    assert len(graph.contexts[creates[0].context_id].invocation_ids) == 2
    assert all(join.satisfied for join in graph.joins.values())
    assert closed == []
    adapter.finish(outcome="completed", natural_completion=True)
    assert trace.events[-1].attributes["reuse_labels_complete"] is True
    assert not any(session.state for session in adapter.reusable_subagents._sessions.values())


def test_real_graph_can_explicitly_close_idle_handle():
    agent, adapter, trace, _, _ = graph_fixture(close_after=True)
    result = agent.invoke({"messages": [HumanMessage(content="root")]}, {"callbacks": [adapter]})
    assert result["messages"][-1].text == "integrated"
    closes = [event for event in trace.events if event.attributes.get("subagent_context_close")]
    assert len(closes) == 1
    assert all(session.closed for session in adapter.reusable_subagents._sessions.values())


def test_resumed_child_can_resume_its_own_grandchild_with_private_history():
    trace = CollectingSink()
    adapter = DeepAgentsRuntimeAdapter(
        trace, BeliefKVRequestMetadata("wf", "root", "root-context", 0),
        reusable_subagents=ReusableSubagentRegistry("wf"),
    )
    leaf_model = ReuseModel()
    leaf = create_agent(leaf_model)
    child = create_agent(NestedReuseModel(), middleware=[ReusableSubAgentMiddleware(
        backend=object(), adapter=adapter,
        subagents=[{"name": "leaf", "description": "leaf specialist", "runnable": leaf}],
    )])
    root = create_agent(ReuseModel(root=True), middleware=[ReusableSubAgentMiddleware(
        backend=object(), adapter=adapter,
        subagents=[{"name": "specialist", "description": "nested workstream", "runnable": child}],
    )])
    adapter.start()
    result = root.invoke({"messages": [HumanMessage(content="root")]}, {"callbacks": [adapter]})
    assert result["messages"][-1].text == "integrated"
    assert len(leaf_model.seen) == 2
    assert [message.text for message in leaf_model.seen[-1] if isinstance(message, HumanMessage)] == [
        "inspect leaf", "verify leaf",
    ]
    graph = RuntimeCausalContextGraph()
    graph.apply_batch(tuple(trace.events))
    assert len(graph.joins) == 4 and all(join.satisfied for join in graph.joins.values())
    assert sum(len(context.invocation_ids) == 2 for context in graph.contexts.values()) == 2


def test_parallel_identical_assignments_get_distinct_private_sessions():
    from tests.test_deepagents_adapter import QueueToolCallingModel

    trace = CollectingSink()
    adapter = DeepAgentsRuntimeAdapter(
        trace, BeliefKVRequestMetadata("wf", "root", "root-context", 0),
        reusable_subagents=ReusableSubagentRegistry("wf"),
    )
    root_model = QueueToolCallingModel(responses=[
        AIMessage(content="", tool_calls=[{
            "name": "task", "id": call_id, "type": "tool_call",
            "args": {"description": "same question", "subagent_type": "specialist"},
        } for call_id in ("parallel-one", "parallel-two")]),
        AIMessage(content="done"),
    ])
    child_model = ReuseModel()
    agent = create_agent(root_model, middleware=[ReusableSubAgentMiddleware(
        backend=object(), adapter=adapter, subagents=[{
            "name": "specialist", "description": "test",
            "runnable": create_agent(child_model),
        }],
    )])
    adapter.start()
    result = agent.invoke({"messages": [HumanMessage(content="root")]}, {"callbacks": [adapter]})
    assert result["messages"][-1].text == "done"
    assert len(child_model.seen) == 2
    returns = [event for event in trace.events if event.kind is Kind.RETURN]
    assert len(returns) == 2 and all(event.attributes["context_retired"] is False for event in returns)
    assert len({event.context_id for event in returns}) == 2
    graph = RuntimeCausalContextGraph()
    graph.apply_batch(tuple(trace.events))
    assert all(join.satisfied for join in graph.joins.values())


def test_native_session_id_survives_activation_but_compaction_invalidates_it():
    registry = ReusableSubagentRegistry("wf")
    first = reserve(registry)
    closed = []
    leases = NativeRadixSessionLeases(closed.append)
    metadata = BeliefKVRequestMetadata(
        "wf", first.invocation_id, first.context_id, 0, full_prompt_replay_guaranteed=True,
    )
    original = leases.for_request(metadata)
    commit(registry, first)
    second = reserve(registry, "c2", first.handle)
    resumed = replace(metadata, invocation_id=second.invocation_id, context_epoch=second.initial_epoch)
    assert leases.for_request(resumed) == original
    leases.compact("wf", first.context_id, second.initial_epoch + 1)
    assert closed == [original]
    assert leases.for_request(replace(resumed, context_epoch=second.initial_epoch + 1)) != original


def test_first_model_after_resume_can_compact_without_advancing_create_epoch():
    agent, adapter, trace, _, closed = graph_fixture()
    agent.invoke({"messages": [HumanMessage(content="root")]}, {"callbacks": [adapter]})
    handle = next(iter(adapter.reusable_subagents._sessions))
    pending = adapter._declare_task_group("root", "third-round", [{
        "name": "task", "id": "third", "type": "tool_call",
        "args": {
            "description": "continue long history", "subagent_type": "specialist",
            "agent_id": handle,
        },
    }])[0]
    adapter.reusable_subagents.claim(
        "third", role="specialist", description="continue long history", agent_id=handle,
    )
    base = adapter._identities[pending.child_invocation_id].metadata
    previous_session = adapter.native_radix_sessions.for_request(base)
    model_id = uuid4()
    with adapter.stage_context_compaction(ContextCompactionRecord(
        source_message_count=40, retained_message_count=8, summary_chars=512,
        summary_sha256="a" * 64, trigger_tokens=249036, keep_tokens=8192,
    )):
        adapter.on_chat_model_start(
            {}, [[HumanMessage(content="compacted checkpoint")]], run_id=model_id,
            metadata=adapter.invocation_scope_for_call("third"),
        )
    compact = next(event for event in trace.events if event.kind is Kind.CONTEXT_COMPACT)
    assert compact.attributes["previous_context_epoch"] == base.context_epoch
    assert compact.context_epoch == base.context_epoch + 1
    graph = RuntimeCausalContextGraph()
    graph.apply_batch(tuple(trace.events))
    assert graph.contexts[base.context_id].epoch == compact.context_epoch
    assert previous_session in closed
    assert adapter.native_radix_sessions.for_request(
        adapter.metadata_for_model_run(model_id),
    ) != previous_session


def test_rejected_unknown_handle_does_not_leave_a_dangling_join():
    from tests.test_deepagents_adapter import QueueToolCallingModel

    trace = CollectingSink()
    adapter = DeepAgentsRuntimeAdapter(
        trace, BeliefKVRequestMetadata("wf", "root", "root-context", 0),
        reusable_subagents=ReusableSubagentRegistry("wf"),
    )
    model = QueueToolCallingModel(responses=[
        AIMessage(content="", tool_calls=[{
            "name": "task", "id": "invalid", "type": "tool_call",
            "args": {
                "description": "inspect", "subagent_type": "specialist",
                "agent_id": "unknown-handle",
            },
        }]),
        AIMessage(content="done"),
    ])
    agent = create_agent(model, middleware=[ReusableSubAgentMiddleware(
        backend=object(), adapter=adapter, subagents=[{
            "name": "specialist", "description": "test", "runnable": create_agent(ReuseModel()),
        }],
    )])
    adapter.start()
    result = agent.invoke({"messages": [HumanMessage(content="root")]}, {"callbacks": [adapter]})
    assert result["messages"][-1].text == "done"
    assert "Task rejected" in result["messages"][-2].text
    assert not any(event.kind is Kind.JOIN_CREATE for event in trace.events)
    graph = RuntimeCausalContextGraph()
    graph.apply_batch(tuple(trace.events))
    assert not graph.joins


def test_workload_contract_is_dynamic_and_retention_does_not_change_prompt():
    contract = delegation_contract(REUSABLE_NESTED_IN_GRAPH_PROFILE)
    assert contract["root_round_target"] is None
    assert contract["fixed_role_sequence"] is False
    assert contract["physical_retention_affects_prompt"] is False
    assert contract["maximum_spawn_depth"] == 2


def test_comparison_arms_share_every_workload_and_scheduler_setting():
    import json
    from pathlib import Path
    from beliefkv.experiments.subagent_retention import ORDER, environment

    spec = json.loads((Path(__file__).resolve().parents[1]
                       / "configs/migration/reusable_subagent_retention_20261022.json").read_text())
    environments = [environment(spec["common_environment"], arm) for arm in ORDER]
    for result, arm in zip(environments, ORDER):
        assert result["BELIEFKV_SUBAGENT_RETENTION"] == arm
        assert result["BELIEFKV_TOOL_RESTORE_MODE"] == "speculative"
        assert result["BELIEFKV_RESTORE_TIERS"] == "complete"
        assert result["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "0"
        assert result["BELIEFKV_ENABLE_RESIDENT_FIRST"] == "1"
        assert result["BELIEFKV_ENABLE_FRONTIER_RESTORE"] == "0"
        assert result["BELIEFKV_ENABLE_TOOL_CONTINUITY"] == "0"
    common = [{key: value for key, value in result.items()
               if key != "BELIEFKV_SUBAGENT_RETENTION"} for result in environments]
    assert all(result == common[0] for result in common)
    with pytest.raises(ValueError, match="differs"):
        environment({**spec["common_environment"], "ROOT_COUNT": "16"}, "selective")


def test_boundary_semantics_are_bounded_and_do_not_emit_source_text():
    features = reuse_features("fix src/cache.py\n" * 10000, "failing tests remain")
    assert features["unresolved"] and features["implementation"]
    assert len(features["scope_ids"]) <= 128
    assert "src/cache.py" not in repr(features)


@pytest.mark.parametrize("as_model", (False, True))
@pytest.mark.parametrize("status,unresolved,expected", (
    ("complete", [], False),
    ("complete", ["verify concurrent close"], True),
    ("blocked", [], True),
))
def test_structured_completion_fields_override_report_keywords(as_model, status, unresolved, expected):
    from beliefkv.experiments.agent_protocol import ChildCompletion

    completion = ChildCompletion(
        status=status, summary="No unresolved issues; no follow-up required.",
        unresolved=unresolved, confidence="high",
    )
    registry = ReusableSubagentRegistry("wf")
    activation = reserve(registry)
    _, state = registry.claim(
        activation.call_id, role=activation.role,
        description="assignment c1", agent_id=None,
    )
    state["messages"] = [AIMessage(content="private evidence")]
    state["structured_response"] = completion if as_model else completion.model_dump()
    registry.complete(activation.call_id, state, completion.model_dump_json())
    assert registry.return_attributes(activation.call_id)["reuse_features"]["unresolved"] is expected


@pytest.mark.parametrize("report,expected", (
    ("No unresolved issues; no follow-up required.", False),
    ("Remaining: none. Tests passing.", False),
    ("No remaining failing tests.", False),
    ("Follow-up not needed.", False),
    ("Unresolved: []", False),
    ("Tests pass. Remaining concurrency cases need verification.", True),
    ("No failing tests; blocked on unavailable credentials.", True),
    ("The review remains blocked.", True),
))
def test_unstructured_followup_signal_ignores_explicit_negations(report, expected):
    assert reuse_features("review", report)["unresolved"] is expected
