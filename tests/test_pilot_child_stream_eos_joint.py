from __future__ import annotations

from scripts.pilot_child_stream_eos_joint import (
    first_eos_events, gate_accepts, gated_score, score, select_policy,
)


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


def test_content_gate_uses_only_snapshot_delivered_at_first_eos_crossing():
    row = {
        "label": "return", "eos": {"0.05": 250},
        "snapshots": [
            {"ts_ms": 100, "content_chars": 40, "content_tail": "Checking"},
            {"ts_ms": 300, "content_chars": 600, "content_tail": "Resolved."},
        ],
    }
    assert gate_accepts(row, "0.05", "eos_only")
    assert not gate_accepts(row, "0.05", "chars_512")
    assert not gate_accepts(row, "0.05", "sentence_end_no_open_issue")
    assert not gate_accepts(row, "0.25", "eos_only")


def test_fused_policy_selection_observes_training_only_risk():
    snapshots = [
        {"ts_ms": 0, "content_chars": 40, "content_tail": "Checking"},
        {"ts_ms": 5000, "content_chars": 600, "content_tail": "Resolved."},
    ]
    rows = [
        {
            "task": "project__1", "label": "return", "join_last": False,
            "return_ts": 6000, "snapshots": snapshots, "eos": {"0.05": 5200},
        }
        for _ in range(10)
    ] + [
        {
            "task": "project__1", "label": "tool", "join_last": False,
            "snapshots": snapshots, "eos": {"0.05": 200},
        }
        for _ in range(20)
    ]
    policy, results = select_policy(rows)
    assert policy is not None
    assert results["|".join(policy)]["return_trigger_500_to_2000ms"] == 10
    assert results["|".join(policy)]["first_triggered_tool"] == 0


def test_sampled_eos_requires_new_evidence_after_content_gate_opens():
    row = {
        "task": "project__1", "label": "return", "join_last": True,
        "return_ts": 6500, "eos": {"0.05": 300},
        "snapshots": [
            {
                "ts_ms": 100, "content_chars": 40, "content_tail": "Checking",
                "eos_shadow_max_logprob_since_previous_snapshot": None,
            },
            {
                "ts_ms": 300, "content_chars": 80, "content_tail": "Checking",
                "eos_shadow_max_logprob_since_previous_snapshot": -2.0,
            },
            {
                "ts_ms": 5000, "content_chars": 600, "content_tail": "Resolved.",
                "eos_shadow_max_logprob_since_previous_snapshot": None,
            },
            {
                "ts_ms": 5400, "content_chars": 650, "content_tail": "Resolved.",
                "eos_shadow_max_logprob_since_previous_snapshot": -2.0,
            },
        ],
    }
    accepted = gated_score([row], "0.05", "chars_512")
    assert accepted["return_trigger_500_to_2000ms"] == 1
    assert accepted["gate_rejected_return_crossings"] == 0
    row["snapshots"].pop()
    rejected = gated_score([row], "0.05", "chars_512")
    assert rejected["first_triggered_return"] == 0
    assert rejected["gate_rejected_return_crossings"] == 1
