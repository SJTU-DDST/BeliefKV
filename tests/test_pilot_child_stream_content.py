from __future__ import annotations

from collections import Counter
import json

import numpy as np

from scripts import pilot_child_stream_content as pilot


def test_length_conditioned_text_fits_only_train():
    lengths = np.array([[-1.], [0.], [1.], [2.]])
    text = np.column_stack((2 + 3 * lengths[:, 0], [1., 1., -1., -1.]))
    held_length = np.array([[3.]])
    weights = np.ones(4)
    train, held = pilot.length_conditioned_text(
        text, np.array([[999., 0.]]), lengths, held_length, weights
    )
    another_train, _ = pilot.length_conditioned_text(
        text, np.array([[0., 0.]]), lengths, held_length, weights
    )
    assert np.allclose(train, another_train)
    assert np.max(np.abs(train[:, 0])) < 1e-3
    assert held[0, 0] > 900


def test_vocabulary_requires_distinct_training_workflows():
    with np.testing.assert_raises(ValueError):
        pilot.text_features(["unique unique", "unique unique"], ["unique"],
                            ["one", "one"])
    train, held = pilot.text_features(
        ["shared unique", "shared other"], ["shared"],
        ["one", "two"],
    )
    assert train.shape == (2, 1)
    assert held[0, 0] > 0


def test_collect_includes_text_round_that_later_calls_tool(tmp_path):
    workflow = tmp_path / "astropy__123"
    workflow.mkdir()
    invocation = "child-1"
    child_request = "rid-1"
    (workflow / "child_stream_content_stats.json").write_text(
        json.dumps({"complete": True})
    )
    stream = [
        {"event": "child_stream_content", "request_id": child_request,
         "invocation_id": invocation, "ts_ms": 10.0, "content_chars": 128,
         "content_tail": "I need more evidence", "tool_chunk": False,
         "finish_reason": None},
        {"event": "child_stream_result", "request_id": child_request,
         "invocation_id": invocation, "context_id": "ctx",
         "context_epoch": 0, "ts_ms": 20.0, "tool_call_count": 0,
         "invalid_tool_call_count": 0, "finish_reason": "stop"},
    ]
    (workflow / "child_stream_content.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in stream)
    )
    events = [
        {"kind": "llm_result", "invocation_id": invocation,
         "context_id": "ctx", "context_epoch": 0, "ts_ms": 20.0,
         "attributes": {"request_id": child_request}},
        {"kind": "tool_start", "invocation_id": invocation, "ts_ms": 25.0},
        {"kind": "return", "invocation_id": invocation, "ts_ms": 35.0},
    ]
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events)
    )
    rows, counts = pilot.collect(tmp_path)
    assert len(rows) == 1
    assert rows[0]["label"] == "tool"
    assert counts["text_then_tool_rounds"] == 1


def test_project_holdout_counts_tool_rounds_and_first_trigger(monkeypatch, tmp_path):
    rows = []
    for project in ("astropy", "django", "sphinx-doc"):
        for index in range(4):
            for label in ("return", "tool"):
                text = (
                    f"Final result: solution ready for {index}."
                    if label == "return" else
                    f"Still investigating: run tests for {index}."
                )
                rows.append({
                    "task": f"{project}__{index}",
                    "project": project,
                    "rid": f"{project}-{index}-{label}",
                    "label": label,
                    "join_last": label == "return",
                    "return_ts": 1000.0 if label == "return" else None,
                    "snapshots": [
                        {"ts_ms": 100.0, "content_chars": 128,
                         "content_tail": text},
                        {"ts_ms": 300.0, "content_chars": 256,
                         "content_tail": text + " extra"},
                    ],
                })
    monkeypatch.setattr(pilot, "collect", lambda _: (rows, Counter()))
    result = pilot.evaluate(tmp_path, "sphinx-doc")
    json.dumps(result)
    assert result["heldout_rounds"] == 8
    assert result["train_rounds"] == 16
    assert result["train_projects"] == ["astropy", "django"]
    assert "near_return_2000ms_delivered_tail_only" in result["results"]
    assert "near_return_2000ms_length_conditioned_content" in result["results"]
    for model in result["results"].values():
        assert model["heldout_return_rounds"] == 4
        assert model["heldout_tool_rounds"] == 4
        assert model["heldout_join_last_rounds"] == 4
        assert model["heldout_triggered_return_rounds"] <= 4
        assert model["heldout_tool_false_first_triggers"] <= 4
