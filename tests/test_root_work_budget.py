import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace as NS

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command
import pytest

from beliefkv.experiments.root_work_budget import RootWorkBudgetMiddleware
from tests.test_child_work_budget import Audit, ToolModel


@pytest.mark.parametrize("asynchronous", [False, True])
def test_root_can_finish_real_work_past_soft_target_and_start_a_new_phase(asynchronous):
    completed_joins = [0]

    @tool
    def evidence(item: str) -> str:
        """Inspect one concrete integration invariant."""
        return "Observed " + item

    @tool
    def task(description: str) -> str:
        """Complete a real delegated question in the test fixture."""
        completed_joins[0] += 1
        return "Independent report: " + description

    def call(name, index):
        return AIMessage(content="", tool_calls=[{
            "name": name, "args": {
                "description" if name == "task" else "item": str(index),
            }, "id": f"call-{index}",
        }])

    audit = Audit()
    agent = create_agent(
        model=ToolModel(responses=[
            *(call("evidence", index) for index in range(1, 5)),
            call("task", 5),
            call("evidence", 6), call("evidence", 7), call("evidence", 8),
            AIMessage(content="Completed all requirements after integrating the report."),
        ]),
        tools=[evidence, task],
        middleware=[RootWorkBudgetMiddleware(
            adapter=NS(completed_join_count=lambda: completed_joins[0]),
            warning_calls=2, target_calls=3, audit=audit, scope="root",
        )],
    )
    inputs = {"messages": [HumanMessage(content="Implement and validate the assignment.")]}
    result = asyncio.run(agent.ainvoke(inputs)) if asynchronous else agent.invoke(inputs)
    assert result["messages"][-1].content.startswith("Completed all requirements")
    assert "root_work_model_calls" not in result
    assert audit.rows[-1]["phase_model_calls"] == 4
    assert audit.rows[-1]["total_model_calls"] == 9
    assert audit.rows[-1]["completed_joins"] == 1
    notices = [row for row in audit.rows if row["event"] == "root_work_budget_notice"]
    assert [row["model_calls"] for row in notices] == [2, 3, 2, 3]
    tools = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert tools[3].content == "Observed 4"
    assert tools[5].content == "Observed 6"
    assert "do not skip requirements" in tools[2].content
    assert all(row["forced"] is False for row in audit.rows)


def test_just_completed_join_suppresses_obsolete_phase_notice():
    audit = Audit()
    middleware = RootWorkBudgetMiddleware(
        adapter=NS(completed_join_count=lambda: 1),
        warning_calls=2, target_calls=3, audit=audit, scope="root",
    )
    original = Command(update={"messages": [
        ToolMessage(content="Actual child report.", tool_call_id="child"),
    ]})
    result = middleware._append_notice(NS(
        state={"root_work_notice": (3, "child"), "root_work_completed_joins": 0},
        tool_call={"id": "child"},
    ), original)
    assert result is original
    assert not audit.rows


def test_real_nested_harness_keeps_root_budget_private_and_allows_followup_rounds(
    tmp_path, monkeypatch,
):
    from beliefkv.experiments import deepagents_swebench as harness
    from beliefkv.experiments.agent_protocol import ActivationDeadline
    from beliefkv.experiments.delegation_policy import DYNAMIC_NESTED_IN_GRAPH_PROFILE
    from beliefkv.runtime.deepagents_adapter import DeepAgentsRuntimeAdapter
    from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata
    from beliefkv.runtime.sglang_v0520_sessions import NativeRadixSessionLeases
    from tests.test_deepagents_adapter import CollectingSink
    from tests.test_nested_delegation import NestedFixtureModel, configuration

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    audit_path = tmp_path / "audit.jsonl"
    audit = harness.JsonlAudit(audit_path)
    backend = harness.DockerWorkspaceBackend(
        workspace, image="fixture:unused", audit=audit, support_dir=None,
    )
    closed = []
    adapter = DeepAgentsRuntimeAdapter(
        CollectingSink(), BeliefKVRequestMetadata("workflow", "root", "ctx-root", 0),
        native_radix_sessions=NativeRadixSessionLeases(closed.append),
    )
    model = NestedFixtureModel()
    model._dynamic = True
    model._adapter, model._sessions, model._closed = (
        adapter, adapter.native_radix_sessions, closed,
    )
    monkeypatch.setattr(harness, "_model", lambda *_args: model)
    monkeypatch.setattr(harness, "make_summary_model", lambda model, **_kwargs: model)
    config = replace(
        configuration(tmp_path, DYNAMIC_NESTED_IN_GRAPH_PROFILE),
        root_work_budget_warning_calls=1, root_work_budget_target_calls=2,
    )
    workload = harness.SweBenchWorkload(
        "phase-fixture", "fixture/repo", "abc", "Review repository invariants.", "unknown",
    )
    deadline = NS(deadline=ActivationDeadline(), register_model=lambda model: model)
    adapter.start()
    try:
        agent = harness._build_autonomous_agent(config, workload, backend, adapter, deadline)
        result = agent.invoke(
            {"messages": [HumanMessage(content="Complete all required follow-up phases.")]},
            config={"callbacks": [adapter], "recursion_limit": 64},
        )
        adapter.finish(outcome="completed")
    finally:
        audit.close()
    assert "Controlled root fixture report" in result["messages"][-1].content
    assert adapter.completed_join_count() == 4
    assert len([row for row in model._requests if row["role"] == "root"]) > 2
    assert all(
        row["root_phase_budget_present"] is (row["role"] == "root")
        for row in model._requests
    )
    events = [json.loads(line) for line in audit_path.read_text().splitlines()]
    returned = [row for row in events if row["event"] == "root_work_budget_return"]
    assert returned[-1]["completed_joins"] == 4
    assert returned[-1]["total_model_calls"] > config.root_work_budget_target_calls


@pytest.mark.parametrize("warning,target", [(0, 24), (16, 0), (24, 16), (True, 24)])
def test_invalid_root_budget_configuration_is_rejected(tmp_path, warning, target):
    from beliefkv.experiments.deepagents_swebench import DeepAgentsExperimentConfig
    from beliefkv.experiments.delegation_policy import DYNAMIC_NESTED_IN_GRAPH_PROFILE

    with pytest.raises(ValueError, match="root work budget"):
        DeepAgentsExperimentConfig(
            mode="autonomous", base_url="http://localhost:1/v1", model="fixture",
            output_dir=tmp_path, workload_manifest=tmp_path / "manifest.json",
            docker_image="fixture:unused",
            subagent_fanout_profile=DYNAMIC_NESTED_IN_GRAPH_PROFILE,
            root_work_budget_warning_calls=warning, root_work_budget_target_calls=target,
        )
