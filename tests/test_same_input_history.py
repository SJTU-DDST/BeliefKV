from __future__ import annotations

import pytest

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.experiments.p6_decision_points import _event_triggers
from beliefkv.predictor.project_tool_history import ProjectToolHistory
from beliefkv.predictor.same_input_history import SameInputToolHistory


def _attrs(call: str, *, status: str | None = None) -> dict:
    attrs = {
        "tool_name": "execute", "tool_call_id": call, "input_sha256": "sha"
    }
    if status is not None:
        attrs["status"] = status
    return attrs


def test_same_input_history_only_uses_completed_calls_in_same_invocation() -> None:
    history = SameInputToolHistory(limit=1)
    assert history.start("wf", "child", _attrs("a"), 10) == {}
    assert history.start("wf", "child", _attrs("concurrent"), 20) == {}
    history.end("wf", "child", _attrs("a", status="success"), 110)
    assert history.start("wf", "other-child", _attrs("different"), 120) == {}
    assert history.start("wf", "child", _attrs("b"), 130) == {
        "previous_same_input_duration_ms": 100,
        "previous_same_input_age_ms": 20,
        "previous_same_input_status": "success",
    }
    history.end("wf", "child", _attrs("b", status="error"), 200)
    assert history.start("wf", "child", _attrs("c"), 210)[
        "previous_same_input_status"
    ] == "error"
    history.discard_invocation("wf", "child")
    assert history.start("wf", "child", _attrs("d"), 220) == {}


def test_export_reconstructs_old_trace_and_rejects_conflicting_online_prior() -> None:
    def event(kind: RuntimeEventKind, call: str, time_ms: int,
              *, previous: float | None = None) -> RuntimeEvent:
        attrs = _attrs(call, status="success" if kind == RuntimeEventKind.TOOL_END else None)
        if previous is not None:
            attrs["previous_same_input_duration_ms"] = previous
        return RuntimeEvent(
            event_id=f"e-{time_ms}", ts_ms=time_ms, kind=kind,
            workflow_id="wf", invocation_id="child", attributes=attrs,
        )

    events = [
        event(RuntimeEventKind.TOOL_START, "a", 10),
        event(RuntimeEventKind.TOOL_END, "a", 110),
        event(RuntimeEventKind.TOOL_START, "b", 130, previous=100),
    ]
    triggers = _event_triggers(events)
    assert triggers[-1]["attributes"]["previous_same_input_duration_ms"] == 100
    with pytest.raises(ValueError, match="history disagrees"):
        _event_triggers(events[:-1] + [
            event(RuntimeEventKind.TOOL_START, "b", 130, previous=999),
        ])


def test_project_history_never_uses_open_or_failed_calls() -> None:
    history = ProjectToolHistory(minimum_support=2, window=2)
    attrs = {
        "tool_name": "execute", "is_child": True,
        "observed_command_class": "test_suite",
    }
    def call(name: str) -> dict:
        return {**attrs, "tool_call_id": name}

    assert history.start("wf-1", "repo", call("a"), 1) == {}
    assert history.start("wf-2", "repo", call("b"), 2) == {}
    history.end("wf-1", {"tool_call_id": "a", "status": "success"}, 101)
    assert history.start("wf-3", "repo", call("c"), 102) == {}
    history.end("wf-2", {"tool_call_id": "b", "status": "error"}, 200)
    history.end("wf-3", {"tool_call_id": "c", "status": "success"}, 202)
    assert history.start("wf-4", "other-repo", call("d"), 203) == {}
    assert history.start(
        "wf-5", "repo", {**call("e"), "observed_command_class": "other"}, 203
    ) == {}
    assert history.start("wf-6", "repo", call("f"), 203) == {
        "project_class_duration_median_ms": 100.0,
        "project_class_completed_support": 2,
    }
    history.end("wf-6", {"tool_call_id": "f", "status": "success"}, 213)
    assert history.start("wf-7", "repo", call("g"), 214)[
        "project_class_duration_median_ms"
    ] == 55.0


def test_export_rebuilds_project_history_and_rejects_conflicting_online_value() -> None:
    metadata = {"wf-a": {"project": "repo"}, "wf-b": {"project": "repo"}}
    events = []
    for index in range(16):
        start = index * 20
        for kind, ts in (
            (RuntimeEventKind.TOOL_START, start),
            (RuntimeEventKind.TOOL_END, start + 10),
        ):
            events.append(RuntimeEvent(
                event_id=f"e-{index}-{kind.value}", ts_ms=ts, kind=kind,
                workflow_id="wf-a", invocation_id="child",
                attributes={
                    **_attrs(f"call-{index}",
                             status="success" if kind == RuntimeEventKind.TOOL_END
                             else None),
                    "is_child": True, "observed_command_class": "test_suite",
                },
            ))
    final = RuntimeEvent(
        event_id="final", ts_ms=400, kind=RuntimeEventKind.TOOL_START,
        workflow_id="wf-b", invocation_id="child",
        attributes={
            **_attrs("final"), "is_child": True,
            "observed_command_class": "test_suite",
        },
    )
    assert _event_triggers(
        [*events, final], workflow_metadata=metadata,
    )[-1]["attributes"]["project_class_duration_median_ms"] == 10
    invalid = RuntimeEvent(
        event_id="invalid", ts_ms=400, kind=RuntimeEventKind.TOOL_START,
        workflow_id="wf-b", invocation_id="child",
        attributes={
            **final.attributes, "project_class_duration_median_ms": 999.0,
        },
    )
    with pytest.raises(ValueError, match="project tool history disagrees"):
        _event_triggers([*events, invalid], workflow_metadata=metadata)
