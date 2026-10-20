import gzip
import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from beliefkv.control.causal_graph import InvocationState, RuntimeCausalContextGraph
from beliefkv.core.events import RuntimeEventKind
from beliefkv.experiments.agent_protocol import ActivationDeadline, LoopGuardPolicy
from beliefkv.experiments.delegation_policy import (
    DYNAMIC_NESTED_IN_GRAPH_PROFILE, NESTED_COORDINATOR_ROLE,
    NESTED_IN_GRAPH_PROFILE, delegation_contract,
)
from beliefkv.experiments import deepagents_swebench as harness
from beliefkv.runtime.deepagents_adapter import DeepAgentsRuntimeAdapter
from beliefkv.runtime.event_channel import QueuedRuntimeEventSink
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata
from beliefkv.runtime.sglang_v0520_sessions import NativeRadixSessionLeases
from tests.test_deepagents_adapter import CollectingSink


class NestedFixtureModel(BaseChatModel):
    _requests: list = PrivateAttr(default_factory=list)
    _lock: object = PrivateAttr(default_factory=threading.Lock)
    _adapter: object = PrivateAttr(default=None)
    _sessions: object = PrivateAttr(default=None)
    _closed: list = PrivateAttr(default_factory=list)
    _dynamic: bool = PrivateAttr(default=False)

    @property
    def _llm_type(self):
        return "nested-delegation-cpu-fixture"

    def get_num_tokens_from_messages(self, messages, tools=None):
        return sum(len(str(message.content)) // 4 + 1 for message in messages)

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self.bind(
            fixture_tool_names=[getattr(tool, "name", str(tool)) for tool in tools],
            fixture_tool_choice=tool_choice, **kwargs,
        )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        system = "\n".join(
            str(message.content) for message in messages if isinstance(message, SystemMessage)
        )
        role = (
            "root" if (
                "FOUR distinct delegation phases" in system
                or "DYNAMIC MULTI-ROUND DELEGATION" in system
            ) else
            "coordinator" if "Coordinate only the workstream assigned" in system else
            "direct" if "BOUNDED NESTED DELEGATION" in system else "leaf"
        )
        prior = [message for message in messages if isinstance(message, AIMessage)]
        assignment = next(
            (str(message.content) for message in messages if isinstance(message, HumanMessage)),
            "fixture",
        )
        metadata = self._adapter.metadata_for_model_run(run_manager.run_id)
        session = self._sessions.for_request(metadata)
        assert session not in self._closed
        with self._lock:
            self._requests.append({
                "role": role, "first": not prior,
                "invocation_id": metadata.invocation_id,
                "tools": kwargs.get("fixture_tool_names", []),
                "tool_choice": kwargs.get("fixture_tool_choice"),
                "bounds": (kwargs.get("extra_body") or {}).get("beliefkv_task_fanout"),
                "root_source_present": "ROOT_SOURCE_ONLY_SENTINEL" in system,
            })

        def call(name, args):
            return {"name": name, "args": args, "id": f"fixture-{uuid4().hex}"}

        if role == "root":
            rounds = sum(any(call["name"] == "task" for call in message.tool_calls)
                         for message in prior)
            if self._dynamic and not prior:
                calls = [call("write_todos", {"todos": [
                    {"content": "Investigate the actual assignment.", "status": "in_progress"},
                ]})]
            else:
                calls = [] if rounds >= 4 else [
                    call("task", {
                        "subagent_type": (
                            "repository-explorer" if self._dynamic
                            else NESTED_COORDINATOR_ROLE
                        ),
                        "description": f"Evidence {rounds + 1}: coordinate two independent invariants.",
                    }),
                ]
                if calls and (not self._dynamic or rounds != 1):
                    calls.append(call("task", {
                        "subagent_type": "test-analyst",
                        "description": f"Evidence {rounds + 1}: inspect disjoint compatibility evidence.",
                    }))
        elif not prior and (
            role == "coordinator"
            or role == "direct" and "coordinate two independent invariants" in assignment
        ):
            calls = [
                call("task", {
                    "subagent_type": name,
                    "description": f"{assignment} Independent question {index}.",
                }) for index, name in enumerate(("repository-explorer", "test-analyst"))
            ]
        elif not any(
            item["name"] == "announce_completion_intent"
            for message in prior for item in message.tool_calls
        ):
            calls = [call("announce_completion_intent", {"estimated_final_report_tokens": 32})]
        else:
            calls = []
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content="" if calls else f"Controlled {role} fixture report: {assignment}",
            tool_calls=calls,
        ))])


def configuration(tmp_path, profile=NESTED_IN_GRAPH_PROFILE):
    return harness.DeepAgentsExperimentConfig(
        mode="autonomous", base_url="http://localhost:1/v1", model="fixture",
        output_dir=tmp_path / "output", workload_manifest=tmp_path / "manifest.json",
        docker_image="fixture:unused", subagent_fanout_profile=profile,
        child_final_report_shadow=True, completion_gate_enabled=False,
        tool_circuit_breaker_enabled=False,
        loop_guard=LoopGuardPolicy(
            enforce_semantic_guard=False, enforce_soft_graph_budget=False,
        ),
    )


@pytest.mark.parametrize("deliver_control", [False, True])
@pytest.mark.parametrize("profile,source_context", [
    (NESTED_IN_GRAPH_PROFILE, False),
    (DYNAMIC_NESTED_IN_GRAPH_PROFILE, False),
    (DYNAMIC_NESTED_IN_GRAPH_PROFILE, True),
])
def test_real_nested_harness_preserves_direct_join_ownership_and_sessions(
    tmp_path, monkeypatch, deliver_control, profile, source_context,
):
    audit = harness.JsonlAudit(tmp_path / "audit.jsonl")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backend = harness.DockerWorkspaceBackend(
        workspace, image="fixture:unused", audit=audit, support_dir=None,
    )
    trace, control, closed = CollectingSink(), CollectingSink(), []
    queued = QueuedRuntimeEventSink(control) if deliver_control else None
    sessions = NativeRadixSessionLeases(closed.append)
    adapter = DeepAgentsRuntimeAdapter(
        trace, BeliefKVRequestMetadata(
            "workflow", "root", "ctx-root", 0, full_prompt_replay_guaranteed=True,
        ),
        control_sink=queued, native_radix_sessions=sessions,
    )
    model = NestedFixtureModel()
    model._dynamic = profile == DYNAMIC_NESTED_IN_GRAPH_PROFILE
    model._adapter, model._sessions, model._closed = adapter, sessions, closed
    monkeypatch.setattr(harness, "_model", lambda *args: model)
    monkeypatch.setattr(harness, "make_summary_model", lambda model, **kwargs: model)
    deadline = SimpleNamespace(deadline=ActivationDeadline(), register_model=lambda model: model)
    workload = harness.SweBenchWorkload(
        instance_id="fixture-1", repo="fixture/repo", base_commit="abc",
        problem_statement="Controlled nested delegation.", difficulty="unknown",
    )
    if source_context:
        from dataclasses import replace
        from beliefkv.experiments.long_repository_tasks import FrozenRepositoryContext

        path = tmp_path / "root-source.gz"
        raw = gzip.compress(b"ROOT_SOURCE_ONLY_SENTINEL\n" * 100, mtime=0)
        path.write_bytes(raw)
        workload = replace(
            workload, task_kind="locobench_agent",
            frozen_repository_context=FrozenRepositoryContext(
                path, hashlib.sha256(raw).hexdigest(), 120000, 1,
            ),
        )
    adapter.start()
    try:
        agent = harness._build_autonomous_agent(
            configuration(tmp_path, profile), workload, backend, adapter, deadline,
        )
        result = agent.invoke(
            {"messages": [HumanMessage(content="Run the four controlled phases.")]},
            config={"callbacks": [adapter], "recursion_limit": 64},
        )
        adapter.finish(outcome="completed")
    finally:
        if queued is not None:
            queued.close()
        audit.close()
    assert "Controlled root fixture report" in result["messages"][-1].content

    graph = RuntimeCausalContextGraph()
    parent_by_join = {}
    nested_joins = 0
    for event in trace.events:
        graph.apply(event)
        if event.kind is RuntimeEventKind.JOIN_WAIT:
            parent_by_join[event.join_id] = event.invocation_id
        if event.kind is RuntimeEventKind.JOIN_SATISFIED:
            parent = parent_by_join[event.join_id]
            if parent != "root":
                nested_joins += 1
                assert graph.invocations[parent].state is InvocationState.READY
                assert graph.invocations["root"].state is InvocationState.WAIT_JOIN
    assert nested_joins == 4
    agents = 16 if model._dynamic else 17
    assert len(graph.invocations) == agents
    assert all(item.state is InvocationState.DONE for item in graph.invocations.values())
    assert len(closed) == len(set(closed)) == agents

    stages = [
        event for event in trace.events
        if event.attributes.get("child_completion_signal_kind") == "stage"
    ]
    assert len(stages) == agents - 1
    for event in stages:
        assert parent_by_join[event.join_id] == graph.invocations[event.invocation_id].parent_invocation_id
    first = [request for request in model._requests if request["first"]]
    assert all(
        request["root_source_present"] == (source_context and request["role"] == "root")
        for request in model._requests
    )
    if model._dynamic:
        assert not any(request["role"] == "coordinator" for request in first)
        assert sum(request["role"] == "direct" for request in first) == 7
        assert all(
            request["tool_choice"] is None and request["bounds"] is None
            for request in model._requests
        )
        assert all("task" in request["tools"] for request in first if request["role"] == "direct")
        assert result["messages"][1].tool_calls[0]["name"] == "write_todos"
        root_fanouts = [
            len([call for call in message.tool_calls if call["name"] == "task"])
            for message in result["messages"]
            if isinstance(message, AIMessage) and any(
                call["name"] == "task" for call in message.tool_calls
            )
        ]
        assert root_fanouts == [2, 1, 2, 2]
    else:
        assert sum(request["role"] == "coordinator" for request in first) == 4
        assert all(
            (request["tool_choice"], request["bounds"]) == ("task", [2, 4])
            for request in first if request["role"] in ("root", "coordinator")
        )
    assert all("task" not in request["tools"] for request in first if request["role"] == "leaf")
    if deliver_control:
        control_graph = RuntimeCausalContextGraph()
        control_graph.apply_batch(control.events)
        assert all(
            item.state is InvocationState.DONE for item in control_graph.invocations.values()
        )
        control_ids = {event.event_id for event in control.events}
        assert all(event.event_id in control_ids for event in stages)
    else:
        assert not control.events

    path = tmp_path / "trace.jsonl"
    path.write_text("".join(json.dumps(event.to_dict()) + "\n" for event in trace.events))
    shape = harness._trace_summary(path)
    assert shape["root_delegation_round_count"] == 4
    assert shape["nested_delegation_round_count"] == shape["nested_join_satisfied_count"] == 4
    assert shape["delegation_round_count"] == 8
    assert shape["dynamic_subagent_count"] == agents - 1
    assert shape["nested_spawn_count"] == 8
    assert shape["spawn_count_by_depth"] == {"1": agents - 9, "2": 8}
    assert shape["maximum_spawn_depth"] == 2
    assert shape["unknown_spawn_depth_count"] == 0
    assert shape["all_joins_satisfied"] and shape["all_subagents_returned"]


def test_shape_contract_and_future_candidate_are_explicit():
    contract = delegation_contract(NESTED_IN_GRAPH_PROFILE)
    assert contract["root_round_target"] == 4
    assert contract["maximum_spawn_depth"] == 2
    assert contract["nested_coordinator_role"] == NESTED_COORDINATOR_ROLE
    assert not contract["terminal_rejection_for_round_count"]
    assert delegation_contract("native_in_graph_2to4")["root_round_target"] == 3
    candidate = json.loads((Path(__file__).resolve().parents[1] / (
        "configs/migration/qwen35_prefetch_two_wave_2026-10-18.json"
    )).read_text())
    assert candidate["workload"]["fanout_profile"] == NESTED_IN_GRAPH_PROFILE
    for key in (
        "root_round_target", "children_per_round", "maximum_spawn_depth",
        "nested_coordinator_role", "nested_round_target",
    ):
        assert candidate["workload"][key] == contract[key]
    assert candidate["environment"]["FANOUT_PROFILE"] == NESTED_IN_GRAPH_PROFILE
    assert candidate["not_before"] is None
    assert candidate["gpu_experiment_started"] is True


def test_dynamic_contract_has_no_prescribed_rounds_roles_or_generation_bounds():
    contract = delegation_contract(DYNAMIC_NESTED_IN_GRAPH_PROFILE)
    for name in (
        "root_round_target", "nested_round_target", "children_per_round",
        "initial_fanout_generation_bounds", "nested_fanout_generation_bounds",
    ):
        assert contract[name] is None
    assert contract["direct_child_task_tool_enabled"]
    assert contract["root_initial_tool_choice"] == contract["nested_initial_tool_choice"] == "auto"
    assert not contract["fixed_role_sequence"]
    assert not contract["natural_final_response_rejected"]
    assert contract["maximum_spawn_depth"] == 2


def test_dynamic_delegation_allows_final_response_without_any_spawn(tmp_path):
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.tools import tool

    class RecordingModel(FakeMessagesListChatModel):
        _choices: list = PrivateAttr(default_factory=list)

        def bind_tools(self, tools, **kwargs):
            self._choices.append(kwargs)
            return self

    @tool
    def task(description: str) -> str:
        """Delegate when useful."""
        raise AssertionError("The model chose to finish without delegation.")

    model = RecordingModel(responses=[AIMessage(content="The bounded question is answered.")])
    agent = create_agent(
        model=model, tools=[task],
        middleware=[harness.DynamicDelegationPromptMiddleware()],
    )
    result = agent.invoke({"messages": [HumanMessage(content="Answer the bounded question.")]})
    assert result["messages"][-1].content == "The bounded question is answered."
    assert model._choices[0].get("tool_choice") is None
    assert model._choices[0]["parallel_tool_calls"]


def test_four_round_guidance_preserves_early_natural_return(tmp_path):
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    audit = harness.JsonlAudit(tmp_path / "early.jsonl")
    try:
        agent = create_agent(
            model=FakeMessagesListChatModel(responses=[
                AIMessage(content="Blocked by the assigned missing dependency."),
            ]),
            tools=[], middleware=[harness.NativeDelegationRoundPromptMiddleware(
                audit, minimum_children=2, target_rounds=4, nested_coordinator=True,
            )],
        )
        result = agent.invoke({"messages": [HumanMessage(content="Blocked assignment.")]})
        assert result["messages"][-1].content == "Blocked by the assigned missing dependency."
    finally:
        audit.close()


def test_cancelling_nested_work_retires_descendants_without_successful_join_events():
    trace = CollectingSink()
    adapter = DeepAgentsRuntimeAdapter(
        trace, BeliefKVRequestMetadata("workflow", "root", "ctx-root", 0),
    )
    adapter.start()
    coordinator = adapter._declare_task_group("root", "root-model", [{
        "id": "coordinator", "args": {
            "subagent_type": NESTED_COORDINATOR_ROLE, "description": "Coordinate.",
        },
    }])[0]
    adapter._declare_task_group(coordinator.child_invocation_id, "nested-model", [{
        "id": f"grandchild-{index}", "args": {
            "subagent_type": "test-analyst", "description": f"Question {index}.",
        },
    } for index in range(2)])
    assert adapter.cancel_pending_tasks(reason="controlled cancellation") == 3
    assert adapter.cancel_pending_tasks(reason="controlled cancellation") == 0
    adapter.finish(outcome="error")
    graph = RuntimeCausalContextGraph()
    graph.apply_batch(trace.events)
    assert all(item.state.terminal for item in graph.invocations.values())
    assert not any(event.kind is RuntimeEventKind.JOIN_SATISFIED for event in trace.events)
    assert sum(event.kind is RuntimeEventKind.JOIN_TIMEOUT for event in trace.events) == 2
    assert sum(event.kind is RuntimeEventKind.INVOCATION_CANCEL for event in trace.events) == 3
    assert all(
        join.member_invocation_ids <= join.completed_member_ids for join in graph.joins.values()
    )
