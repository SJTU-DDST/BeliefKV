from __future__ import annotations

from scripts.pilot_child_stream_eos_joint import first_eos_events, score


def test_first_eos_events_match_request_epoch_and_pretool_window():
    row = {
        "task": "django__1", "rid": "request", "invocation_id": "child",
        "context_id": "context", "context_epoch": 2,
        "snapshots": [{"ts_ms": 100, "content_chars": 5, "content_tail": "hello"}],
        "result_ts": 400,
    }
    base = {
        "kind": "structured_action", "invocation_id": "child",
        "context_id": "context", "context_epoch": 2,
        "attributes": {
            "request_id": "request",
            "beliefkv_child_eos_shadow": True,
            "eos_top_probability_threshold": 0.0001,
        },
    }
    events = [
        {**base, "ts_ms": 99},
        {**base, "ts_ms": 110, "context_epoch": 1},
        {**base, "ts_ms": 120,
         "attributes": {**base["attributes"], "request_id": "other"}},
        {**base, "ts_ms": 130},
        {**base, "ts_ms": 160},
        {**base, "ts_ms": 190,
         "attributes": {
             "request_id": "request",
             "beliefkv_child_eos_first_top_hit_shadow": True,
         }},
        {**base, "ts_ms": 250},
    ]
    assert first_eos_events(row, events, 220) == {
        "0.0001": 130, "top20": 190,
    }
    assert first_eos_events(row, events, 125) == {}


def test_score_counts_first_early_window_and_tool_negative_once():
    rows = [
        {"task": "django__1", "label": "return", "join_last": True,
         "return_ts": 3000, "eos": {"top20": 1200}},
        {"task": "django__2", "label": "return", "join_last": False,
         "return_ts": 7000, "eos": {"top20": 3000}},
        {"task": "django__2", "label": "tool", "join_last": False,
         "eos": {"top20": 600}},
        {"task": "django__2", "label": "tool", "join_last": False,
         "eos": {}},
    ]
    result = score(rows, "top20")
    assert result["return_trigger_500_to_2000ms"] == 1
    assert result["return_trigger_early_over_2000ms"] == 1
    assert result["first_triggered_tool"] == 1
    assert result["join_last_500_to_2000ms"] == 1
    assert result["window_hits_by_workflow"] == {"django__1": 1}
