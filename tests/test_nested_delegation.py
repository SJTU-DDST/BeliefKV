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
    NESTED_COORDINATOR_ROLE, NESTED_IN_GRAPH_PROFILE, delegation_contract,
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
            "root" if "FOUR distinct delegation phases" in system else
            "coordinator" if "Coordinate only the workstream assigned" in system else "leaf"
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
            })

        def call(name, args):
            return {"name": name, "args": args, "id": f"fixture-{uuid4().hex}"}

        if role == "root":
            rounds = sum(any(call["name"] == "task" for call in message.tool_calls)
                         for message in prior)
            calls = [] if rounds >= 4 else [
                call("task", {
                    "subagent_type": NESTED_COORDINATOR_ROLE,
                    "description": f"Phase {rounds + 1}: coordinate two independent invariants.",
                }),
                call("task", {
                    "subagent_type": "test-analyst",
                    "description": f"Phase {rounds + 1}: inspect disjoint compatibility evidence.",
                }),
            ]
        elif role == "coordinator" and not prior:
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


def configuration(tmp_path):
    return harness.DeepAgentsExperimentConfig(
        mode="autonomous", base_url="http://localhost:1/v1", model="fixture",
        output_dir=tmp_path / "output", workload_manifest=tmp_path / "manifest.json",
        docker_image="fixture:unused", subagent_fanout_profile=NESTED_IN_GRAPH_PROFILE,
        child_final_report_shadow=True, completion_gate_enabled=False,
        tool_circuit_breaker_enabled=False,
        loop_guard=LoopGuardPolicy(
            enforce_semantic_guard=False, enforce_soft_graph_budget=False,
        ),
    )


@pytest.mark.parametrize("deliver_control", [False, True])
def test_real_nested_harness_preserves_direct_join_ownership_and_sessions(
    tmp_path, monkeypatch, deliver_control,
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
    model._adapter, model._sessions, model._closed = adapter, sessions, closed
    monkeypatch.setattr(harness, "_model", lambda *args: model)
    monkeypatch.setattr(harness, "make_summary_model", lambda model, **kwargs: model)
    deadline = SimpleNamespace(deadline=ActivationDeadline(), register_model=lambda model: model)
    workload = harness.SweBenchWorkload(
        instance_id="fixture-1", repo="fixture/repo", base_commit="abc",
        problem_statement="Controlled nested delegation.", difficulty="unknown",
    )
    adapter.start()
    try:
        agent = harness._build_autonomous_agent(
            configuration(tmp_path), workload, backend, adapter, deadline,
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
    assert len(graph.invocations) == 17
    assert all(item.state is InvocationState.DONE for item in graph.invocations.values())
    assert len(closed) == len(set(closed)) == 17

    stages = [
        event for event in trace.events
        if event.attributes.get("child_completion_signal_kind") == "stage"
    ]
    assert len(stages) == 16
    for event in stages:
        assert parent_by_join[event.join_id] == graph.invocations[event.invocation_id].parent_invocation_id
    first = [request for request in model._requests if request["first"]]
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
    assert shape["dynamic_subagent_count"] == 16
    assert shape["nested_spawn_count"] == 8
    assert shape["spawn_count_by_depth"] == {"1": 8, "2": 8}
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
    assert candidate["not_before"] == "2026-10-19T08:00:00+08:00"


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
