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
             "stream_content_counted_chars": 100,
             "stream_final_chunk_ts_ms": 1970.,
             "llm_end_callback_entry_ts_ms": 1985.,
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
        done_ns=np.asarray(1_960_000_000),
    )
    np.savez(
        traces / "tool.npz",
        rid=np.asarray("tool-round"),
        arrival_ns=np.asarray([1_000_000_000]),
        finish_ns=np.asarray(1_080_000_000),
    )
    report = summarize(traces, root)
    assert summarize(traces, [root]) == report
    assert report["matched_terminal_rounds"] == 1
    assert report["finish_to_runtime_result_count"] == 2
    assert report["finish_to_runtime_result_p50_ms"] == 35
    assert report["finish_to_runtime_result_p95_ms"] == 48.5
    assert report["invalid_result_order"] == 0
    assert report["finish_to_done_count"] == 1
    assert report["finish_to_done_p95_ms"] == 10
    assert report["done_to_runtime_result_p95_ms"] == 40
    assert report["client_final_chunk_count"] == 1
    assert report["invalid_client_final_order"] == 0
    assert report["finish_to_client_final_p95_ms"] == 20
    assert report["client_final_to_runtime_p95_ms"] == 30
    assert report["callback_entry_count"] == 1
    assert report["invalid_callback_entry_order"] == 0
    assert report["client_final_to_callback_entry_p95_ms"] == 15
    assert report["callback_entry_to_runtime_p95_ms"] == 15
    assert report["done_to_client_final_p95_ms"] == 10
    assert report["client_final_before_done_count"] == 0
    assert report["final_chunk_candidate_true"] == 1
    assert report["final_chunk_candidate_false"] == 0
    assert report["final_chunk_candidate_true_lead_p50_ms"] == 130
    assert report["first_hidden_to_return_p50_ms"] == 1100
    assert report["last_hidden_to_return_p50_ms"] == 200
    assert report["join_last_child_count"] == 1


def test_aborted_rounds_are_not_counted_as_normal_delivery(tmp_path):
    root = tmp_path / "workflows"
    workflow = root / "example"
    workflow.mkdir(parents=True)
    events = _events() + [{
        "kind": "llm_result", "invocation_id": "deepagents-invocation:child",
        "ts_ms": 2200., "attributes": {
            "request_id": "aborted", "finish_reason": "abort",
        },
    }]
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events)
    )
    traces = tmp_path / "traces"
    traces.mkdir()
    np.savez(
        traces / "aborted.npz", rid=np.asarray("aborted"),
        arrival_ns=np.asarray([2_100_000_000]),
        finish_ns=np.asarray(2_300_000_000),
    )
    report = summarize(traces, root)
    assert report["child_llm_results"] == 3
    assert report["matched_hidden_trace_rounds"] == 1
    assert report["normal_child_results"] == 2
    assert report["matched_normal_child_results"] == 0
    assert report["abnormal_result_reasons"] == {"abort": 1}
    assert report["invalid_result_order"] == 0


def test_final_chunk_can_arrive_before_done_but_not_before_finish(tmp_path):
    root = tmp_path / "workflows"
    workflow = root / "example"
    workflow.mkdir(parents=True)
    events = _events()
    events[3]["attributes"]["stream_final_chunk_ts_ms"] = 1955.
    events[3]["attributes"]["llm_end_callback_entry_ts_ms"] = 1980.
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events)
    )
    traces = tmp_path / "traces"
    traces.mkdir()
    np.savez(
        traces / "final.npz", rid=np.asarray("final-round"),
        arrival_ns=np.asarray([1_900_000_000]),
        finish_ns=np.asarray(1_950_000_000),
        done_ns=np.asarray(1_960_000_000),
    )
    report = summarize(traces, root)
    assert report["client_final_chunk_count"] == 1
    assert report["client_final_before_done_count"] == 1
    assert report["done_to_client_final_p50_ms"] == -5
    assert report["client_final_to_callback_entry_p50_ms"] == 25


def test_final_chunk_candidate_censors_unreturned_child_and_counts_repeated_round(
    tmp_path,
):
    root = tmp_path / "workflows"
    workflow = root / "example"
    workflow.mkdir(parents=True)
    events = _events()
    events.insert(3, {
        "kind": "llm_result", "invocation_id": "deepagents-invocation:child",
        "ts_ms": 1500., "attributes": {
            "request_id": "premature", "finish_reason": "stop",
            "stream_final_chunk_ts_ms": 1490.,
            "stream_content_counted_chars": 100,
        },
    })
    events += [
        {"kind": "spawn", "target_invocation_id": "deepagents-invocation:pending"},
        {"kind": "llm_result", "invocation_id": "deepagents-invocation:pending",
         "ts_ms": 2200., "attributes": {
             "request_id": "censored", "finish_reason": "stop",
             "stream_final_chunk_ts_ms": 2190.,
             "stream_content_counted_chars": 100,
         }},
    ]
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events)
    )
    traces = tmp_path / "traces"
    traces.mkdir()
    report = summarize(traces, root)
    assert report["final_chunk_candidate_true"] == 1
    assert report["final_chunk_candidate_false"] == 1
    assert report["final_chunk_candidate_censored"] == 1
