from __future__ import annotations

import threading
import time
from uuid import uuid4

import pytest

pytest.importorskip("deepagents")

from beliefkv.control.causal_graph import RuntimeCausalContextGraph
from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.experiments.p6_decision_points import (
    _event_triggers, build_frontier_decision_points,
)
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


def _history(now: float, *, early: bool = False) -> ProjectToolHistory:
    history = ProjectToolHistory(early_survivor_shadow=early)
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
    early: bool = False,
) -> tuple[DeepAgentsRuntimeAdapter, _Sink, object]:
    trace = _Sink()
    adapter = DeepAgentsRuntimeAdapter(
        trace, BeliefKVRequestMetadata("wf", "root", "ctx", 0),
        control_sink=control, clock_ms=lambda: now[0],
        project_tool_history=_history(now[0], early=early), project_id="repo",
        tool_wait_shadow_timer=timer,
        early_tool_wait_shadow=early,
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


def _early_observations(trace: _Sink) -> list[RuntimeEvent]:
    return [
        event for event in trace.events
        if event.kind == RuntimeEventKind.STRUCTURED_ACTION
        and event.attributes.get("beliefkv_tool_wait_early_shadow") is True
    ]


def test_early_tool_wait_is_opt_in_and_trace_only() -> None:
    now = [50_000.]
    control = _Sink()
    adapter, trace, run = _child_tool(now, control=control)
    invocation = next(
        event.invocation_id for event in trace.events
        if event.kind == RuntimeEventKind.TOOL_START
        and event.attributes.get("tool_call_id") == "target"
    )
    now[0] += 101
    assert not adapter.observe_early_tool_wait(str(run), invocation, "target")
    adapter.on_tool_end("done", run_id=run)

    now = [50_000.]
    adapter, trace, run = _child_tool(now, control=control, early=True)
    invocation = next(
        event.invocation_id for event in trace.events
        if event.kind == RuntimeEventKind.TOOL_START
        and event.attributes.get("tool_call_id") == "target"
    )
    assert not adapter.observe_early_tool_wait(str(run), invocation, "target")
    now[0] += 101
    assert not adapter.observe_early_tool_wait(str(run), invocation, "wrong")
    assert not adapter.observe_early_tool_wait(str(run), "stale", "target")
    assert adapter.observe_early_tool_wait(str(run), invocation, "target")
    early, = _early_observations(trace)
    assert early.attributes["tool_wait_shape_eta_ms_p50"] == 2299
    assert early.attributes["diagnostic_only"] is True
    assert not _early_observations(control)
    adapter.on_tool_end("done", run_id=run)
    assert not adapter.observe_early_tool_wait(str(run), invocation, "target")
    adapter.finish(outcome="completed")
    assert not adapter.observe_early_tool_wait(str(run), invocation, "target")


def test_early_tool_wait_ignores_failed_and_expired_calls() -> None:
    now = [50_000.]
    adapter, trace, run = _child_tool(now, early=True)
    invocation = next(
        event.invocation_id for event in trace.events
        if event.kind == RuntimeEventKind.TOOL_START
        and event.attributes.get("tool_call_id") == "target"
    )
    adapter._tool_wait_shadow_expired = lambda: True
    now[0] += 101
    assert not adapter.observe_early_tool_wait(str(run), invocation, "target")
    adapter._tool_wait_shadow_expired = lambda: False
    adapter.on_tool_error(RuntimeError("failed"), run_id=run)
    assert not adapter.observe_early_tool_wait(str(run), invocation, "target")
    assert not _early_observations(trace)


def test_early_timer_is_anchored_before_tool_start_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deadlines: list[tuple[float, object]] = []

    class CaptureTimer:
        def schedule(self, deadline: float, callback: object) -> bool:
            deadlines.append((deadline, callback))
            return True

    original = DeepAgentsRuntimeAdapter._publish

    def slow_publish(self, events, **kwargs):
        if any(
            event.kind == RuntimeEventKind.TOOL_START
            and event.attributes.get("tool_call_id") == "target"
            for event in events
        ):
            time.sleep(.13)
        return original(self, events, **kwargs)

    monkeypatch.setattr(DeepAgentsRuntimeAdapter, "_publish", slow_publish)
    now = [50_000.]
    adapter, trace, run = _child_tool(now, timer=CaptureTimer(), early=True)
    assert len(deadlines) == 2
    early_deadline, callback = min(deadlines)
    assert early_deadline <= time.monotonic()
    now[0] += 130
    assert callback()
    assert len(_early_observations(trace)) == 1
    adapter.on_tool_end("done", run_id=run)


def test_500ms_timer_is_anchored_before_tool_start_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deadlines: list[tuple[float, object]] = []

    class CaptureTimer:
        def schedule(self, deadline: float, callback: object) -> bool:
            deadlines.append((deadline, callback))
            return True

    original = DeepAgentsRuntimeAdapter._publish

    def slow_publish(self, events, **kwargs):
        if any(
            event.kind == RuntimeEventKind.TOOL_START
            and event.attributes.get("tool_call_id") == "target"
            for event in events
        ):
            time.sleep(.13)
        return original(self, events, **kwargs)

    monkeypatch.setattr(DeepAgentsRuntimeAdapter, "_publish", slow_publish)
    now = [50_000.]
    adapter, trace, run = _child_tool(now, timer=CaptureTimer())
    assert len(deadlines) == 1
    deadline, callback = deadlines[0]
    assert 0 < deadline - time.monotonic() < .5
    now[0] += 501
    assert callback()
    observation, = _observations(trace)
    assert observation.attributes["tool_elapsed_ms"] == 501
    adapter.on_tool_end("done", run_id=run)


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


def test_export_validates_early_shadow_causal_history() -> None:
    events: list[RuntimeEvent] = []
    metadata = {"target": {"project": "repo"}}
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
    target_attrs = {
        "tool_call_id": "target", "tool_name": "execute", "is_child": True,
        "observed_command_class": execute_command_class({"command": COMMAND}),
        "observed_command_shape": execute_command_shape({"command": COMMAND}),
        "input_chars": 100,
        "project_shape_survivor_100ms_total_median_ms": 2400,
        "project_shape_survivor_100ms_support": 4,
        "project_shape_survivor_100ms_deviation_p90_ms": 0,
    }
    target = RuntimeEvent(
        "target-start", 20_000, RuntimeEventKind.TOOL_START, "target",
        invocation_id="child", attributes=target_attrs,
    )
    attrs = {
        "beliefkv_tool_wait_early_shadow": True,
        "diagnostic_only": True, "source": "deepagents_tool_wait_shadow",
        "tool_call_id": "target", "tool_elapsed_ms": 101,
        "project_shape_survivor_100ms_total_median_ms": 2400,
        "project_shape_survivor_100ms_support": 4,
        "project_shape_survivor_100ms_deviation_p90_ms": 0,
        "tool_wait_shape_eta_ms_p50": 2299,
    }

    def early(ts: float = 20_101, **overrides: object) -> RuntimeEvent:
        return RuntimeEvent(
            f"early-{ts}", ts, RuntimeEventKind.STRUCTURED_ACTION, "target",
            invocation_id="child", attributes={**attrs, **overrides},
        )

    assert _event_triggers([*events, target, early()],
                           workflow_metadata=metadata)[-1]["kind"] == "tool_start"
    with pytest.raises(ValueError, match="early tool wait has no unique"):
        _event_triggers([*events, early()], workflow_metadata=metadata)
    with pytest.raises(ValueError, match="early tool wait has no unique"):
        _event_triggers([*events, target, early(), early(20_102)],
                        workflow_metadata=metadata)
    with pytest.raises(ValueError, match="early tool wait has no unique"):
        _event_triggers([*events, target, RuntimeEvent(
            "target-end", 20_100, RuntimeEventKind.TOOL_END, "target",
            invocation_id="child", attributes={
                "tool_call_id": "target", "status": "success",
            }), early()], workflow_metadata=metadata)
    with pytest.raises(ValueError, match="early tool wait disagrees"):
        _event_triggers([*events, target, early(20_099)],
                        workflow_metadata=metadata)
    with pytest.raises(ValueError, match="early tool wait disagrees"):
        _event_triggers([*events, target, early(tool_wait_shape_eta_ms_p50=999)],
                        workflow_metadata=metadata)
    with pytest.raises(ValueError, match="early shape history disagrees"):
        _event_triggers([
            *events,
            RuntimeEvent("bad", 20_000, RuntimeEventKind.TOOL_START, "target",
                         invocation_id="child", attributes={
                             **target_attrs,
                             "project_shape_survivor_100ms_support": 5,
                         }),
        ], workflow_metadata=metadata)


def test_p6_replay_keeps_only_valid_wait_tool_landmark() -> None:
    traces = []
    metadata = {}

    def event(wf: str, name: str, ts_ms: float, kind: RuntimeEventKind,
              *, invocation: str | None = None, **attrs: object) -> RuntimeEvent:
        return RuntimeEvent(
            f"{wf}:{name}", ts_ms, kind, wf, invocation_id=invocation,
            attributes=attrs,
        )

    for index in range(4):
        wf = f"seed-{index}"
        metadata[wf] = {"project": "repo"}
        base = index * 4_000.
        traces.append([
            event(wf, "workflow", base, RuntimeEventKind.WORKFLOW_START),
            RuntimeEvent(f"{wf}:root", base + .1, RuntimeEventKind.INVOCATION_CREATE,
                         wf, invocation_id=f"{wf}:root",
                         context_id=f"{wf}:root-ctx"),
            RuntimeEvent(f"{wf}:child", base + .2, RuntimeEventKind.INVOCATION_CREATE,
                         wf, invocation_id=f"{wf}:child",
                         context_id=f"{wf}:child-ctx",
                         parent_invocation_id=f"{wf}:root"),
            event(wf, "start", base + 1, RuntimeEventKind.TOOL_START,
                  invocation=f"{wf}:child", tool_name="execute",
                  tool_call_id=f"call-{index}", is_child=True,
                  observed_command_class=execute_command_class(
                      {"command": COMMAND}),
                  observed_command_shape=execute_command_shape(
                      {"command": COMMAND})),
            event(wf, "end", base + 2401, RuntimeEventKind.TOOL_END,
                  invocation=f"{wf}:child", tool_call_id=f"call-{index}",
                  status="success"),
            event(wf, "end-workflow", base + 2402,
                  RuntimeEventKind.WORKFLOW_END),
        ])
    wf = "target"
    metadata[wf] = {"project": "repo"}
    target = [
        event(wf, "workflow", 20_000, RuntimeEventKind.WORKFLOW_START),
        RuntimeEvent(f"{wf}:root", 20_000.1, RuntimeEventKind.INVOCATION_CREATE,
                     wf, invocation_id="root", context_id="root-ctx"),
        RuntimeEvent(f"{wf}:child", 20_000.2, RuntimeEventKind.INVOCATION_CREATE,
                     wf, invocation_id="child", context_id="child-ctx",
                     parent_invocation_id="root"),
        event(wf, "start", 20_010, RuntimeEventKind.TOOL_START,
              invocation="child", tool_name="execute", tool_call_id="target",
              is_child=True,
              observed_command_class=execute_command_class({"command": COMMAND}),
              observed_command_shape=execute_command_shape({"command": COMMAND}),
              project_shape_survivor_500ms_total_median_ms=2400.,
              project_shape_survivor_500ms_support=4),
        event(wf, "wait", 20_511, RuntimeEventKind.TOOL_WAIT_OBSERVATION,
              invocation="child", tool_call_id="target", tool_elapsed_ms=501.,
              project_shape_survivor_500ms_total_median_ms=2400.,
              project_shape_survivor_500ms_support=4,
              tool_wait_shape_eta_ms_p50=1899.),
        event(wf, "end", 20_610, RuntimeEventKind.TOOL_END,
              invocation="child", tool_call_id="target", status="success"),
        event(wf, "end-workflow", 20_611, RuntimeEventKind.WORKFLOW_END),
    ]

    def replay(extra: list[RuntimeEvent]) -> list[dict]:
        return build_frontier_decision_points(
            [[item.to_dict() for item in trace] for trace in [*traces, extra]],
            calls=[], service_rows=[], audit_records=[], transfer_records=[],
            run_id="run", workflow_metadata=metadata,
        )

    valid = [
        row for row in replay(target)
        if row["trigger_kind"] == "tool_wait_observation"
    ]
    assert len(valid) == 1
    assert valid[0]["trigger_attributes"]["tool_wait_shape_eta_ms_p50"] == 1899
    assert any(
        item["invocation_id"] == "child" and item["state"] == "wait_tool"
        for item in valid[0]["invocations"]
    )
    parallel_end = event(
        wf, "other-end", 20_510, RuntimeEventKind.TOOL_END,
        invocation="child", tool_call_id="other", status="success",
    )
    parallel_start = event(
        wf, "other-start", 20_009, RuntimeEventKind.TOOL_START,
        invocation="child", tool_name="execute", tool_call_id="other",
        is_child=True,
        observed_command_class=execute_command_class({"command": COMMAND}),
        observed_command_shape=execute_command_shape({"command": COMMAND}),
    )
    invalid = replay([*target[:3], parallel_start, target[3],
                      parallel_end, *target[4:]])
    assert not any(
        row["trigger_kind"] == "tool_wait_observation" for row in invalid
    )
