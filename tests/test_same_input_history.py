from __future__ import annotations

import pytest

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.experiments.p6_decision_points import _event_triggers
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
