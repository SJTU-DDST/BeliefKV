import json

import numpy as np

from scripts.audit_child_hidden_trace import index_workflow, summarize


def _events():
    return [
        {"kind": "spawn", "target_invocation_id": "deepagents-invocation:child"},
        {"kind": "join_create", "join_id": "join", "member_invocation_ids": [
            "deepagents-invocation:child",
        ]},
        {"kind": "llm_result", "invocation_id": "deepagents-invocation:child",
         "ts_ms": 1100., "attributes": {
             "request_id": "tool-round", "finish_reason": "tool_calls",
             "tool_call_count": 1, "output_chars": 0,
         }},
        {"kind": "llm_result", "invocation_id": "deepagents-invocation:child",
         "ts_ms": 2000., "attributes": {
             "request_id": "final-round", "finish_reason": "stop",
             "tool_call_count": 0, "output_chars": 100,
         }},
        {"kind": "return", "invocation_id": "deepagents-invocation:child",
         "ts_ms": 2100.},
        {"kind": "join_satisfied", "join_id": "join", "ts_ms": 2100.},
    ]


def test_only_last_real_child_round_gets_terminal_label():
    terminal, join_last = index_workflow(_events())
    assert terminal == {
        "final-round": ("deepagents-invocation:child", 2100.)
    }
    assert join_last == {"deepagents-invocation:child"}


def test_summarize_links_no_text_hidden_trace_to_return(tmp_path):
    root = tmp_path / "workflows"
    workflow = root / "example"
    workflow.mkdir(parents=True)
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in _events())
    )
    traces = tmp_path / "traces"
    traces.mkdir()
    np.savez(
        traces / "final.npz",
        rid=np.asarray("final-round"),
        arrival_ns=np.asarray([1_000_000_000, 1_900_000_000]),
        finish_ns=np.asarray(1_950_000_000),
    )
    report = summarize(traces, root)
    assert report["matched_terminal_rounds"] == 1
    assert report["first_hidden_to_return_p50_ms"] == 1100
    assert report["last_hidden_to_return_p50_ms"] == 200
    assert report["join_last_child_count"] == 1
