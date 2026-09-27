from __future__ import annotations

import threading
import time
from uuid import uuid4

import pytest

pytest.importorskip("deepagents")

from beliefkv.control.causal_graph import RuntimeCausalContextGraph
from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.experiments.p6_decision_points import _event_triggers
from beliefkv.predictor.command_class import (
    execute_command_class, execute_command_shape,
)
from beliefkv.predictor.project_tool_history import ProjectToolHistory
from beliefkv.runtime.deepagents_adapter import DeepAgentsRuntimeAdapter
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata
from beliefkv.runtime.tool_wait_shadow import ToolWaitShadowTimer


class _Sink:
    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []

    def emit_batch(self, events: tuple[RuntimeEvent, ...]) -> None:
        self.events.extend(events)


COMMAND = "python -c 'print(1)'"


def _history(now: float) -> ProjectToolHistory:
    history = ProjectToolHistory()
    for index in range(4):
        start = now - 40_000 + index * 4_000
        call_id = f"seed-{index}"
        workflow = f"prior-{index}"
        history.start(workflow, "repo", {
            "tool_call_id": call_id, "tool_name": "execute",
            "observed_command_class": execute_command_class({"command": COMMAND}),
            "observed_command_shape": execute_command_shape({"command": COMMAND}),
            "is_child": True, "input_chars": 100,
        }, start)
        history.end(workflow, {
            "tool_call_id": call_id, "status": "success",
        }, start + 2_400)
    return history


def _child_tool(
    now: list[float], *, timer: ToolWaitShadowTimer | None = None,
    control: _Sink | None = None,
) -> tuple[DeepAgentsRuntimeAdapter, _Sink, object]:
    trace = _Sink()
    adapter = DeepAgentsRuntimeAdapter(
        trace, BeliefKVRequestMetadata("wf", "root", "ctx", 0),
        control_sink=control, clock_ms=lambda: now[0],
        project_tool_history=_history(now[0]), project_id="repo",
        tool_wait_shadow_timer=timer,
    )
    adapter.start()
    task = adapter.declare_runtime_tasks([("explorer", "inspect")])[0]
    task_run = uuid4()
    adapter.on_tool_start(
        {"name": "task"}, "", run_id=task_run,
        inputs={"subagent_type": "explorer", "description": "inspect"},
        tool_call_id=task.tool_call_id,
    )
    tool_run = uuid4()
    adapter.on_tool_start(
        {"name": "execute"}, "", run_id=tool_run,
        parent_run_id=task_run, inputs={"command": COMMAND},
        tool_call_id="target",
    )
    return adapter, trace, tool_run


def _observations(trace: _Sink) -> list[RuntimeEvent]:
    return [
        event for event in trace.events
        if event.kind == RuntimeEventKind.TOOL_WAIT_OBSERVATION
    ]


def test_tool_wait_observation_is_causal_trace_only_and_non_mutating() -> None:
    now = [50_000.]
    control = _Sink()
    adapter, trace, run = _child_tool(now, control=control)
    tool_start = next(
        event for event in trace.events
        if event.kind == RuntimeEventKind.TOOL_START
        and event.attributes.get("tool_call_id") == "target"
    )
    assert tool_start.attributes["project_shape_survivor_500ms_support"] == 4
    assert not adapter.observe_tool_wait(
        str(run), tool_start.invocation_id, "target"
    )
    now[0] += 501
    assert not adapter.observe_tool_wait(
        str(run), tool_start.invocation_id, "wrong"
    )
    assert not adapter.observe_tool_wait(str(run), "other", "target")
    assert adapter.observe_tool_wait(
        str(run), tool_start.invocation_id, "target"
    )
    observation, = _observations(trace)
    assert observation.attributes["tool_elapsed_ms"] == 501
    assert observation.attributes["tool_wait_shape_eta_ms_p50"] == 1899
    assert observation.invocation_id == tool_start.invocation_id
    assert observation.workflow_id == "wf"
    assert all(
        event.kind != RuntimeEventKind.TOOL_WAIT_OBSERVATION
        for event in control.events
    )
    graph = RuntimeCausalContextGraph()
    deltas = graph.apply_batch(trace.events)
    assert deltas[-1].changed_contexts == frozenset()
    assert graph.invocations[observation.invocation_id].active_tool_start_ms == (
        tool_start.ts_ms
    )
    adapter.on_tool_end("done", run_id=run)
    now[0] += 50
    assert not adapter.observe_tool_wait(
        str(run), tool_start.invocation_id, "target"
    )
    adapter.finish(outcome="completed")
    assert not adapter.observe_tool_wait(
        str(run), tool_start.invocation_id, "target"
    )


def test_tool_wait_observation_rejects_error_replacement_and_finish() -> None:
    now = [50_000.]
    adapter, trace, run = _child_tool(now)
    invocation = next(
        event.invocation_id for event in trace.events
        if event.kind == RuntimeEventKind.TOOL_START
        and event.attributes.get("tool_call_id") == "target"
    )
    adapter.on_tool_error(RuntimeError("failed"), run_id=run)
    now[0] += 501
    assert not adapter.observe_tool_wait(str(run), invocation, "target")
    second = uuid4()
    adapter.on_tool_start(
        {"name": "execute"}, "", run_id=second,
        parent_run_id=next(
            key for key, value in adapter._task_run_to_call.items()
            if value
        ),
        inputs={"command": COMMAND}, tool_call_id="replacement",
    )
    assert not adapter.observe_tool_wait(str(second), invocation, "target")
    adapter.finish(outcome="error")
    assert not adapter.observe_tool_wait(str(second), invocation, "replacement")
    assert not _observations(trace)


def test_short_tool_and_parallel_tool_end_do_not_reuse_call_identity() -> None:
    now = [50_000.]
    adapter, trace, first = _child_tool(now)
    invocation = next(
        event.invocation_id for event in trace.events
        if event.kind == RuntimeEventKind.TOOL_START
        and event.attributes.get("tool_call_id") == "target"
    )
    task_run = next(iter(adapter._task_run_to_call))
    second = uuid4()
    adapter.on_tool_start(
        {"name": "execute"}, "", run_id=second,
        parent_run_id=task_run,
        inputs={"command": COMMAND}, tool_call_id="parallel",
    )
    now[0] += 100
    adapter.on_tool_end("short", run_id=first)
    now[0] += 401
    assert not adapter.observe_tool_wait(str(first), invocation, "target")
    assert adapter.observe_tool_wait(str(second), invocation, "parallel")
    assert [event.attributes["tool_call_id"] for event in _observations(trace)] == [
        "parallel"
    ]
    adapter.on_tool_end("done", run_id=second)
    assert not adapter.observe_tool_wait(str(second), invocation, "parallel")


def test_tool_wait_observation_stops_at_deadline_and_during_teardown() -> None:
    now = [50_000.]
    expired = [False]
    adapter, trace, run = _child_tool(now)
    adapter._tool_wait_shadow_expired = lambda: expired[0]
    invocation = next(
        event.invocation_id for event in trace.events
        if event.kind == RuntimeEventKind.TOOL_START
        and event.attributes.get("tool_call_id") == "target"
    )
    now[0] += 500
    expired[0] = True
    assert not adapter.observe_tool_wait(str(run), invocation, "target")
    expired[0] = False
    adapter.stop_tool_wait_shadow()
    assert not adapter.observe_tool_wait(str(run), invocation, "target")
    adapter.finish(outcome="completed")
    assert not _observations(trace)


def test_tool_wait_timer_is_shared_bounded_and_waits_for_inflight_close() -> None:
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    timer = ToolWaitShadowTimer(max_pending=1)

    def observe() -> bool:
        entered.set()
        release.wait(timeout=2)
        return True

    try:
        assert timer.schedule(time.monotonic() - 1, observe)
        assert entered.wait(1)
        assert timer.schedule(time.monotonic() + 60, lambda: False)
        assert not timer.schedule(time.monotonic() + 60, lambda: False)
        closer = threading.Thread(target=lambda: (timer.close(), closed.set()))
        closer.start()
        assert not closed.wait(.02)
        release.set()
        assert closed.wait(1)
        closer.join()
        assert timer.summary() == {
            "published": 1, "dropped": 1, "errors": 0, "pending": 0,
        }
    finally:
        release.set()
        timer.close()


def test_tool_wait_timer_dispatches_live_call_at_landmark() -> None:
    with ToolWaitShadowTimer() as timer:
        now = [time.monotonic() * 1000.]
        adapter, trace, run = _child_tool(now, timer=timer)
        # The production adapter clock advances with monotonic time.
        adapter.clock_ms = lambda: time.monotonic() * 1000.
        deadline = time.monotonic() + 1.5
        while not _observations(trace) and time.monotonic() < deadline:
            time.sleep(.01)
        assert len(_observations(trace)) == 1
        adapter.finish(outcome="completed")
        time.sleep(.02)
        assert len(_observations(trace)) == 1
        assert timer.summary()["published"] == 1
        adapter.on_tool_end("done", run_id=run)


def test_export_validates_tool_wait_survival_and_identity() -> None:
    events: list[RuntimeEvent] = []
    metadata = {}
    for index in range(4):
        wf = f"prior-{index}"
        metadata[wf] = {"project": "repo"}
        start = index * 4_000.
        attrs = {
            "tool_call_id": f"seed-{index}", "tool_name": "execute",
            "observed_command_class": execute_command_class({"command": COMMAND}),
            "observed_command_shape": execute_command_shape({"command": COMMAND}),
            "is_child": True, "input_chars": 100,
        }
        events.extend((
            RuntimeEvent(f"start-{index}", start, RuntimeEventKind.TOOL_START,
                         wf, invocation_id="child", attributes=attrs),
            RuntimeEvent(f"end-{index}", start + 2400, RuntimeEventKind.TOOL_END,
                         wf, invocation_id="child", attributes={
                             "tool_call_id": f"seed-{index}", "status": "success",
                         }),
        ))
    metadata["target"] = {"project": "repo"}
    target = RuntimeEvent(
        "start-target", 20_000, RuntimeEventKind.TOOL_START, "target",
        invocation_id="child", attributes={
            "tool_call_id": "target", "tool_name": "execute", "is_child": True,
            "observed_command_class": execute_command_class({"command": COMMAND}),
            "observed_command_shape": execute_command_shape({"command": COMMAND}),
            "input_chars": 100,
        },
    )
    observation = RuntimeEvent(
        "wait-target", 20_501, RuntimeEventKind.TOOL_WAIT_OBSERVATION, "target",
        invocation_id="child", attributes={
            "tool_call_id": "target", "tool_elapsed_ms": 501,
            "project_shape_survivor_500ms_total_median_ms": 2400,
            "project_shape_survivor_500ms_support": 4,
            "tool_wait_shape_eta_ms_p50": 1899,
        },
    )
    replayed = _event_triggers([*events, target, observation],
                               workflow_metadata=metadata)
    assert replayed[-1]["kind"] == "tool_wait_observation"
    assert replayed[-1]["attributes"]["tool_wait_shape_eta_ms_p50"] == 1899
    with pytest.raises(ValueError, match="causal history"):
        _event_triggers([*events, target, RuntimeEvent(
            "bad", 20_501, RuntimeEventKind.TOOL_WAIT_OBSERVATION,
            "target", invocation_id="wrong", attributes=observation.attributes,
        )], workflow_metadata=metadata)
    with pytest.raises(ValueError, match="no unique open call"):
        _event_triggers([*events, target, RuntimeEvent(
            "done", 20_500, RuntimeEventKind.TOOL_END, "target",
            invocation_id="child", attributes={
                "tool_call_id": "target", "status": "success",
            }), observation], workflow_metadata=metadata)
