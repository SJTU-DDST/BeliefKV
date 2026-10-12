"""Waiting membership follows graph events and native session rebinding."""

from types import SimpleNamespace as NS

import pytest

from beliefkv.control.causal_graph import CausalGraphError, InvocationState
from beliefkv.core.events import RuntimeEventKind
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_sglang_v0520_runtime import event, req


def waiting_runtime(*, bind=True):
    runtime = NativeAdmissionRuntime()
    requests = {}
    for name in ("z", "tool-z", "a", "tool-a", "child"):
        tagged = req(name)
        tagged.session_id, tagged.session_generation = f"session-{name}", 1
        requests[name] = tagged
        if bind:
            runtime.register_visible_request(tagged)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        *(event(index, RuntimeEventKind.INVOCATION_CREATE,
                invocation_id=name, context_id=f"ctx-{name}")
          for index, name in enumerate(requests, start=1)),
        event(6, RuntimeEventKind.JOIN_CREATE,
              join_id="join", member_invocation_ids=("child",)),
        event(7, RuntimeEventKind.JOIN_WAIT, invocation_id="z", join_id="join"),
        event(8, RuntimeEventKind.JOIN_WAIT, invocation_id="a", join_id="join"),
        event(9, RuntimeEventKind.TOOL_START, invocation_id="tool-z"),
        event(10, RuntimeEventKind.TOOL_START, invocation_id="tool-a"),
    ))
    return runtime, requests


def test_wait_candidates_keep_prepare_order_and_tool_refresh_rotation():
    runtime, _ = waiting_runtime()
    joins = runtime._waiting_context_keys(InvocationState.WAIT_JOIN)
    tools = runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
    assert [key.invocation_id for key in joins] == ["a", "z"]
    assert [key.invocation_id for key in tools] == ["tool-z", "tool-a"]
    assert runtime._waiting_context_keys(InvocationState.WAIT_JOIN) is joins
    assert runtime._waiting_context_keys(InvocationState.WAIT_TOOL) is tools
    runtime.on_events((
        event(11, RuntimeEventKind.TOOL_END, invocation_id="tool-z"),
        event(12, RuntimeEventKind.JOIN_SATISFIED, join_id="join"),
    ))
    assert runtime._waiting_context_keys(InvocationState.WAIT_JOIN) == ()
    assert [
        key.invocation_id for key in runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
    ] == ["tool-a"]


def test_native_binding_invalidates_an_empty_wait_snapshot_without_graph_change():
    runtime, requests = waiting_runtime(bind=False)
    assert runtime._waiting_context_keys(InvocationState.WAIT_TOOL) == ()
    version = runtime.graph.graph_version
    runtime.register_visible_request(requests["tool-a"])
    assert runtime.graph.graph_version == version
    assert [
        key.invocation_id for key in runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
    ] == ["tool-a"]


def test_requeued_session_generation_replaces_cached_wait_identity():
    runtime, requests = waiting_runtime()
    before = runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
    version = runtime.graph.graph_version
    requests["tool-a"].session_generation = 2
    runtime.on_requests_requeued((requests["tool-a"],), is_retracted=True)
    after = runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
    assert runtime.graph.graph_version == version
    assert [key.invocation_id for key in after] == ["tool-z", "tool-a"]
    assert before[-1].session_generation == 1
    assert after[-1].session_generation == 2


def test_abort_of_finished_waiting_request_removes_cached_session_without_graph_change():
    runtime, requests = waiting_runtime()
    runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
    requests["tool-a"].finished = lambda: True
    runtime.on_batch_completed(NS(reqs=(requests["tool-a"],)))
    assert "tool-a" not in runtime.visible
    assert "ctx-tool-a" in runtime.context_sessions
    version = runtime.graph.graph_version
    runtime.on_abort_request(NS(rid="tool-a", abort_all=False))
    assert runtime.graph.graph_version == version
    assert [
        key.invocation_id for key in runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
    ] == ["tool-z"]


def test_graph_failure_discards_cached_wait_candidates():
    runtime, _ = waiting_runtime()
    runtime._waiting_context_keys(InvocationState.WAIT_JOIN)
    runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
    with pytest.raises(CausalGraphError):
        runtime.on_events((event(11, RuntimeEventKind.WORKFLOW_START),))
    assert runtime._waiting_context_keys(InvocationState.WAIT_JOIN) == ()
    assert runtime._waiting_context_keys(InvocationState.WAIT_TOOL) == ()
