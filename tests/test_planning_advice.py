"""CPU capacity/message lifecycle checks; fake models do not qualify task quality."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import Field
import pytest

from beliefkv.experiments.planning_advice_middleware import (
    MARKER, PlanningAdviceMiddleware, middleware_for_config, planning_boundary,
)
from beliefkv.runtime import planning_advice as advice
from beliefkv.runtime.native_transfer_policy import native_residency_budget


def native_fixture(monkeypatch, *, rows=4, running=2, maximum=6):
    cache = SimpleNamespace(
        req_to_token_pool=SimpleNamespace(size=6, available_size=lambda: rows),
        token_to_kv_pool_allocator=SimpleNamespace(size=10000),
        full_evictable_size=lambda: 100,
    )
    monkeypatch.setattr(advice, "observe_static_full_mamba_headroom", lambda cache: SimpleNamespace(
        observable=True, device_full_free_tokens=1000, device_mamba_free_slots=0,
    ))
    return SimpleNamespace(
        _native_cache=cache, _residency_pool_bytes=(98304, 0),
        _native_max_running=maximum, _native_page_size=16, _native_input_reserve=32,
        _native_running_batch=SimpleNamespace(reqs=[object() for _ in range(running)]),
    )


def snapshot():
    return {
        "schema_version": 1, "run_id": "pilot", "model_path": "/models/current",
        "clock_domain": advice.local_monotonic_clock_domain(),
        "server_id": "server", "revision": 1,
        "observed_monotonic_ms": time.monotonic() * 1000.,
        "source": "native_scheduler_safe_point",
        "capacity": {
            "supported": True, "request_rows_free": 2, "request_slots_now": 2,
            "running_requests": 2, "waiting_requests": 3,
            "full_free_tokens": 10000, "full_evictable_tokens": 1000,
            "next_batch_reserve_tokens": 256, "full_bytes_per_token": 98304,
            "service_cost_supported": False,
        },
    }


def reader(path):
    return advice.PlanningAdviceReader(path, run_id="pilot", model_path="/models/current")


def test_rowless_prefetch_frontier_never_becomes_child_execution_capacity(monkeypatch):
    runtime = native_fixture(monkeypatch, rows=0, running=6)
    before = deepcopy(vars(runtime._native_cache.req_to_token_pool))
    observed = advice.native_capacity(runtime, [])
    prefetch = native_residency_budget(
        running_requests=6, max_running_requests=6, available_request_rows=0,
        prefill_slots=4, page_size=16, input_token_reserve=32,
        full_free_tokens=1000, mamba_free_slots=0, full_bytes_per_token=98304,
        mamba_bytes_per_slot=0, protected_bytes=0, occupied_request_rows=6,
    )
    assert prefetch.request_slots == 1
    assert observed["request_slots_now"] == observed["request_rows_free"] == 0
    assert vars(runtime._native_cache.req_to_token_pool) == before


def test_native_counters_keep_reclaimable_and_growth_separate(monkeypatch):
    runtime = native_fixture(monkeypatch, rows=3, running=2)
    observed = advice.native_capacity(runtime, [object()])
    assert observed["request_slots_now"] == 3
    assert observed["next_batch_reserve_tokens"] == 32 + 16 * 6
    assert observed["full_free_tokens"] == 1000
    assert observed["full_evictable_tokens"] == 100
    assert observed["waiting_requests"] == 1
    assert observed["service_cost_supported"] is False
    runtime._residency_pool_bytes = (98304, 4096)
    assert advice.native_capacity(runtime, [])["supported"] is False


def test_full_only_observer_and_capacity_collector_run_without_observer_patch():
    def named(name, **values):
        return type(name, (SimpleNamespace,), {})(**values)

    full = named("MHATokenToKVPool", size=10000)
    host = named(
        "MHATokenToKVPoolHost", size=20000, size_per_token=98304,
        device_pool=full, available_size=lambda: 19000,
    )
    entry = SimpleNamespace(device_pool=full, host_pool=host)
    group = named("HostPoolGroup", entry_map={"kv": entry}, anchor_entry=entry)
    cache = named(
        "UnifiedRadixCache", disable=False,
        tree_components=(SimpleNamespace(name="FULL", value=0),),
        token_to_kv_pool_allocator=named(
            "TokenToKVPoolAllocator", size=10000, _kvcache=full,
            available_size=lambda: 1000, free_group=None,
        ),
        req_to_token_pool=SimpleNamespace(size=6, available_size=lambda: 3),
        host_pool_group=group, cache_controller=SimpleNamespace(mem_pool_host=group),
        full_evictable_size=lambda: 100,
    )
    runtime = SimpleNamespace(
        _native_cache=cache, _residency_pool_bytes=(98304, 0),
        _native_max_running=6, _native_page_size=16, _native_input_reserve=32,
        _native_running_batch=SimpleNamespace(reqs=[object(), object()]),
    )
    result = advice.native_capacity(runtime, [])
    assert result["supported"] and result["request_slots_now"] == 3
    assert result["full_free_tokens"] == 1000


@pytest.mark.parametrize("mutation,status", [
    (lambda row: row.update(run_id="other"), "identity_mismatch"),
    (lambda row: row.update(model_path="/models/other"), "identity_mismatch"),
    (lambda row: row.update(clock_domain="foreign"), "identity_mismatch"),
    (lambda row: row.update(observed_monotonic_ms=time.monotonic() * 1000. - 3000), "stale_or_future"),
    (lambda row: row.update(observed_monotonic_ms=time.monotonic() * 1000. + 3000), "stale_or_future"),
    (lambda row: row["capacity"].update(request_slots_now=3), "invalid"),
    (lambda row: row["capacity"].update(full_free_tokens=True), "invalid"),
    (lambda row: row["capacity"].update(service_cost_supported=True), "invalid"),
])
def test_unqualified_snapshot_never_becomes_zero_or_fresh_capacity(tmp_path, mutation, status):
    path = tmp_path / "advice.json"
    row = snapshot()
    mutation(row)
    path.write_text(json.dumps(row))
    observed = reader(path).read()
    assert observed.status == status and observed.snapshot is None


def test_reader_rejects_restart_revision_regression_and_oversized_data(tmp_path):
    path = tmp_path / "advice.json"
    row = snapshot()
    path.write_text(json.dumps(row))
    consumer = reader(path)
    assert consumer.read().status == "fresh"
    row["revision"] = 0
    path.write_text(json.dumps(row))
    assert consumer.read().status == "server_or_revision_changed"
    row["revision"], row["server_id"] = 2, "replacement"
    path.write_text(json.dumps(row))
    assert consumer.read().status == "server_or_revision_changed"
    path.write_bytes(b"x" * (advice.MAX_SNAPSHOT_BYTES + 1))
    assert consumer.read().status == "oversized"


def test_publisher_round_trip_and_close_explicitly_invalidates_snapshot(tmp_path, monkeypatch):
    runtime = native_fixture(monkeypatch)
    path = tmp_path / "advice.json"
    publisher = advice.PlanningAdvicePublisher(path, run_id="pilot", model_path="/models/current")
    consumer = reader(path)
    try:
        publisher.capture(runtime, [], now_ms=time.monotonic() * 1000.)
        deadline = time.monotonic() + 2.
        while consumer.read().status != "fresh" and time.monotonic() < deadline:
            time.sleep(.005)
        assert consumer.read().status == "fresh"
        assert publisher.last_error is None
        with pytest.raises(FileExistsError):
            advice.PlanningAdvicePublisher(path, run_id="pilot", model_path="/models/current")
    finally:
        publisher.close()
    assert not publisher._thread.is_alive()
    assert consumer.read().status == "unsupported"
    assert json.loads(path.read_text())["capacity"]["reason"] == "closed"
    publisher.close()


def test_boundary_requires_every_tool_return_and_preserves_failure_semantics():
    origin = AIMessage(content="", tool_calls=[
        {"id": "a", "name": "task", "args": {"description": "A"}},
        {"id": "b", "name": "task", "args": {"description": "B"}},
    ])
    a = ToolMessage(content="A", tool_call_id="a", name="task")
    b = ToolMessage(content="B", tool_call_id="b", name="task")
    assert planning_boundary([origin, a]) is None
    assert planning_boundary([origin, a, b]) == "completed_delegation_group"
    nameless = [message.model_copy(update={"name": None}) for message in (a, b)]
    assert planning_boundary([origin, *nameless]) == "completed_delegation_group"
    errors = [message.model_copy(update={"status": "error"}) for message in (a, b)]
    assert planning_boundary([origin, *errors]) is None
    assert planning_boundary([HumanMessage(content="memo", additional_kwargs={MARKER: {}})]) is None


class RecordingModel(FakeMessagesListChatModel):
    seen: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        self.seen.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


@tool
def task(description: str) -> str:
    """Return one bounded checkpoint report."""
    return "report:" + description


@pytest.mark.parametrize("asynchronous", [False, True])
def test_real_agent_graph_adds_one_persisted_advice_per_complete_group(asynchronous):
    model = RecordingModel(responses=[
        AIMessage(content="", tool_calls=[
            {"id": "a", "name": "task", "args": {"description": "A"}},
            {"id": "b", "name": "task", "args": {"description": "B"}},
        ]),
        AIMessage(content="done"),
    ])
    original = HumanMessage(content="Original task and all requirements", id="original")
    middleware = PlanningAdviceMiddleware(mode="static")
    graph = create_agent(model=model, tools=[task], middleware=[middleware])
    result = (
        asyncio.run(graph.ainvoke({"messages": [original]}))
        if asynchronous else graph.invoke({"messages": [original]})
    )
    assert len(model.seen) == 2
    messages = result["messages"]
    assert messages[0].content == original.content and messages[0].id == "original"
    assert [
        message.content for message in messages if isinstance(message, ToolMessage)
    ] == ["report:A", "report:B"]
    additions = [
        message for message in messages if MARKER in message.additional_kwargs
    ]
    assert len(additions) == 2
    assert additions[0].additional_kwargs[MARKER]["phase"] == "arrival_or_new_stage"
    assert additions[1].additional_kwargs[MARKER]["phase"] == "completed_delegation_group"
    assert len([m for m in model.seen[1] if isinstance(m, ToolMessage)]) == 2
    assert [m.content for m in model.seen[0]] == [m.content for m in model.seen[1][:2]]


def test_no_reader_io_on_ordinary_tool_continuation_and_off_mode():
    assert middleware_for_config(SimpleNamespace(planning_advice_mode="off")) is None
    consumer = SimpleNamespace(read=lambda: pytest.fail("ordinary tool should not read snapshot"))
    middleware = PlanningAdviceMiddleware(mode="hardware", reader=consumer)
    origin = AIMessage(content="", tool_calls=[
        {"id": "read", "name": "read_file", "args": {"path": "app.py"}},
    ])
    result = ToolMessage(content="source", tool_call_id="read", name="read_file")
    assert middleware.before_model({"messages": [origin, result]}, None) is None


def test_stale_hardware_fallback_matches_static_prompt_without_zero_capacity(tmp_path):
    path = tmp_path / "advice.json"
    row = snapshot()
    row["observed_monotonic_ms"] -= 3000
    path.write_text(json.dumps(row))
    state = {"messages": [HumanMessage(content="all original requirements")]}
    hardware = PlanningAdviceMiddleware(mode="hardware", reader=reader(path))
    static = PlanningAdviceMiddleware(mode="static")
    first = hardware.before_model(state, None)["messages"][0]
    second = static.before_model(state, None)["messages"][0]
    assert first.content == second.content
    assert "request_slots_now" not in first.content
    assert first.additional_kwargs[MARKER]["status"] == "stale_or_future"


def test_post_launch_native_hook_publishes_without_diagnostic_writer():
    from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime

    captured = []
    runtime = NativeAdmissionRuntime()
    runtime._opportunity_writer = None
    runtime._planning_advice = SimpleNamespace(
        capture=lambda owner, queue, **kwargs: captured.append((owner, queue, kwargs)),
        close=lambda: None,
    )
    try:
        runtime.scheduler_step([], defer_observations=True)
        assert captured == []
        queue = [object()]
        runtime.sample_runtime_observations(queue)
        assert len(captured) == 1
        assert captured[0][0] is runtime and captured[0][1] is queue
        assert captured[0][2]["now_ms"] > 0
    finally:
        runtime.close()


def test_cli_and_config_bind_hardware_advice_explicitly(tmp_path, monkeypatch):
    from beliefkv.experiments.deepagents_swebench import DeepAgentsExperimentConfig
    from scripts.run_deepagents_swebench import parse_args

    common = dict(
        mode="autonomous", base_url="http://unused/v1", model="current",
        output_dir=tmp_path, workload_manifest=tmp_path / "manifest.json",
        docker_image="unused",
    )
    assert DeepAgentsExperimentConfig(**common).planning_advice_mode == "off"
    with pytest.raises(ValueError, match="snapshot/run/model"):
        DeepAgentsExperimentConfig(**common, planning_advice_mode="hardware")
    monkeypatch.setattr(sys, "argv", [
        "run_deepagents_swebench.py", "--mode", "autonomous",
        "--planning-advice-mode", "hardware", "--planning-advice-file", "/tmp/advice",
        "--planning-advice-run-id", "pilot", "--planning-advice-model-path", "/models/current",
    ])
    parsed = parse_args()
    assert parsed.planning_advice_file == Path("/tmp/advice")
    assert parsed.planning_advice_run_id == "pilot"


@pytest.mark.parametrize("mode", ["off", "static", "hardware"])
def test_natural_harness_planning_advice_allows_zero_delegation_without_retries(tmp_path, monkeypatch, mode):
    from beliefkv.experiments import deepagents_swebench as harness
    from beliefkv.experiments.agent_protocol import LoopGuardPolicy
    from beliefkv.experiments.delegation_policy import delegation_contract
    from beliefkv.runtime.agent_safety import ActivationDeadline
    from beliefkv.runtime.deepagents_adapter import DeepAgentsRuntimeAdapter
    from beliefkv.runtime.event_channel import JsonlRuntimeEventSink
    from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata

    audit = harness.JsonlAudit(tmp_path / "audit.jsonl")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backend = harness.DockerWorkspaceBackend(
        workspace, image="fixture:unused", audit=audit, support_dir=None,
    )
    sink = JsonlRuntimeEventSink(tmp_path / "trace.jsonl")
    adapter = DeepAgentsRuntimeAdapter(
        sink, BeliefKVRequestMetadata(
            "workflow", "root", "context", 0, full_prompt_replay_guaranteed=True,
        ),
    )
    model = RecordingModel(responses=[AIMessage(content="Controlled completion.")])
    monkeypatch.setattr(harness, "_model", lambda *args: model)
    monkeypatch.setattr(harness, "make_summary_model", lambda model, **kwargs: model)
    options = {}
    if mode == "hardware":
        path = tmp_path / "advice.json"
        path.write_text(json.dumps(snapshot()))
        options = {
            "planning_advice_file": path, "planning_advice_run_id": "pilot",
            "planning_advice_model_path": "/models/current",
        }
    config = harness.DeepAgentsExperimentConfig(
        mode="autonomous", base_url="http://unused/v1", model="fixture",
        output_dir=tmp_path / "output", workload_manifest=tmp_path / "manifest.json",
        docker_image="fixture:unused", subagent_fanout_profile="natural",
        planning_advice_mode=mode, completion_gate_enabled=False,
        tool_circuit_breaker_enabled=False,
        loop_guard=LoopGuardPolicy(
            enforce_semantic_guard=False, enforce_soft_graph_budget=False,
        ),
        **options,
    )
    workload = harness.SweBenchWorkload(
        instance_id="fixture", repo="fixture/repo", base_commit="abc",
        problem_statement="Preserve all required results and verification.",
        difficulty="unknown",
    )
    deadline = SimpleNamespace(deadline=ActivationDeadline(), register_model=lambda model: model)
    original = HumanMessage(content=workload.problem_statement)
    adapter.start()
    try:
        graph = harness._build_autonomous_agent(config, workload, backend, adapter, deadline)
        result = graph.invoke(
            {"messages": [original]}, config={"callbacks": [adapter], "recursion_limit": 12},
        )
        adapter.finish(outcome="completed")
    finally:
        sink.close()
        audit.close()
    assert result["messages"][-1].content == "Controlled completion."
    assert len(model.seen) == 1
    assert not any(isinstance(message, ToolMessage) for message in result["messages"])
    assert result["messages"][0].content == original.content
    additions = [message for message in result["messages"] if MARKER in message.additional_kwargs]
    assert len(additions) == (0 if mode == "off" else 1)
    if mode == "hardware":
        assert additions[0].additional_kwargs[MARKER]["status"] == "fresh"
        assert '"service_cost_supported":false' in additions[0].content
    records = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert not any(record["event"].startswith("root_delegation_") for record in records)
    contract = delegation_contract("natural")
    assert not contract.get("minimum_completed_root_join_members")
    assert not contract.get("natural_final_response_rejected")
