from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytest.importorskip("deepagents")

from langchain_core.messages import HumanMessage

from beliefkv.core.events import RuntimeEventKind
from beliefkv.runtime.deepagents_adapter import DeepAgentsRuntimeAdapter
from beliefkv.runtime.eos_shadow import eos_top_logprob
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


def test_eos_top_logprob_ignores_sampled_terminal_and_absent_top():
    found, scored = eos_top_logprob({
        "content": [
            {
                "token": "ordinary",
                "top_logprobs": [
                    {"token": "<|im_end|>", "logprob": -2.0},
                    {"token": "private", "logprob": -0.1},
                ],
            },
            {
                "token": "<|im_end|>",
                "top_logprobs": [
                    {"token": "<|im_end|>", "logprob": 0.},
                ],
            },
        ],
    })
    assert found == -2.0
    assert scored == 2
    assert eos_top_logprob({"content": [{"token": "word"}]}) == (None, 1)


def test_child_eos_threshold_uses_current_request_and_deduplicates():
    trace = _Sink()
    adapter = DeepAgentsRuntimeAdapter(
        trace, BeliefKVRequestMetadata("wf", "root", "ctx", 0),
        eos_shadow=True,
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
    def chunk(text, *, finish=None, sampled="word"):
        return SimpleNamespace(
            generation_info={
                "logprobs": {"content": [{
                    "token": sampled, "top_logprobs": [{
                        "token": "<|im_end|>", "logprob": -1.9,
                    }],
                }]},
                **({"finish_reason": finish} if finish else {}),
            },
            message=SimpleNamespace(content=text, tool_call_chunks=[]),
        )
    first = chunk("visible")
    adapter.on_llm_new_token("visible", chunk=first, run_id=run)
    second = chunk("private")
    adapter.on_llm_new_token("private", chunk=second, run_id=run)
    adapter.on_llm_new_token("private", chunk=second, run_id=run)
    adapter.on_llm_new_token("", chunk=chunk(
        "", finish="stop", sampled="<|im_end|>",
    ), run_id=run)
    cues = [
        event for event in trace.events
        if event.attributes.get("beliefkv_child_eos_shadow")
    ]
    assert [event.attributes["eos_top_probability_threshold"] for event in cues] == [
        0.01, 0.05, 0.1,
    ]
    assert all(event.invocation_id == task.invocation_id for event in cues)
    assert all(event.join_id == task.join_id for event in cues)
    assert all(event.attributes["request_id"] == f"beliefkv:{run}" for event in cues)
    assert "private" not in json.dumps([event.to_dict() for event in cues])


def test_opt_in_low_eos_threshold_delivers_earlier_without_changing_defaults():
    trace = _Sink()
    adapter = DeepAgentsRuntimeAdapter(
        trace, BeliefKVRequestMetadata("wf", "root", "ctx", 0),
        eos_shadow=True, eos_low_prob_shadow=True,
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

    def chunk(logprob):
        return SimpleNamespace(
            generation_info={"logprobs": {"content": [{
                "token": "word",
                "top_logprobs": [{
                    "token": "<|im_end|>", "logprob": logprob,
                }],
            }]}},
            message=SimpleNamespace(content="visible", tool_call_chunks=[]),
        )

    adapter.on_llm_new_token("visible", chunk=chunk(-7.), run_id=run)
    adapter.on_llm_new_token("visible", chunk=chunk(-6.), run_id=run)
    cues = [
        event.attributes["eos_top_probability_threshold"]
        for event in trace.events
        if event.attributes.get("beliefkv_child_eos_shadow")
    ]
    assert cues == [0.0001, 0.001]
