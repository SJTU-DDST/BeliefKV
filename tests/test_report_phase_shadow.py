from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytest.importorskip("deepagents")

from langchain_core.messages import HumanMessage

from beliefkv.core.events import RuntimeEventKind
from beliefkv.runtime.deepagents_adapter import DeepAgentsRuntimeAdapter
from beliefkv.runtime.report_phase import ReportPhaseTracker
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata


class _Sink:
    def __init__(self) -> None:
        self.events = []

    def emit_batch(self, events) -> None:
        self.events.extend(events)


def test_heading_tracker_handles_split_chunks_fences_and_bounded_lines():
    tracker = ReportPhaseTracker()
    assert tracker.feed("## Final Report\n" + "x" * 1024) == [
        ("other", len("## Final Report\n")),
    ]
    assert tracker.feed("\n```\n## Conclusion\n```\n### Summ") == []
    phases = tracker.feed("ary:\n" + "y" * 170 + "\n## Conclusion\n")
    assert phases == [
        ("summary", 1024 + len("## Final Report\n")
         + len("\n```\n## Conclusion\n```\n### Summary:\n")),
        ("conclusion", tracker.chars),
    ]
    assert all("private" not in str(phase) for phase in phases)


def test_child_phase_events_are_trace_only_bound_to_request_epoch():
    trace = _Sink()
    adapter = DeepAgentsRuntimeAdapter(
        trace, BeliefKVRequestMetadata("wf", "root", "ctx", 0),
        report_phase_shadow=True,
    )
    adapter.start()
    task = adapter.declare_runtime_tasks([("explorer", "private")])[0]
    tool_run = uuid4()
    adapter.on_tool_start(
        {"name": "task"}, "", run_id=tool_run,
        inputs={"subagent_type": "explorer", "description": "private"},
        tool_call_id=task.tool_call_id,
    )
    run = uuid4()
    adapter.on_chat_model_start(
        {}, [[HumanMessage(content="private prompt")]],
        run_id=run, parent_run_id=tool_run,
    )

    def emit(text: str, *, tool: bool = False) -> None:
        chunk = SimpleNamespace(message=SimpleNamespace(
            content=text, tool_call_chunks=[{"name": "execute"}] if tool else [],
        ))
        adapter.on_llm_new_token(text, chunk=chunk, run_id=run)
        adapter.on_llm_new_token(text, chunk=chunk, run_id=run)

    emit("x" * 1024 + "\n## Sum")
    emit("mary\n")
    emit("## Conclusion\n", tool=True)
    phases = [
        event for event in trace.events
        if event.attributes.get("beliefkv_child_report_phase_shadow")
    ]
    assert len(phases) == 1
    assert phases[0].kind == RuntimeEventKind.STRUCTURED_ACTION
    assert phases[0].invocation_id == task.invocation_id
    assert phases[0].join_id == task.join_id
    assert phases[0].attributes["request_id"] == f"beliefkv:{run}"
    assert phases[0].attributes["phase_kind"] == "summary"
    assert phases[0].attributes["diagnostic_only"] is True
    assert "private" not in json.dumps(phases[0].to_dict())
