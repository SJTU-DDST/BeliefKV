from __future__ import annotations

import asyncio
import itertools
import json
import threading
import time
from types import SimpleNamespace
from typing import Any, Sequence
from unittest.mock import patch
from uuid import uuid4

import pytest

pytest.importorskip("deepagents")

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from beliefkv.control.causal_graph import InvocationState, RuntimeCausalContextGraph
from beliefkv.core.events import RuntimeEventKind
from beliefkv.experiments.agent_protocol import ActivationDeadline
from beliefkv.runtime.deepagents_adapter import (
    BeliefKVChatOpenAI,
    DeepAgentsRuntimeAdapter,
)
from beliefkv.runtime.context_lifecycle import (
    ContextCompactionRecord,
    ContextLifecycleMiddleware,
    ContextLifecyclePolicy,
)
from beliefkv.runtime.event_channel import QueuedRuntimeEventSink
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata
from beliefkv.runtime.sglang_v0520_sessions import NativeRadixSessionLeases


def test_native_session_follows_context_not_tool_and_retires_on_return() -> None:
    closed = []
    sessions = NativeRadixSessionLeases(closed.append)
    sink = CollectingSink()
    root = BeliefKVRequestMetadata(
        "wf", "root", "ctx", 0, full_prompt_replay_guaranteed=True
    )
    adapter = DeepAgentsRuntimeAdapter(
        sink, root, native_radix_sessions=sessions
    )
    adapter.start()
    model_run_id = uuid4()
    adapter.on_chat_model_start(
        {}, [[HumanMessage(content="inspect")]], run_id=model_run_id
    )
    client = BeliefKVChatOpenAI(
        beliefkv_adapter=adapter,
        model="test-model",
        base_url="http://127.0.0.1:30000/v1",
        api_key="EMPTY",
        max_retries=0,
    )
    first, _ = client._with_beliefkv_runtime(
        SimpleNamespace(run_id=model_run_id), {}
    )
    session_id = first["extra_body"]["session_id"]
    assert client._with_beliefkv_runtime(
        SimpleNamespace(run_id=model_run_id), {}
    )[0]["extra_body"]["session_id"] == session_id
    assert closed == []
    with pytest.raises(RuntimeError, match="conflicting native session_id"):
        client._with_beliefkv_runtime(
            SimpleNamespace(run_id=model_run_id),
            {"extra_body": {"session_id": "foreign"}},
        )
    child_metadata = BeliefKVRequestMetadata(
        "wf", "child", "child-ctx", 0, full_prompt_replay_guaranteed=True
    )
    child_session = sessions.for_request(child_metadata)
    assert child_session != session_id
    adapter.finish(outcome="completed")
    adapter.finish(outcome="completed")
    assert set(closed) == {session_id, child_session}


def test_native_session_closes_child_at_return_but_keeps_parent_during_tool() -> None:
    closed = []
    sessions = NativeRadixSessionLeases(closed.append)
    root = BeliefKVRequestMetadata(
        "wf", "root", "ctx", 0, full_prompt_replay_guaranteed=True
    )
    adapter = DeepAgentsRuntimeAdapter(
        CollectingSink(), root, native_radix_sessions=sessions
    )
    adapter.start()
    parent_session = sessions.for_request(root)
    child_task = adapter.declare_runtime_tasks([("explorer", "Inspect")])[0]
    child_session = sessions.for_request(
        BeliefKVRequestMetadata(
            "wf",
            child_task.invocation_id,
            child_task.context_id,
            0,
            full_prompt_replay_guaranteed=True,
        )
    )
    adapter._publish(
        (
            adapter._event(
                RuntimeEventKind.TOOL_START,
                invocation_id="root",
                context_id="ctx",
            ),
        ),
        control=True,
    )
    assert closed == []
    adapter.complete_runtime_task(child_task)
    assert closed == [child_session]
    assert sessions.for_request(root) == parent_session
    adapter.finish(outcome="completed")
    assert closed == [child_session, parent_session]


class CollectingSink:
    def __init__(self) -> None:
        self.events = []
        self._lock = threading.Lock()

    def emit_batch(self, events) -> None:
        with self._lock:
            self.events.extend(events)

    def close(self) -> None:
        pass


def test_completed_same_input_duration_is_emitted_at_next_tool_start() -> None:
    now = [1000.0]
    trace = CollectingSink()
    adapter = DeepAgentsRuntimeAdapter(
        trace, BeliefKVRequestMetadata("wf", "root", "ctx", 0),
        clock_ms=lambda: now[0],
    )
    adapter.start()
    first = uuid4()
    adapter.on_tool_start(
        {"name": "execute"}, "", run_id=first,
        inputs={"command": "echo ready"}, tool_call_id="first",
    )
    now[0] = 1100.0
    adapter.on_tool_end("ready", run_id=first)
    now[0] = 1120.0
    second = uuid4()
    adapter.on_tool_start(
        {"name": "execute"}, "", run_id=second,
        inputs={"command": "echo ready"}, tool_call_id="second",
    )
    starts = [event for event in trace.events
              if event.kind == RuntimeEventKind.TOOL_START]
    assert "previous_same_input_duration_ms" not in starts[0].attributes
    assert starts[1].attributes["previous_same_input_duration_ms"] == 100
    assert starts[1].attributes["previous_same_input_age_ms"] == 20
    assert starts[1].attributes["previous_same_input_status"] == "success"
    assert "echo ready" not in json.dumps(starts[1].to_dict())

class BatchCollectingSink(CollectingSink):
    def __init__(self) -> None:
        super().__init__()
        self.batches = []

    def emit_batch(self, events) -> None:
        self.batches.append(tuple(events))
        super().emit_batch(events)


class FailingControlSink:
    def emit_batch(self, events) -> None:
        del events
        raise ConnectionError("runtime control socket disappeared")


def _child_completion_result(*tool_names: str) -> LLMResult:
    return LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content="private model output",
                        tool_calls=[
                            {
                                "name": name,
                                "args": {"private": "tool arguments"},
                                "id": f"completion-{index}",
                            }
                            for index, name in enumerate(tool_names)
                        ],
                    )
                )
            ]
        ]
    )


def _natural_child_result(
    text: str, *, finish_reason: str = "stop", invalid: bool = False
) -> LLMResult:
    return LLMResult(generations=[[
        ChatGeneration(message=AIMessage(
            content=text,
            response_metadata={"finish_reason": finish_reason},
            invalid_tool_calls=(
                [{"name": "task", "args": "{", "id": "bad", "error": "invalid"}]
                if invalid else []
            ),
        ))
    ]])


def test_natural_child_final_is_provisional_and_rejects_ambiguous_output() -> None:
    trace = CollectingSink()
    control = CollectingSink()
    queued = QueuedRuntimeEventSink(control)
    adapter = DeepAgentsRuntimeAdapter(
        trace, BeliefKVRequestMetadata("wf", "root", "ctx", 0),
        control_sink=queued,
    )
    try:
        adapter.start()
        task = adapter.declare_runtime_tasks([("explorer", "Inspect")])[0]
        chain = uuid4()
        adapter.on_chain_start(
            {}, {}, run_id=chain, metadata=adapter.invocation_scope(task),
        )
        for output in (
            _natural_child_result(" "),
            _natural_child_result("unfinished", finish_reason="length"),
            _natural_child_result("invalid tool", invalid=True),
        ):
            run = uuid4()
            adapter.on_chat_model_start(
                {}, [[HumanMessage(content="child")]],
                run_id=run, parent_run_id=chain,
            )
            adapter.on_llm_end(output, run_id=run, parent_run_id=chain)
        run = uuid4()
        adapter.on_chat_model_start(
            {}, [[HumanMessage(content="child")]],
            run_id=run, parent_run_id=chain,
        )
        adapter.on_llm_end(
            _natural_child_result("Private final answer"),
            run_id=run, parent_run_id=chain,
        )
        queued.close()
        intents = [
            event for event in control.events
            if event.kind == RuntimeEventKind.STRUCTURED_ACTION
        ]
        assert len(intents) == 1
        intent = intents[0]
        assert intent.invocation_id == task.invocation_id
        assert intent.join_id == task.join_id
        assert intent.attributes["child_completion_signal_kind"] == "natural_final"
        assert intent.attributes["structured_action_names"] == []
        assert intent.attributes["request_id"] == f"beliefkv:{run}"
        assert "Private final answer" not in json.dumps(intent.to_dict())
        assert not any(
            event.kind == RuntimeEventKind.RETURN for event in control.events
        )
    finally:
        queued.close()


def test_child_completion_intent_has_bound_identity_and_no_model_payload() -> None:
    trace = CollectingSink()
    control = CollectingSink()
    queued = QueuedRuntimeEventSink(control)
    adapter = DeepAgentsRuntimeAdapter(
        trace,
        BeliefKVRequestMetadata("wf", "root", "ctx", 0),
        control_sink=queued,
    )
    try:
        adapter.start()
        task = adapter.declare_runtime_tasks(
            [("explorer", "private task description")], group_id="intent"
        )[0]
        tool_run = uuid4()
        adapter.on_tool_start(
            {"name": "task"},
            "",
            run_id=tool_run,
            inputs={"subagent_type": "explorer", "description": "private task description"},
            tool_call_id=task.tool_call_id,
        )
        first_run = uuid4()
        adapter.on_chat_model_start(
            {}, [[HumanMessage(content="private prompt")]],
            run_id=first_run, parent_run_id=tool_run,
        )
        adapter.on_llm_end(
            _child_completion_result("read_file"),
            run_id=first_run, parent_run_id=tool_run,
        )
        model_run = uuid4()
        adapter.on_chat_model_start(
            {}, [[HumanMessage(content="private prompt")]],
            run_id=model_run, parent_run_id=tool_run,
        )
        adapter.on_llm_end(
            _child_completion_result("ChildCompletion"),
            run_id=model_run, parent_run_id=tool_run,
        )
        graph = RuntimeCausalContextGraph()
        graph.apply_batch(trace.events[:-1])
        before = graph.invocations[task.invocation_id].state
        delta = graph.apply(trace.events[-1])
        assert graph.invocations[task.invocation_id].state == before
        assert delta.changed_contexts == frozenset()
        assert delta.completed_invocations == frozenset()
        adapter.complete_runtime_task(task)
        intent = [
            event for event in control.events
            if event.kind == RuntimeEventKind.STRUCTURED_ACTION
        ]
        assert len(intent) == 1
        event = intent[0]
        assert event.join_id == task.join_id
        assert event.invocation_id == task.invocation_id
        assert event.context_id == task.context_id
        assert event.context_epoch == 1
        assert event.attributes["request_id"] == f"beliefkv:{model_run}"
        assert event.attributes["beliefkv_child_completion_intent"] is True
        assert event.attributes["provisional"] is True
        assert event.attributes["structured_action_kinds"] == ["final_answer"]
        assert event.attributes["structured_action_names"] == ["ChildCompletion"]
        assert event.attributes["child_completion_signal_kind"] == "explicit"
        assert "private" not in json.dumps(event.to_dict())
        assert event in trace.events
        assert [
            item.kind for item in control.events
            if item.invocation_id == task.invocation_id
        ][-2:] == [RuntimeEventKind.STRUCTURED_ACTION, RuntimeEventKind.RETURN]
    finally:
        queued.close()


def test_stream_first_content_is_trace_only_and_one_per_child_model_run() -> None:
    trace = CollectingSink()
    control = CollectingSink()
    queued = QueuedRuntimeEventSink(control)
    adapter = DeepAgentsRuntimeAdapter(
        trace, BeliefKVRequestMetadata("wf", "root", "ctx", 0),
        control_sink=queued,
    )
    try:
        adapter.start()
        task = adapter.declare_runtime_tasks(
            [("explorer", "private task")], group_id="stream-shadow"
        )[0]
        tool_run = uuid4()
        adapter.on_tool_start(
            {"name": "task"}, "", run_id=tool_run,
            inputs={"subagent_type": "explorer", "description": "private task"},
            tool_call_id=task.tool_call_id,
        )
        run = uuid4()
        adapter.on_chat_model_start(
            {}, [[HumanMessage(content="private prompt")]],
            run_id=run, parent_run_id=tool_run,
        )
        adapter.on_llm_new_token(" ", run_id=run)
        adapter.on_llm_new_token(
            "secret final answer", run_id=run,
            chunk=SimpleNamespace(message=SimpleNamespace(
                content="secret final answer", tool_call_chunks=[],
            )),
        )
        adapter.on_llm_new_token(
            "", run_id=run,
            chunk=SimpleNamespace(message=SimpleNamespace(
                content="", tool_call_chunks=[{"name": "execute"}],
            )),
        )
        adapter.on_llm_new_token("another private token", run_id=run)
        queued.close()
        shadows = [
            event for event in trace.events
            if event.attributes.get("beliefkv_child_first_content_shadow")
        ]
        assert len(shadows) == 1
        assert shadows[0].invocation_id == task.invocation_id
        assert shadows[0].join_id == task.join_id
        assert shadows[0].attributes["diagnostic_only"] is True
        assert shadows[0] not in control.events
        assert "secret" not in json.dumps(shadows[0].to_dict())
        tools = [
            event for event in trace.events
            if event.attributes.get("beliefkv_child_first_tool_chunk_shadow")
        ]
        assert len(tools) == 1
        assert tools[0] not in control.events
        assert "execute" not in json.dumps(tools[0].to_dict())
    finally:
        queued.close()


def test_streaming_entrypoints_preserve_child_identity() -> None:
    trace = CollectingSink()
    adapter = DeepAgentsRuntimeAdapter(
        trace, BeliefKVRequestMetadata("wf", "root", "ctx", 0)
    )
    adapter.start()
    child = adapter.declare_runtime_tasks(
        [("explorer", "private task")], group_id="streamed-identity"
    )[0]
    tool_run = uuid4()
    adapter.on_tool_start(
        {"name": "task"}, "", run_id=tool_run,
        inputs={"subagent_type": "explorer", "description": "private task"},
        tool_call_id=child.tool_call_id,
    )
    client = BeliefKVChatOpenAI(
        beliefkv_adapter=adapter,
        model="test-model", base_url="http://127.0.0.1:30000/v1",
        api_key="EMPTY", max_retries=0,
    )
    captured = []

    def stream_stub(self, messages, stop=None, run_manager=None, **kwargs):
        captured.append(kwargs)
        yield "chunk"

    async def astream_stub(self, messages, stop=None, run_manager=None, **kwargs):
        captured.append(kwargs)
        yield "chunk"

    async def consume(run):
        return [
            chunk async for chunk in client._astream(
                [HumanMessage(content="child")],
                run_manager=SimpleNamespace(run_id=run),
                tools=[{"type": "function"}],
            )
        ]

    for asynchronous in (False, True):
        run = uuid4()
        adapter.on_chat_model_start(
            {}, [[HumanMessage(content="child")]],
            run_id=run, parent_run_id=tool_run,
        )
        with (
            patch.object(ChatOpenAI, "_stream", stream_stub),
            patch.object(ChatOpenAI, "_astream", astream_stub),
        ):
            chunks = (
                asyncio.run(consume(run))
                if asynchronous else list(client._stream(
                    [HumanMessage(content="child")],
                    run_manager=SimpleNamespace(run_id=run),
                    tools=[{"type": "function"}],
                ))
            )
        assert chunks == ["chunk"]
        assert captured[-1]["extra_body"]["rid"] == f"beliefkv:{run}"
        assert captured[-1]["extra_body"]["beliefkv_metadata"]["invocation_id"] == (
            child.invocation_id
        )
        assert "tool_choice" not in captured[-1]
        assert client.active_request_count() == 0

    def implicit_generate(self, messages, stop=None, run_manager=None, **kwargs):
        return list(self._stream(messages, stop=stop, **kwargs))

    async def implicit_agenerate(
        self, messages, stop=None, run_manager=None, **kwargs
    ):
        return [
            chunk async for chunk in self._astream(
                messages, stop=stop, **kwargs
            )
        ]

    for asynchronous in (False, True):
        run = uuid4()
        adapter.on_chat_model_start(
            {}, [[HumanMessage(content="child")]],
            run_id=run, parent_run_id=tool_run,
        )
        with (
            patch.object(ChatOpenAI, "_stream", stream_stub),
            patch.object(ChatOpenAI, "_astream", astream_stub),
            patch.object(ChatOpenAI, "_generate_with_cache", implicit_generate),
            patch.object(ChatOpenAI, "_agenerate_with_cache", implicit_agenerate),
        ):
            chunks = (
                asyncio.run(client._agenerate_with_cache(
                    [HumanMessage(content="child")],
                    run_manager=SimpleNamespace(run_id=run),
                    tools=[{"type": "function"}],
                ))
                if asynchronous else client._generate_with_cache(
                    [HumanMessage(content="child")],
                    run_manager=SimpleNamespace(run_id=run),
                    tools=[{"type": "function"}],
                )
            )
        assert chunks == ["chunk"]
        assert captured[-1]["extra_body"]["rid"] == f"beliefkv:{run}"
        assert "tool_choice" not in captured[-1]
        assert client.active_request_count() == 0


def test_child_completion_intent_rejects_unbound_ambiguous_repeated_and_terminal() -> None:
    control = CollectingSink()
    queued = QueuedRuntimeEventSink(control)
    adapter = DeepAgentsRuntimeAdapter(
        CollectingSink(),
        BeliefKVRequestMetadata("wf", "root", "ctx", 0),
        control_sink=queued,
    )
    try:
        adapter.start()
        task = adapter.declare_runtime_tasks([("explorer", "Inspect")])[0]
        unbound = uuid4()
        adapter.on_chat_model_start({}, [[HumanMessage(content="root")]], run_id=unbound)
        adapter.on_llm_end(_child_completion_result("ChildCompletion"), run_id=unbound)
        unrelated_tool = uuid4()
        adapter.on_tool_start(
            {"name": "read_file"}, "", run_id=unrelated_tool,
            inputs={"file_path": "/file"},
        )
        unrelated_model = uuid4()
        adapter.on_chat_model_start(
            {}, [[HumanMessage(content="root tool")]],
            run_id=unrelated_model, parent_run_id=unrelated_tool,
        )
        adapter.on_llm_end(
            _child_completion_result("ChildCompletion"),
            run_id=unrelated_model, parent_run_id=unrelated_tool,
        )
        chain = uuid4()
        adapter.on_chain_start(
            {}, {}, run_id=chain, metadata=adapter.invocation_scope(task),
        )
        multi = uuid4()
        adapter.on_chat_model_start(
            {}, [[HumanMessage(content="child")]],
            run_id=multi, parent_run_id=chain,
        )
        adapter.on_llm_end(
            _child_completion_result("ChildCompletion", "read_file"),
            run_id=multi, parent_run_id=chain,
        )
        duplicate_tools = uuid4()
        adapter.on_chat_model_start(
            {}, [[HumanMessage(content="child")]],
            run_id=duplicate_tools, parent_run_id=chain,
        )
        adapter.on_llm_end(
            _child_completion_result("ChildCompletion", "ChildCompletion"),
            run_id=duplicate_tools, parent_run_id=chain,
        )
        valid = uuid4()
        adapter.on_chat_model_start(
            {}, [[HumanMessage(content="child")]],
            run_id=valid, parent_run_id=chain,
        )
        adapter.on_llm_end(
            _child_completion_result("ChildCompletion"),
            run_id=valid, parent_run_id=chain,
        )
        adapter.on_llm_end(
            _child_completion_result("ChildCompletion"),
            run_id=valid, parent_run_id=chain,
        )
        terminal_run = uuid4()
        adapter.on_chat_model_start(
            {}, [[HumanMessage(content="child")]],
            run_id=terminal_run, parent_run_id=chain,
        )
        adapter.complete_runtime_task(task)
        adapter.on_llm_end(
            _child_completion_result("ChildCompletion"),
            run_id=terminal_run, parent_run_id=chain,
        )
        assert [
            event.attributes["request_id"] for event in control.events
            if event.kind == RuntimeEventKind.STRUCTURED_ACTION
        ] == [f"beliefkv:{valid}"]
        assert queued.timing_summary()["tool_start_count"] == 1
    finally:
        queued.close()


def test_child_completion_intent_is_nonblocking_and_precedes_confirmed_return() -> None:
    intent_started = threading.Event()
    release_intent = threading.Event()

    class SlowIntentSink(CollectingSink):
        def emit_batch(self, events) -> None:
            if events[0].kind == RuntimeEventKind.STRUCTURED_ACTION:
                intent_started.set()
                if not release_intent.wait(timeout=2.0):
                    raise TimeoutError("intent ACK was not released")
            super().emit_batch(events)

        def close(self) -> None:
            pass

    control = SlowIntentSink()
    queued = QueuedRuntimeEventSink(control)
    adapter = DeepAgentsRuntimeAdapter(
        CollectingSink(),
        BeliefKVRequestMetadata("wf", "root", "ctx", 0),
        control_sink=queued,
    )
    sender = None
    try:
        adapter.start()
        task = adapter.declare_runtime_tasks([("explorer", "Inspect")])[0]
        chain = uuid4()
        adapter.on_chain_start(
            {}, {}, run_id=chain, metadata=adapter.invocation_scope(task),
        )
        model_run = uuid4()
        adapter.on_chat_model_start(
            {}, [[HumanMessage(content="child")]],
            run_id=model_run, parent_run_id=chain,
        )
        began = time.monotonic()
        adapter.on_llm_end(
            _child_completion_result("ChildCompletion"),
            run_id=model_run, parent_run_id=chain,
        )
        assert time.monotonic() - began < 0.5
        assert intent_started.wait(timeout=1.0)
        completed = threading.Event()

        def return_child() -> None:
            adapter.complete_runtime_task(task)
            completed.set()

        sender = threading.Thread(target=return_child)
        sender.start()
        assert not completed.wait(timeout=0.05)
        release_intent.set()
        sender.join(timeout=2.0)
        assert not sender.is_alive()
        assert completed.is_set()
        assert [
            event.kind for event in control.events
            if event.invocation_id == task.invocation_id
        ][-2:] == [
            RuntimeEventKind.STRUCTURED_ACTION, RuntimeEventKind.RETURN
        ]
        assert queued.timing_summary()["tool_start_count"] == 0
    finally:
        release_intent.set()
        if sender is not None:
            sender.join(timeout=2.0)
        queued.close()


def test_child_completion_intent_fails_closed_without_queued_control_sink() -> None:
    trace = CollectingSink()
    control = CollectingSink()
    adapter = DeepAgentsRuntimeAdapter(
        trace,
        BeliefKVRequestMetadata("wf", "root", "ctx", 0),
        control_sink=control,
    )
    adapter.start()
    task = adapter.declare_runtime_tasks([("explorer", "Inspect")])[0]
    chain = uuid4()
    adapter.on_chain_start({}, {}, run_id=chain, metadata=adapter.invocation_scope(task))
    model_run = uuid4()
    adapter.on_chat_model_start(
        {}, [[HumanMessage(content="child")]],
        run_id=model_run, parent_run_id=chain,
    )
    adapter.on_llm_end(
        _child_completion_result("ChildCompletion"),
        run_id=model_run, parent_run_id=chain,
    )
    assert not any(
        event.kind == RuntimeEventKind.STRUCTURED_ACTION
        for event in trace.events + control.events
    )


def test_child_completion_intent_full_queue_fails_closed_without_waiting() -> None:
    stalled = threading.Event()
    release = threading.Event()

    class StalledSink(CollectingSink):
        def emit_batch(self, events) -> None:
            if events[0].kind == RuntimeEventKind.TOOL_START:
                stalled.set()
                if not release.wait(timeout=2.0):
                    raise TimeoutError("tool ACK was not released")
            super().emit_batch(events)

    control = StalledSink()
    queued = QueuedRuntimeEventSink(control, max_pending=1)
    adapter = DeepAgentsRuntimeAdapter(
        CollectingSink(),
        BeliefKVRequestMetadata("wf", "root", "ctx", 0),
        control_sink=queued,
    )
    try:
        adapter.start()
        task = adapter.declare_runtime_tasks([("explorer", "Inspect")])[0]
        chain = uuid4()
        adapter.on_chain_start(
            {}, {}, run_id=chain, metadata=adapter.invocation_scope(task),
        )
        model_run = uuid4()
        adapter.on_chat_model_start(
            {}, [[HumanMessage(content="child")]],
            run_id=model_run, parent_run_id=chain,
        )
        tool_start = adapter._event(RuntimeEventKind.TOOL_START, invocation_id="root")
        adapter._publish((tool_start,), control=True)
        assert stalled.wait(timeout=1.0)
        adapter._publish(
            (adapter._event(RuntimeEventKind.TOOL_START, invocation_id="root"),),
            control=True,
        )
        began = time.monotonic()
        adapter.on_llm_end(
            _child_completion_result("ChildCompletion"),
            run_id=model_run, parent_run_id=chain,
        )
        assert time.monotonic() - began < 0.5
        assert adapter.control_delivery_summary()["degraded"] is True
        assert adapter.control_delivery_summary()["last_failure"]["error_type"] == "RuntimeError"
        release.set()
    finally:
        release.set()
        queued.close()
    assert not any(
        event.kind == RuntimeEventKind.STRUCTURED_ACTION
        for event in control.events
    )
    assert queued.timing_summary()["tool_start_count"] == 2


def test_ordinary_tool_start_does_not_wait_for_control_ack() -> None:
    ack_started = threading.Event()
    release_ack = threading.Event()

    class SlowControlSink(CollectingSink):
        def emit_batch(self, events) -> None:
            if events[0].kind == RuntimeEventKind.TOOL_START:
                ack_started.set()
                if not release_ack.wait(timeout=2.0):
                    raise TimeoutError("tool-start delivery was not released")
            super().emit_batch(events)

        def close(self) -> None:
            pass

    trace = CollectingSink()
    control = SlowControlSink()
    queued = QueuedRuntimeEventSink(control)
    metadata = BeliefKVRequestMetadata(
        root_workflow_id="wf",
        invocation_id="root",
        context_id="ctx",
        context_epoch=0,
    )
    adapter = DeepAgentsRuntimeAdapter(trace, metadata, control_sink=queued)
    start = adapter._event(
        RuntimeEventKind.TOOL_START, invocation_id="root"
    )
    end = adapter._event(
        RuntimeEventKind.TOOL_END, invocation_id="root"
    )
    try:
        began = time.monotonic()
        adapter._publish((start,), control=True)
        assert time.monotonic() - began < 0.5
        assert ack_started.wait(timeout=1.0)
        assert trace.events == [start]
        completed = threading.Event()

        def publish_end() -> None:
            adapter._publish((end,), control=True)
            completed.set()

        sender = threading.Thread(target=publish_end)
        sender.start()
        assert not completed.wait(timeout=0.05)
        release_ack.set()
        sender.join(timeout=2.0)
        assert not sender.is_alive()
        assert control.events == [start, end]
        assert not adapter.control_delivery_summary()["degraded"]
    finally:
        release_ack.set()
        queued.close()


class QueueToolCallingModel(BaseChatModel):
    responses: list[AIMessage]

    @property
    def _llm_type(self) -> str:
        return "queue-tool-calling-model"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable:
        del tools, tool_choice, kwargs
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        if not self.responses:
            raise AssertionError("fake model response queue is empty")
        return ChatResult(
            generations=[ChatGeneration(message=self.responses.pop(0))]
        )


def test_explicit_call_censor_preserves_runtime_identity() -> None:
    sink = CollectingSink()
    root = BeliefKVRequestMetadata(
        root_workflow_id="workflow",
        invocation_id="root",
        context_id="context",
        context_epoch=0,
        agent_definition_id="supervisor",
        agent_instance_id="supervisor",
    )
    adapter = DeepAgentsRuntimeAdapter(sink, root, clock_ms=lambda: 1.0)
    adapter.start()
    adapter.record_call_censor(
        {
            "call_kind": "tool",
            "censor_reason": "duplicate_suppressed",
            "tool_call_id": "tool-1",
            "invocation_id": "root",
        }
    )
    event = next(item for item in sink.events if item.kind == RuntimeEventKind.CALL_CENSORED)
    assert event.invocation_id == "root"
    assert event.context_id == "context"
    assert event.attributes["tool_call_id"] == "tool-1"
    assert event.attributes["invocation_identity_fallback"] is False


def test_deepagents_task_callbacks_form_replayable_parent_child_join() -> None:
    trace_sink = CollectingSink()
    control_sink = CollectingSink()
    ticks = itertools.count(1)
    root = BeliefKVRequestMetadata(
        root_workflow_id="wf-deepagents",
        invocation_id="root",
        context_id="ctx-root",
        context_epoch=0,
        agent_definition_id="supervisor",
        agent_instance_id="supervisor-1",
    )
    adapter = DeepAgentsRuntimeAdapter(
        trace_sink,
        root,
        control_sink=control_sink,
        clock_ms=lambda: float(next(ticks)),
    )
    model = QueueToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": "Inspect the parser and report findings.",
                            "subagent_type": "general-purpose",
                        },
                        "id": "task-call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="The parser has one relevant branch."),
            AIMessage(content="Integrated the subagent report."),
        ]
    )
    agent = create_deep_agent(
        model=model,
        tools=[],
        system_prompt="Delegate repository investigation with task().",
        name="beliefkv-supervisor",
    )

    adapter.start()
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "Inspect the parser."}]},
        config={"callbacks": [adapter], "recursion_limit": 20},
    )
    adapter.finish(outcome="completed")

    assert result["messages"][-1].text == "Integrated the subagent report."
    task_results = [
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage)
        and str(message.tool_call_id) == "task-call-1"
    ]
    assert len(task_results) == 1
    kinds = [event.kind for event in trace_sink.events]
    assert kinds.count(RuntimeEventKind.SPAWN) == 1
    assert kinds.count(RuntimeEventKind.JOIN_CREATE) == 1
    assert kinds.count(RuntimeEventKind.JOIN_WAIT) == 1
    assert kinds.count(RuntimeEventKind.JOIN_SATISFIED) == 1
    assert kinds.count(RuntimeEventKind.LLM_SUBMIT) == 3
    assert kinds.count(RuntimeEventKind.LLM_RESULT) == 3
    gate = adapter.semantic_gate_result()
    assert gate is not None
    assert gate["join_id"].startswith("deepagents-join:")
    assert gate["parent_invocation_id"] == "root"
    assert gate["parent_context_id"] == "ctx-root"
    assert gate["parent_context_epoch"] == 1
    assert gate["request_id"].startswith("beliefkv:")
    model_events = [
        event
        for event in trace_sink.events
        if event.kind in {RuntimeEventKind.LLM_SUBMIT, RuntimeEventKind.LLM_RESULT}
    ]
    assert all(
        str(event.attributes["request_id"]).startswith("beliefkv:")
        for event in model_events
    )

    child_create = next(
        event
        for event in trace_sink.events
        if event.kind == RuntimeEventKind.INVOCATION_CREATE
        and event.invocation_id != "root"
    )
    child_llm = next(
        event
        for event in trace_sink.events
        if event.kind == RuntimeEventKind.LLM_SUBMIT
        and event.invocation_id == child_create.invocation_id
    )
    assert child_llm.context_id == child_create.context_id
    assert child_create.attributes["persistent"] is True
    assert child_create.attributes["description_chars"] > 0
    assert "description" not in child_create.attributes

    graph = RuntimeCausalContextGraph()
    graph.apply_batch(trace_sink.events)
    assert graph.invocations["root"].state == InvocationState.DONE
    assert (
        graph.invocations[child_create.invocation_id].state
        == InvocationState.DONE
    )

    control_kinds = [event.kind for event in control_sink.events]
    assert RuntimeEventKind.WORKFLOW_START not in control_kinds
    assert RuntimeEventKind.LLM_SUBMIT not in control_kinds
    assert RuntimeEventKind.SPAWN in control_kinds
    assert RuntimeEventKind.JOIN_WAIT in control_kinds

def test_parallel_task_declaration_uses_one_all_join() -> None:
    trace_sink = CollectingSink()
    root = BeliefKVRequestMetadata("wf", "root", "ctx", 0, "supervisor", "root")
    adapter = DeepAgentsRuntimeAdapter(trace_sink, root)
    adapter.start()

    tasks = adapter.declare_runtime_tasks(
        [
            ("explorer", "Inspect package A"),
            ("tester", "Inspect tests B"),
        ],
        group_id="model-run",
    )

    join = next(
        event
        for event in trace_sink.events
        if event.kind == RuntimeEventKind.JOIN_CREATE
    )
    assert len(join.member_invocation_ids) == 2
    assert len(tasks) == 2
    assert join.attributes["mode"] == "all"
    assert sum(event.kind == RuntimeEventKind.JOIN_WAIT for event in trace_sink.events) == 1


def test_code_orchestrator_can_bind_dynamic_child_runs() -> None:
    trace_sink = CollectingSink()
    control_sink = CollectingSink()
    ticks = itertools.count(1)
    root = BeliefKVRequestMetadata(
        root_workflow_id="wf-planned",
        invocation_id="root",
        context_id="ctx-root",
        context_epoch=0,
        agent_definition_id="planner",
        agent_instance_id="planner-1",
    )
    adapter = DeepAgentsRuntimeAdapter(
        trace_sink,
        root,
        control_sink=control_sink,
        clock_ms=lambda: float(next(ticks)),
    )
    adapter.start()
    tasks = adapter.declare_runtime_tasks(
        [
            ("repository-explorer", "Inspect the implementation."),
            ("test-analyst", "Find relevant tests."),
        ],
        group_id="plan-1",
    )

    chain_run_id = uuid4()
    model_run_id = uuid4()
    adapter.on_chain_start(
        {},
        {},
        run_id=chain_run_id,
        metadata=adapter.invocation_scope(tasks[0]),
    )
    adapter.on_chat_model_start(
        {},
        [[HumanMessage(content="inspect")]],
        run_id=model_run_id,
        parent_run_id=chain_run_id,
    )
    metadata = adapter.metadata_for_model_run(model_run_id)
    assert metadata.invocation_id == tasks[0].invocation_id
    assert metadata.context_id == tasks[0].context_id

    adapter.complete_runtime_task(tasks[0])
    adapter.complete_runtime_task(tasks[1])
    adapter.finish(outcome="completed")
    kinds = [event.kind for event in trace_sink.events]
    assert kinds.count(RuntimeEventKind.SPAWN) == 2
    assert kinds.count(RuntimeEventKind.JOIN_SATISFIED) == 1
    assert kinds.count(RuntimeEventKind.RETURN) == 3


def test_model_submit_records_semantic_prompt_contract_and_sampling_seed() -> None:
    sink = CollectingSink()
    adapter = DeepAgentsRuntimeAdapter(
        sink,
        BeliefKVRequestMetadata("wf", "root", "ctx", 0, "supervisor", "root"),
    )
    adapter.start()
    messages = [[HumanMessage(content="inspect")]]
    contract = {
        "seed": 17,
        "model": "test-model",
        "max_tokens": 1024,
        "tools": [{"type": "function", "function": {"name": "search"}}],
    }

    adapter.on_chat_model_start(
        {},
        messages,
        run_id=uuid4(),
        invocation_params=contract,
    )
    first = [
        event for event in sink.events if event.kind == RuntimeEventKind.LLM_SUBMIT
    ][-1]
    adapter.on_chat_model_start(
        {},
        messages,
        run_id=uuid4(),
        invocation_params=dict(contract),
    )
    second = [
        event for event in sink.events if event.kind == RuntimeEventKind.LLM_SUBMIT
    ][-1]

    assert first.attributes["sampling_seed"] == 17
    assert first.attributes["prompt_semantic_sha256"] == second.attributes[
        "prompt_semantic_sha256"
    ]

    adapter.on_chat_model_start(
        {},
        messages,
        run_id=uuid4(),
        invocation_params={**contract, "max_tokens": 2048},
    )
    changed = [
        event for event in sink.events if event.kind == RuntimeEventKind.LLM_SUBMIT
    ][-1]
    assert changed.attributes["prompt_semantic_sha256"] != first.attributes[
        "prompt_semantic_sha256"
    ]


def test_cancelled_runtime_task_does_not_satisfy_join() -> None:
    sink = CollectingSink()
    root = BeliefKVRequestMetadata("wf", "root", "ctx", 0, "supervisor", "root")
    adapter = DeepAgentsRuntimeAdapter(sink, root)
    adapter.start()
    tasks = adapter.declare_runtime_tasks(
        [("explorer", "Inspect"), ("tester", "Test")],
        group_id="cancelled-group",
    )

    adapter.complete_runtime_task(tasks[0])
    adapter.complete_runtime_task(tasks[1], error=TimeoutError("deadline"))

    kinds = [event.kind for event in sink.events]
    assert kinds.count(RuntimeEventKind.RETURN) == 1
    assert kinds.count(RuntimeEventKind.INVOCATION_CANCEL) == 1
    assert RuntimeEventKind.JOIN_SATISFIED not in kinds
    assert kinds.count(RuntimeEventKind.JOIN_TIMEOUT) == 1


def test_deadline_cancels_pending_children_in_one_control_batch() -> None:
    trace_sink = CollectingSink()
    control_sink = BatchCollectingSink()
    root = BeliefKVRequestMetadata(
        "wf",
        "root",
        "ctx",
        0,
        "supervisor",
        "root",
    )
    adapter = DeepAgentsRuntimeAdapter(
        trace_sink,
        root,
        control_sink=control_sink,
    )
    adapter.start()
    tasks = adapter.declare_runtime_tasks(
        [
            ("explorer", "Inspect"),
            ("tester", "Test"),
            ("reviewer", "Review"),
        ],
        group_id="deadline-group",
    )
    control_sink.events.clear()
    control_sink.batches.clear()

    assert adapter.cancel_pending_tasks(reason="deadline") == len(tasks)

    assert len(control_sink.batches) == 1
    kinds = [event.kind for event in control_sink.batches[0]]
    assert kinds.count(RuntimeEventKind.INVOCATION_CANCEL) == 3
    assert kinds.count(RuntimeEventKind.JOIN_TIMEOUT) == 1


def test_late_event_for_terminal_child_stays_out_of_control_graph() -> None:
    trace_sink = CollectingSink()
    control_sink = CollectingSink()
    root = BeliefKVRequestMetadata(
        "wf",
        "root",
        "ctx",
        0,
        "supervisor",
        "root",
    )
    adapter = DeepAgentsRuntimeAdapter(
        trace_sink,
        root,
        control_sink=control_sink,
    )
    adapter.start()
    task = adapter.declare_runtime_tasks(
        [("explorer", "Inspect")],
        group_id="terminal-race",
    )[0]
    adapter.complete_runtime_task(task, error=TimeoutError("deadline"))
    control_sink.events.clear()

    late = adapter._event(
        RuntimeEventKind.TOOL_END,
        invocation_id=task.invocation_id,
        context_id=task.context_id,
    )
    adapter._publish((late,), control=True)

    assert late in trace_sink.events
    assert control_sink.events == []
    assert adapter.control_delivery_summary()[
        "late_terminal_event_suppressed_count"
    ] == 1


def test_chat_client_uses_remaining_deadline_and_aborts_failed_request(
    monkeypatch,
) -> None:
    sink = CollectingSink()
    root = BeliefKVRequestMetadata("wf", "root", "ctx", 0, "supervisor", "root")
    adapter = DeepAgentsRuntimeAdapter(sink, root)
    adapter.start()
    model_run_id = uuid4()
    adapter.on_chat_model_start(
        {},
        [[HumanMessage(content="inspect")]],
        run_id=model_run_id,
    )
    now = [10.0]
    deadline = ActivationDeadline(clock=lambda: now[0])
    deadline.start(20.0)
    now[0] = 14.0
    client = BeliefKVChatOpenAI(
        beliefkv_adapter=adapter,
        activation_deadline=deadline,
        request_timeout_s=900.0,
        abort_url="http://127.0.0.1:30000/abort_request",
        model="test-model",
        base_url="http://127.0.0.1:30000/v1",
        api_key="EMPTY",
        max_retries=0,
    )
    run_manager = SimpleNamespace(run_id=model_run_id)
    payload, rid = client._with_beliefkv_runtime(run_manager, {})
    assert payload["timeout"] == 16.0
    assert payload["extra_body"]["rid"] == rid
    assert (
        payload["extra_body"]["beliefkv_metadata"]["execution_timeout_s"] == 16.0
    )
    assert payload["extra_body"]["beliefkv_metadata"]["invocation_id"] == "root"

    aborted: list[tuple[str, dict[str, str], float]] = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            del args

    def fake_urlopen(request, *, timeout):
        aborted.append(
            (request.full_url, json.loads(request.data.decode("utf-8")), timeout)
        )
        return _Response()

    def fail_generate(self, messages, stop=None, run_manager=None, **kwargs):
        del self, messages, stop, run_manager, kwargs
        raise TimeoutError("request timed out")

    monkeypatch.setattr(
        "beliefkv.runtime.deepagents_adapter.urllib.request.urlopen", fake_urlopen
    )
    monkeypatch.setattr(ChatOpenAI, "_generate", fail_generate)
    with pytest.raises(TimeoutError, match="request timed out"):
        client._generate([], run_manager=run_manager)

    assert aborted == [
        (
            "http://127.0.0.1:30000/abort_request",
            {"rid": rid},
            1.0,
        )
    ]


def test_ordinary_tool_boundaries_keep_the_model_tool_call_id() -> None:
    sink = CollectingSink()
    root = BeliefKVRequestMetadata("wf", "root", "ctx", 0, "supervisor", "root")
    adapter = DeepAgentsRuntimeAdapter(sink, root)
    adapter.start()
    run_id = uuid4()
    adapter.on_tool_start(
        {"name": "read_file"},
        "",
        run_id=run_id,
        inputs={"file_path": "/module.py"},
        tool_call_id="call-from-model",
    )
    adapter.on_tool_end("contents", run_id=run_id)
    boundaries = [
        event
        for event in sink.events
        if event.kind in {RuntimeEventKind.TOOL_START, RuntimeEventKind.TOOL_END}
    ]
    assert [item.attributes["tool_call_id"] for item in boundaries] == [
        "call-from-model",
        "call-from-model",
    ]


def test_tool_end_preserves_status_error_class_and_workspace_change() -> None:
    sink = CollectingSink()
    root = BeliefKVRequestMetadata("wf", "root", "ctx", 0, "supervisor", "root")
    digests = iter(("before", "after"))
    adapter = DeepAgentsRuntimeAdapter(
        sink,
        root,
        workspace_digest_provider=lambda name, payload: next(digests),
    )
    adapter.start()
    run_id = uuid4()
    adapter.on_tool_start(
        {"name": "edit_file"},
        "",
        run_id=run_id,
        inputs={"file_path": "/workspace/module.py", "old_string": "old"},
        tool_call_id="edit-call",
    )
    adapter.on_tool_end(
        ToolMessage(
            content="Error: String not found in file: 'old'",
            name="edit_file",
            tool_call_id="edit-call",
            status="error",
        ),
        run_id=run_id,
    )

    ended = next(event for event in sink.events if event.kind == RuntimeEventKind.TOOL_END)
    assert ended.attributes["status"] == "error"
    assert ended.attributes["tool_error_class"] == "string_not_found"
    assert ended.attributes["workspace_digest_before"] == "before"
    assert ended.attributes["workspace_digest_after"] == "after"
    assert ended.attributes["workspace_changed"] is True


def test_structured_completion_is_not_counted_as_an_external_tool() -> None:
    sink = CollectingSink()
    root = BeliefKVRequestMetadata("wf", "root", "ctx", 0, "supervisor", "root")
    adapter = DeepAgentsRuntimeAdapter(
        sink,
        root,
        event_namespace="agenticroot",
        completion_tool_names=frozenset({"WorkflowCompletion"}),
    )
    run_id = uuid4()

    adapter.on_tool_start(
        {"name": "WorkflowCompletion"},
        "",
        run_id=run_id,
        inputs={"status": "blocked"},
        tool_call_id="completion-call",
    )
    adapter.on_tool_end("accepted", run_id=run_id)

    assert not any(
        event.kind in {RuntimeEventKind.TOOL_START, RuntimeEventKind.TOOL_END}
        for event in sink.events
    )


def test_unsupported_subagent_type_does_not_create_a_physical_child() -> None:
    sink = CollectingSink()
    root = BeliefKVRequestMetadata("wf", "root", "ctx", 0, "supervisor", "root")
    adapter = DeepAgentsRuntimeAdapter(
        sink,
        root,
        allowed_subagent_types=frozenset({"repository-explorer"}),
    )
    model_run_id = uuid4()
    tool_run_id = uuid4()
    adapter.on_llm_end(
        LLMResult(
            generations=[
                [
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "task",
                                    "args": {
                                        "description": "Inspect the repository",
                                        "subagent_type": "general-purpose",
                                    },
                                    "id": "invalid-task",
                                }
                            ],
                        )
                    )
                ]
            ]
        ),
        run_id=model_run_id,
    )
    adapter.on_tool_start(
        {"name": "task"},
        "",
        run_id=tool_run_id,
        inputs={
            "description": "Inspect the repository",
            "subagent_type": "general-purpose",
        },
        tool_call_id="invalid-task",
    )
    adapter.on_tool_end("unsupported type", run_id=tool_run_id)

    assert not any(
        event.kind
        in {
            RuntimeEventKind.INVOCATION_CREATE,
            RuntimeEventKind.SPAWN,
            RuntimeEventKind.JOIN_CREATE,
        }
        for event in sink.events
    )
    result = next(event for event in sink.events if event.kind == RuntimeEventKind.LLM_RESULT)
    assert result.attributes["rejected_task_call_count"] == 1


def test_adapter_event_namespace_prevents_cross_peer_id_collisions() -> None:
    first_sink = CollectingSink()
    second_sink = CollectingSink()
    first = DeepAgentsRuntimeAdapter(
        first_sink,
        BeliefKVRequestMetadata("wf", "root-a", "ctx-a", 0),
        event_namespace="peera",
    )
    second = DeepAgentsRuntimeAdapter(
        second_sink,
        BeliefKVRequestMetadata("wf", "root-b", "ctx-b", 0),
        event_namespace="peerb",
    )

    first.start()
    second.start()

    first_ids = {event.event_id for event in first_sink.events}
    second_ids = {event.event_id for event in second_sink.events}
    assert first_ids.isdisjoint(second_ids)


def test_declared_join_ids_are_scoped_to_the_workflow() -> None:
    first_sink = CollectingSink()
    second_sink = CollectingSink()
    first = DeepAgentsRuntimeAdapter(
        first_sink,
        BeliefKVRequestMetadata("wf-a", "root-a", "ctx-a", 0),
    )
    second = DeepAgentsRuntimeAdapter(
        second_sink,
        BeliefKVRequestMetadata("wf-b", "root-b", "ctx-b", 0),
    )
    first.start()
    second.start()
    first_tasks = first.declare_runtime_tasks(
        [("analysis", "Inspect")], group_id="same-logical-group"
    )
    second_tasks = second.declare_runtime_tasks(
        [("analysis", "Inspect")], group_id="same-logical-group"
    )
    assert first_tasks[0].join_id != second_tasks[0].join_id


def test_control_delivery_failure_does_not_change_workflow_trajectory() -> None:
    trace_sink = CollectingSink()
    root = BeliefKVRequestMetadata(
        "wf-control-loss", "root", "ctx-root", 0, "supervisor", "root"
    )
    adapter = DeepAgentsRuntimeAdapter(
        trace_sink,
        root,
        control_sink=FailingControlSink(),
    )

    adapter.start()
    tasks = adapter.declare_runtime_tasks(
        [("explorer", "Inspect the implementation")],
        group_id="control-loss",
    )
    adapter.complete_runtime_task(tasks[0])
    adapter.finish(outcome="completed")

    kinds = [event.kind for event in trace_sink.events]
    assert RuntimeEventKind.SPAWN in kinds
    assert RuntimeEventKind.JOIN_SATISFIED in kinds
    assert RuntimeEventKind.WORKFLOW_END in kinds
    summary = adapter.control_delivery_summary()
    assert summary["degraded"] is True
    assert summary["failure_count"] >= 3
    assert summary["first_failure"]["error_type"] == "ConnectionError"


def test_context_compaction_advances_epoch_before_next_model_submit() -> None:
    trace_sink = CollectingSink()
    control_sink = CollectingSink()
    root = BeliefKVRequestMetadata("wf", "root", "ctx", 0, "supervisor", "root")
    adapter = DeepAgentsRuntimeAdapter(
        trace_sink,
        root,
        control_sink=control_sink,
    )
    adapter.start()
    first_run = uuid4()
    adapter.on_chat_model_start(
        {},
        [[HumanMessage(content="first")]],
        run_id=first_run,
    )
    adapter.on_llm_end(
        LLMResult(
            generations=[[ChatGeneration(message=AIMessage(content="continue"))]]
        ),
        run_id=first_run,
    )

    record = ContextCompactionRecord(
        source_message_count=40,
        retained_message_count=8,
        summary_chars=512,
        summary_sha256="a" * 64,
        trigger_tokens=24_576,
        keep_tokens=8_192,
    )
    second_run = uuid4()
    with adapter.stage_context_compaction(record):
        adapter.on_chat_model_start(
            {},
            [[HumanMessage(content="checkpoint"), HumanMessage(content="recent")]],
            run_id=second_run,
        )

    compact = next(
        event
        for event in trace_sink.events
        if event.kind == RuntimeEventKind.CONTEXT_COMPACT
    )
    second_submit = [
        event
        for event in trace_sink.events
        if event.kind == RuntimeEventKind.LLM_SUBMIT
    ][-1]
    assert trace_sink.events.index(compact) < trace_sink.events.index(second_submit)
    assert compact.context_id == "ctx"
    assert compact.context_epoch == 1
    assert compact.attributes["previous_context_epoch"] == 0
    assert compact.attributes["old_kv_disposition"] == "release_ownership"
    assert compact in control_sink.events

    graph = RuntimeCausalContextGraph()
    graph.apply_batch(trace_sink.events)
    assert graph.contexts["ctx"].epoch == 1


def test_summary_model_call_has_ephemeral_runtime_internal_context() -> None:
    trace_sink = CollectingSink()
    control_sink = CollectingSink()
    root = BeliefKVRequestMetadata("wf", "root", "ctx", 0, "supervisor", "root")
    adapter = DeepAgentsRuntimeAdapter(
        trace_sink,
        root,
        control_sink=control_sink,
    )
    adapter.start()
    summary_run = uuid4()
    adapter.on_chat_model_start(
        {},
        [[HumanMessage(content="summarize")]],
        run_id=summary_run,
        metadata={"lc_source": "summarization"},
    )
    metadata = adapter.metadata_for_model_run(summary_run)
    adapter.on_llm_end(
        LLMResult(
            generations=[[ChatGeneration(message=AIMessage(content="checkpoint"))]]
        ),
        run_id=summary_run,
    )

    assert metadata.invocation_id.startswith("root:context-summary:")
    assert metadata.context_id.startswith("ctx:context-summary:")
    assert metadata.context_id != "ctx"
    internal = [
        event
        for event in trace_sink.events
        if bool(event.attributes.get("runtime_internal"))
    ]
    assert {event.kind for event in internal} >= {
        RuntimeEventKind.INVOCATION_CREATE,
        RuntimeEventKind.CALL,
        RuntimeEventKind.LLM_SUBMIT,
        RuntimeEventKind.LLM_RESULT,
        RuntimeEventKind.RETURN,
    }
    assert all(
        event.invocation_id != "root"
        for event in internal
        if event.kind in {RuntimeEventKind.LLM_SUBMIT, RuntimeEventKind.LLM_RESULT}
    )

    graph = RuntimeCausalContextGraph()
    graph.apply_batch(trace_sink.events)
    assert graph.invocations[metadata.invocation_id].state == InvocationState.DONE
    assert graph.invocations["root"].state == InvocationState.READY


def test_context_lifecycle_runs_summary_then_compacts_parent(tmp_path) -> None:
    trace_sink = CollectingSink()
    control_sink = CollectingSink()
    root = BeliefKVRequestMetadata("wf", "root", "ctx", 0, "supervisor", "root")
    adapter = DeepAgentsRuntimeAdapter(
        trace_sink,
        root,
        control_sink=control_sink,
    )
    main_model = QueueToolCallingModel(
        responses=[AIMessage(content="done"), AIMessage(content="done again")]
    )
    summary_model = QueueToolCallingModel(
        responses=[AIMessage(content="durable checkpoint")],
    )
    policy = ContextLifecyclePolicy(
        window_tokens=1_000,
        keep_tokens=100,
        intermediate_output_tokens=100,
        summary_output_tokens=200,
    )
    context_lifecycle = ContextLifecycleMiddleware(
        summary_model,
        backend=FilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        policy=policy,
        compaction_sink=adapter,
        summary_callbacks=(adapter,),
        persist_cursor_across_invocations=True,
    )
    agent = create_agent(
        model=main_model,
        tools=[],
        middleware=[context_lifecycle],
    )

    adapter.start()
    agent_config = {"callbacks": [adapter], "recursion_limit": 10}
    result = agent.invoke(
        {
            "messages": [
                {"role": "user", "content": "x" * 2_000},
                {"role": "assistant", "content": "y" * 2_000},
                {"role": "user", "content": "z" * 2_000},
            ]
        },
        config=agent_config,
    )

    assert result["messages"][-1].text == "done"
    kinds = [event.kind for event in trace_sink.events]
    assert kinds.count(RuntimeEventKind.CONTEXT_COMPACT) == 1
    internal_summary_count = sum(
        event.kind == RuntimeEventKind.LLM_SUBMIT
        and bool(event.attributes.get("runtime_internal"))
        for event in trace_sink.events
    )
    assert internal_summary_count == 1
    compact_index = kinds.index(RuntimeEventKind.CONTEXT_COMPACT)
    parent_submit_index = max(
        index
        for index, event in enumerate(trace_sink.events)
        if event.kind == RuntimeEventKind.LLM_SUBMIT
        and not bool(event.attributes.get("runtime_internal"))
    )
    assert compact_index < parent_submit_index
    assert any(
        event.kind == RuntimeEventKind.CONTEXT_COMPACT
        for event in control_sink.events
    )

    assert result.get("_summarization_event") is None
    summarization_event = context_lifecycle.latest_summarization_event()
    assert summarization_event is not None
    second_result = agent.invoke(
        {
            "messages": [
                *result["messages"],
                {"role": "user", "content": "small follow-up"},
            ],
        },
        config=agent_config,
    )
    assert second_result["messages"][-1].text == "done again"
    assert sum(
        event.kind == RuntimeEventKind.CONTEXT_COMPACT
        for event in trace_sink.events
    ) == 1


def test_failed_summary_does_not_publish_context_compaction(tmp_path) -> None:
    trace_sink = CollectingSink()
    root = BeliefKVRequestMetadata("wf", "root", "ctx", 0, "supervisor", "root")
    adapter = DeepAgentsRuntimeAdapter(trace_sink, root)
    middleware = ContextLifecycleMiddleware(
        QueueToolCallingModel(responses=[]),
        backend=FilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        policy=ContextLifecyclePolicy(),
        compaction_sink=adapter,
        summary_callbacks=(adapter,),
    )

    adapter.start()
    with pytest.raises(AssertionError, match="response queue is empty"):
        middleware._create_summary([HumanMessage(content="history")])

    assert not any(
        event.kind == RuntimeEventKind.CONTEXT_COMPACT
        for event in trace_sink.events
    )
    assert any(
        event.kind == RuntimeEventKind.INVOCATION_CANCEL
        and bool(event.attributes.get("runtime_internal"))
        for event in trace_sink.events
    )
