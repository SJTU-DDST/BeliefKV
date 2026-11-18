import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import threading
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import PrivateAttr

from beliefkv.experiments import deepagents_swebench as harness
from beliefkv.experiments.agent_protocol import LoopGuardPolicy
from beliefkv.experiments.delegation_execution import (
    DelegationExecutionMiddleware, _InvocationSlots,
)
from beliefkv.runtime.agent_safety import ActivationDeadline
from beliefkv.runtime.deepagents_adapter import DeepAgentsRuntimeAdapter
from beliefkv.runtime.event_channel import JsonlRuntimeEventSink
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata


@pytest.mark.parametrize("grant_before_cancel", [False, True])
def test_async_waiter_cancellation_returns_queued_or_just_granted_capacity(grant_before_cancel):
    async def exercise():
        slots = _InvocationSlots(1)
        holder = slots.acquire()
        waiting = asyncio.create_task(slots.acquire_async())
        await asyncio.sleep(0)
        assert slots.snapshot() == {"limit": 1, "leased": 1, "queued": 1}
        if grant_before_cancel:
            holder.release()
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        if not grant_before_cancel:
            holder.release()
        assert slots.snapshot() == {"limit": 1, "leased": 0, "queued": 0}
        async with asyncio.timeout(1):
            recovered = await slots.acquire_async()
        recovered.release()
        recovered.release()
        assert slots.snapshot()["leased"] == 0

    asyncio.run(exercise())


def test_slots_share_capacity_between_sync_and_async_callers():
    async def exercise():
        slots = _InvocationSlots(1)
        holder = slots.acquire()
        waiting = asyncio.create_task(slots.acquire_async())
        await asyncio.sleep(0)
        assert not waiting.done()
        await asyncio.to_thread(holder.release)
        async with asyncio.timeout(1):
            next_holder = await waiting
        assert slots.snapshot() == {"limit": 1, "leased": 1, "queued": 0}
        next_holder.release()
        assert slots.snapshot()["leased"] == 0

    asyncio.run(exercise())


def test_waiters_keep_fifo_order_and_skip_cancelled_entries():
    slots = _InvocationSlots(1)
    holder = slots.acquire()
    first = slots._request()
    cancelled = slots._request()
    last = slots._request()
    assert cancelled.cancel()
    holder.release()
    assert first.done() and not last.done()
    first.result().release()
    assert last.done()
    last.result().release()
    assert slots.snapshot() == {"limit": 1, "leased": 0, "queued": 0}


def test_cancelled_async_handler_returns_its_held_capacity():
    async def exercise():
        middleware = DelegationExecutionMiddleware(limit=1)
        request = SimpleNamespace(tool_call={"id": "held", "name": "task", "args": {}})
        entered = asyncio.Event()
        never = asyncio.Event()

        async def handler(actual):
            entered.set()
            await never.wait()

        running = asyncio.create_task(middleware.awrap_tool_call(request, handler))
        await asyncio.wait_for(entered.wait(), 1)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert middleware._slots.snapshot() == {"limit": 1, "leased": 0, "queued": 0}

    asyncio.run(exercise())


@pytest.mark.parametrize("asynchronous", [False, True])
def test_failed_tool_releases_capacity_and_preserves_request_and_result(asynchronous):
    middleware = DelegationExecutionMiddleware(limit=1)
    request = SimpleNamespace(tool_call={
        "id": "one", "name": "task", "args": {"description": "Keep original assignment."},
    })
    sentinel = object()
    if asynchronous:
        async def exercise():
            async def fail(actual):
                assert actual is request
                raise RuntimeError("original handler error")

            async def succeed(actual):
                assert actual is request
                return sentinel

            with pytest.raises(RuntimeError, match="original handler"):
                await middleware.awrap_tool_call(request, fail)
            assert await middleware.awrap_tool_call(request, succeed) is sentinel

        asyncio.run(exercise())
    else:
        def fail(actual):
            assert actual is request
            raise RuntimeError("original handler error")

        with pytest.raises(RuntimeError, match="original handler"):
            middleware.wrap_tool_call(request, fail)
        assert middleware.wrap_tool_call(request, lambda actual: sentinel) is sentinel
    assert middleware._slots.snapshot() == {"limit": 1, "leased": 0, "queued": 0}


def test_ordinary_tool_bypasses_an_occupied_delegation_limit():
    middleware = DelegationExecutionMiddleware(limit=1)
    holder = middleware._slots.acquire()
    request = SimpleNamespace(tool_call={"id": "read", "name": "read_file", "args": {"path": "a.py"}})
    sentinel = object()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(middleware.wrap_tool_call, request, lambda actual: sentinel)
        try:
            assert future.result(timeout=1) is sentinel
        finally:
            holder.release()
    assert middleware._slots.snapshot()["leased"] == 0


class DelegationFixtureModel(BaseChatModel):
    _adapter: object = PrivateAttr(default=None)
    _lock: object = PrivateAttr(default_factory=threading.Lock)
    _ready: object = PrivateAttr(default_factory=threading.Event)
    _release: object = PrivateAttr(default_factory=threading.Event)
    _target: int = PrivateAttr(default=2)
    _active: int = PrivateAttr(default=0)
    _peak: int = PrivateAttr(default=0)
    _requests: list = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self):
        return "delegation-concurrency-cpu-fixture"

    def get_num_tokens_from_messages(self, messages, tools=None):
        return sum(len(str(message.content)) // 4 + 1 for message in messages)

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self.bind(fixture_tool_choice=tool_choice, **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        metadata = self._adapter.metadata_for_model_run(run_manager.run_id)
        root = metadata.invocation_id == "root"
        with self._lock:
            self._requests.append({
                "root": root, "invocation_id": metadata.invocation_id,
                "messages": [(message.type, str(message.content)) for message in messages],
                "tool_choice": kwargs.get("fixture_tool_choice"),
            })
        if root:
            reports = [message for message in messages if isinstance(message, ToolMessage)]
            calls = [] if reports else [
                {
                    "id": "child-" + label.lower(), "name": "task",
                    "args": {
                        "subagent_type": role, "description": "CONTROLLED CHILD " + label,
                    },
                }
                for label, role in (("A", "repository-explorer"), ("B", "test-analyst"))
            ]
            response = AIMessage(
                content="" if calls else "Integrated both controlled reports.",
                tool_calls=calls,
            )
        else:
            assignment = next(
                str(message.content) for message in messages if isinstance(message, HumanMessage)
            )
            with self._lock:
                self._active += 1
                self._peak = max(self._peak, self._active)
                if self._active >= self._target:
                    self._ready.set()
            try:
                if not self._release.wait(timeout=5):
                    raise TimeoutError("CPU fixture release was not signalled")
                response = AIMessage(content="Report for " + assignment)
            finally:
                with self._lock:
                    self._active -= 1
        return ChatResult(generations=[ChatGeneration(message=response)])


def test_real_natural_harness_changes_overlap_without_changing_declared_tasks(tmp_path, monkeypatch):
    initial_prompts = []
    observations = []
    for limit in (0, 1, 2):
        directory = tmp_path / f"limit{limit}"
        directory.mkdir()
        workspace = directory / "workspace"
        workspace.mkdir()
        audit_path = directory / "audit.jsonl"
        audit = harness.JsonlAudit(audit_path)
        backend = harness.DockerWorkspaceBackend(
            workspace, image="fixture:unused", audit=audit, support_dir=None,
        )
        sink = JsonlRuntimeEventSink(directory / "trace.jsonl")
        adapter = DeepAgentsRuntimeAdapter(
            sink, BeliefKVRequestMetadata(
                "workflow", "root", "context", 0, full_prompt_replay_guaranteed=True,
            ),
        )
        model = DelegationFixtureModel()
        model._adapter, model._target = adapter, 1 if limit == 1 else 2
        monkeypatch.setattr(harness, "_model", lambda *args: model)
        monkeypatch.setattr(harness, "make_summary_model", lambda model, **kwargs: model)
        config = harness.DeepAgentsExperimentConfig(
            mode="autonomous", base_url="http://unused/v1", model="fixture",
            output_dir=directory / "output", workload_manifest=directory / "manifest.json",
            docker_image="fixture:unused", subagent_fanout_profile="natural",
            delegation_concurrency_limit=limit, completion_gate_enabled=False,
            tool_circuit_breaker_enabled=False,
            loop_guard=LoopGuardPolicy(
                enforce_semantic_guard=False, enforce_soft_graph_budget=False,
            ),
        )
        workload = harness.SweBenchWorkload(
            instance_id="fixture", repo="fixture/repo", base_commit="abc",
            problem_statement="Complete both independent controlled assignments.",
            difficulty="unknown",
        )
        deadline = SimpleNamespace(deadline=ActivationDeadline(), register_model=lambda model: model)
        adapter.start()
        try:
            graph = harness._build_autonomous_agent(config, workload, backend, adapter, deadline)
            inputs = {"messages": [HumanMessage(content=workload.problem_statement)]}
            invoke_config = {"callbacks": [adapter], "recursion_limit": 24}
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(graph.invoke, inputs, config=invoke_config)
                try:
                    ready = model._ready.wait(timeout=5)
                finally:
                    model._release.set()
                result = future.result(timeout=10)
                assert ready, "child fixture did not reach the expected overlap"
            adapter.finish(outcome="completed")
        finally:
            model._release.set()
            sink.close()
            audit.close()
        assert result["messages"][-1].content == "Integrated both controlled reports."
        declarations = [
            call for message in result["messages"] if isinstance(message, AIMessage)
            for call in message.tool_calls
        ]
        assert declarations == [
            {
                "id": "child-" + label.lower(), "name": "task", "type": "tool_call",
                "args": {"subagent_type": role, "description": "CONTROLLED CHILD " + label},
            }
            for label, role in (("A", "repository-explorer"), ("B", "test-analyst"))
        ]
        reports = sorted(
            (message.tool_call_id, message.content)
            for message in result["messages"] if isinstance(message, ToolMessage)
        )
        assert reports == [
            ("child-a", "Report for CONTROLLED CHILD A"),
            ("child-b", "Report for CONTROLLED CHILD B"),
        ]
        assert len(model._requests) == 4
        assert sum(request["root"] for request in model._requests) == 2
        assert model._peak == (1 if limit == 1 else 2)
        assert adapter.has_completed_join(minimum_members=2)
        initial_prompts.append(model._requests[0]["messages"])
        records = [json.loads(line) for line in audit_path.read_text().splitlines()]
        enters = [record for record in records if record["event"] == "delegation_execution_enter"]
        exits = [record for record in records if record["event"] == "delegation_execution_exit"]
        assert len(enters) == len(exits) == (2 if limit else 0)
        assert all(record["leased"] <= limit and not record["planner_visible"] for record in enters)
        assert all(record["outcome"] == "returned" for record in exits)
        observation = {
            "limit": limit, "async": False, "child_model_overlap": model._peak,
            "declarations": declarations, "reports": reports, "model_calls": 4,
            "completed_two_member_join": True, "requests": model._requests,
            "scope": "Scripted CPU model, not natural quality, GPU capacity or performance.",
        }
        (directory / "observations.json").write_text(json.dumps(observation, indent=2) + "\n")
        observations.append(observation)
    assert initial_prompts[0] == initial_prompts[1] == initial_prompts[2]
    assert all(row["declarations"] == observations[0]["declarations"] for row in observations)


class AsyncGateFixtureModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_async_langchain_tool_graph_preserves_tasks_and_reports_with_bounded_overlap(tmp_path):
    async def exercise(limit):
        ready, release = asyncio.Event(), asyncio.Event()
        state = {"active": 0, "peak": 0}

        @tool
        async def task(description: str) -> str:
            """Return one controlled child report."""
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
            if state["active"] >= (1 if limit == 1 else 2):
                ready.set()
            try:
                await release.wait()
                return "Report for " + description
            finally:
                state["active"] -= 1

        calls = [
            {"id": label, "name": "task", "args": {"description": label}}
            for label in ("A", "B")
        ]
        model = AsyncGateFixtureModel(responses=[
            AIMessage(content="", tool_calls=calls), AIMessage(content="Both reports integrated."),
        ])
        middleware = DelegationExecutionMiddleware(limit=limit) if limit else None
        graph = create_agent(
            model=model, tools=[task], middleware=[middleware] if middleware else [],
        )
        running = asyncio.create_task(graph.ainvoke({"messages": [HumanMessage(content="Both tasks.")]}))
        try:
            await asyncio.wait_for(ready.wait(), 2)
        finally:
            release.set()
        result = await asyncio.wait_for(running, 2)
        assert result["messages"][-1].content == "Both reports integrated."
        declarations = [
            call for message in result["messages"] if isinstance(message, AIMessage)
            for call in message.tool_calls
        ]
        reports = sorted(
            (message.tool_call_id, message.content)
            for message in result["messages"] if isinstance(message, ToolMessage)
        )
        assert reports == [("A", "Report for A"), ("B", "Report for B")]
        assert state["peak"] == (1 if limit == 1 else 2)
        if middleware:
            assert middleware._slots.snapshot()["leased"] == 0
        return {
            "limit": limit, "tool_overlap": state["peak"],
            "declarations": declarations, "reports": reports,
            "root_model_calls": 2,
            "scope": "Actual async LangChain tool graph, not complete DeepAgents harness.",
        }

    rows = [asyncio.run(exercise(limit)) for limit in (0, 1, 2)]
    assert all(row["declarations"] == rows[0]["declarations"] for row in rows)
    (tmp_path / "observations.json").write_text(json.dumps(rows, indent=2) + "\n")


def test_complete_harness_async_limitation_exists_with_and_without_gate(tmp_path, monkeypatch):
    observations = []
    for limit in (0, 1):
        directory = tmp_path / f"limit{limit}"
        directory.mkdir()
        workspace = directory / "workspace"
        workspace.mkdir()
        audit = harness.JsonlAudit(directory / "audit.jsonl")
        backend = harness.DockerWorkspaceBackend(
            workspace, image="fixture:unused", audit=audit, support_dir=None,
        )
        sink = JsonlRuntimeEventSink(directory / "trace.jsonl")
        adapter = DeepAgentsRuntimeAdapter(
            sink, BeliefKVRequestMetadata("workflow", "root", "context", 0),
        )
        model = DelegationFixtureModel()
        model._adapter = adapter
        monkeypatch.setattr(harness, "_model", lambda *args: model)
        monkeypatch.setattr(harness, "make_summary_model", lambda model, **kwargs: model)
        config = harness.DeepAgentsExperimentConfig(
            mode="autonomous", base_url="http://unused/v1", model="fixture",
            output_dir=directory / "output", workload_manifest=directory / "manifest.json",
            docker_image="fixture:unused", subagent_fanout_profile="natural",
            delegation_concurrency_limit=limit, completion_gate_enabled=False,
            tool_circuit_breaker_enabled=False,
            loop_guard=LoopGuardPolicy(enforce_semantic_guard=False, enforce_soft_graph_budget=False),
        )
        workload = harness.SweBenchWorkload(
            instance_id="fixture", repo="fixture/repo", base_commit="abc",
            problem_statement="Original controlled task.", difficulty="unknown",
        )
        deadline = SimpleNamespace(deadline=ActivationDeadline(), register_model=lambda model: model)
        adapter.start()
        try:
            graph = harness._build_autonomous_agent(config, workload, backend, adapter, deadline)
            with pytest.raises(NotImplementedError, match="awrap_model_call is not available") as failure:
                asyncio.run(graph.ainvoke(
                    {"messages": [HumanMessage(content=workload.problem_statement)]},
                    config={"callbacks": [adapter]},
                ))
            assert model._requests == []
            adapter.finish(outcome="error")
        finally:
            sink.close()
            audit.close()
        observations.append({
            "limit": limit, "error_type": "NotImplementedError", "error": str(failure.value),
            "model_calls": 0, "full_async_harness_qualified": False,
        })
    assert observations[0]["error"] == observations[1]["error"]
    (tmp_path / "observations.json").write_text(json.dumps(observations, indent=2) + "\n")


@pytest.mark.parametrize("limit", [-1, True, 1.5])
def test_configuration_rejects_invalid_or_nested_concurrency_control(tmp_path, limit):
    common = dict(
        mode="autonomous", base_url="http://unused/v1", model="fixture",
        output_dir=tmp_path, workload_manifest=tmp_path / "manifest.json",
        docker_image="unused", delegation_concurrency_limit=limit,
    )
    with pytest.raises(ValueError, match="nonnegative integer"):
        harness.DeepAgentsExperimentConfig(**common)


def test_cli_and_configuration_keep_the_control_default_off_and_single_level(tmp_path, monkeypatch):
    from scripts.run_deepagents_swebench import parse_args

    config = harness.DeepAgentsExperimentConfig(
        mode="autonomous", base_url="http://unused/v1", model="fixture",
        output_dir=tmp_path, workload_manifest=tmp_path / "manifest.json", docker_image="unused",
    )
    assert config.delegation_concurrency_limit == 0
    for settings in (
        {"mode": "planned"}, {"subagent_fanout_profile": "native_in_graph_parallel_workstreams"},
    ):
        with pytest.raises(ValueError, match="single-level natural"):
            replace(config, delegation_concurrency_limit=1, **settings)
    monkeypatch.setattr("sys.argv", [
        "run_deepagents_swebench.py", "--mode", "autonomous",
        "--delegation-concurrency-limit", "2",
    ])
    assert parse_args().delegation_concurrency_limit == 2
