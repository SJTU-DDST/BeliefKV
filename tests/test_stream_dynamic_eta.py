from __future__ import annotations

import json

import pytest

from scripts.pilot_stream_dynamic_eta import evaluate, samples


def _event(ts: float, kind: str, *, workflow: str, **attrs) -> dict:
    return {
        "ts_ms": ts, "kind": kind, "workflow_id": workflow,
        "invocation_id": "child", "attributes": attrs,
    }


def test_dynamic_stage_uses_only_completed_prefix_and_keeps_nonfinal(tmp_path):
    directory = tmp_path / "workflows" / "pydata__one"
    directory.mkdir(parents=True)
    events = [
        {**_event(0, "invocation_create", workflow="wf"),
         "relation_type": "spawn"},
    ]
    for request, offset, final in (
        ("tool", 0, False), ("final", 5000, True),
    ):
        events.append(_event(100 + offset, "llm_submit", workflow="wf",
                             request_id=request))
        for chars, when in ((64, 200), (1024, 400), (1700, 650), (2400, 900)):
            events.append(_event(
                when + offset, "structured_action", workflow="wf",
                request_id=request,
                beliefkv_child_substantial_content_shadow=True,
                content_threshold_chars=chars,
            ))
        events.extend((
            _event(1500 + offset, "llm_result", workflow="wf",
                   request_id=request, output_chars=2700,
                   tool_call_count=0 if final else 1,
                   finish_reason="stop" if final else "tool_calls"),
            _event(1600 + offset, "return" if final else "tool_start",
                   workflow="wf"),
        ))
    (directory / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
    )
    rows, censored = samples(directory.parent)
    assert censored == 0
    assert [row["final"] for row in rows] == [False, True]
    assert rows[0]["features"][:4] == rows[1]["features"][:4]
    assert rows[0]["features"][4] < rows[1]["features"][4]
    assert rows[1]["lead_ms"] == 700
    assert rows[1]["trigger_ms"] == 5900
    assert rows[1]["join_id"] is None


def test_early_stage_uses_only_as_of_milestones(tmp_path):
    directory = tmp_path / "workflows" / "django__one"
    directory.mkdir(parents=True)
    events = [
        {**_event(0, "invocation_create", workflow="wf"),
         "relation_type": "spawn"},
        _event(100, "llm_submit", workflow="wf", request_id="answer"),
    ]
    for chars, when in ((64, 200), (1024, 400), (1700, 3000)):
        events.append(_event(
            when, "structured_action", workflow="wf", request_id="answer",
            beliefkv_child_substantial_content_shadow=True,
            content_threshold_chars=chars,
        ) | {"join_id": "join-1"})
    events.extend((
        _event(5000, "llm_result", workflow="wf", request_id="answer",
               output_chars=1800, tool_call_count=0, finish_reason="stop"),
        _event(5200, "return", workflow="wf"),
    ))
    (directory / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
    )
    early, censored = samples(directory.parent, stage_chars=1024)
    assert censored == 0
    assert len(early) == 1
    assert early[0]["lead_ms"] == 2800
    assert early[0]["join_id"] == "join-1"
    assert early[0]["trigger_ms"] == 2400
    assert early[0]["features"][2:4] == [0, 0]
    later, _ = samples(directory.parent, stage_chars=1700)
    assert len(later) == 1
    assert later[0]["lead_ms"] == 1950
    assert later[0]["trigger_ms"] == 3250
    assert later[0]["features"][2] > 0
    assert later[0]["features"][3] == 0
    assert samples(directory.parent, stage_chars=2400)[0] == []


def test_dynamic_eta_rejects_project_overlap_and_reports_conditional_limit():
    train = [
        {
            "trace_path": f"/root/pydata__{i}/runtime_events.deepagents.jsonl",
            "features": [float(i % 3)] * 5,
            "lead_ms": 1500 + i * 5,
            "final": True,
            "last_join_child": False,
        }
        for i in range(24)
    ]
    heldout = [{
        **train[0],
        "trace_path": "/root/astropy__one/runtime_events.deepagents.jsonl",
        "last_join_child": True,
    }]
    with pytest.raises(ValueError, match="projects overlap"):
        evaluate(train, train, 0)
    report = evaluate(train, heldout, 2)
    assert report["heldout"]["natural_returns"] == 1
    assert report["heldout_last_join_child"]["natural_returns"] == 1
    assert report["heldout_censored_candidates"] == 2
    assert report["status"].startswith("read_only_oracle")
