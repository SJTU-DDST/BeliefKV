from __future__ import annotations

from collections import Counter
import json

import numpy as np

from scripts import pilot_child_stream_content as pilot
from scripts.child_stream_service_index import snapshot_status


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


def test_progress_residual_removes_train_clock_and_length_only():
    basis = np.array([[-2., 1.], [-1., -1.], [1., -1.], [2., 1.]])
    text = np.column_stack((3 + 2 * basis[:, 0] + 4 * basis[:, 1],
                            [1., -1., 1., -1.]))
    train, held = pilot.length_conditioned_text(
        text, np.array([[99., 0.]]), basis, np.array([[3., 0.]]),
        np.ones(len(basis)),
    )
    assert np.max(np.abs(train[:, 0])) < 1e-2
    assert held[0, 0] > 80


def test_decode_progress_is_causal_and_uses_recent_rate():
    snapshots = [
        {"content_chars": 32, "ts_ms": 100},
        {"content_chars": 64, "ts_ms": 300},
        {"content_chars": 128, "ts_ms": 400},
    ]
    row = {"label": "return", "return_ts": 10000, "snapshots": snapshots}
    original = pilot.decode_progress([row]).copy()
    row["return_ts"] = 1
    assert np.array_equal(pilot.decode_progress([row]), original)
    assert original[0, 1] == 0
    assert original[0, 2] == 0
    assert original[2, 2] > original[1, 2]


def test_phase_features_use_only_delivered_content_and_track_new_cues():
    row = {
        "label": "return", "return_ts": 9000,
        "snapshots": [
            {"content_chars": 49, "ts_ms": 10,
             "content_tail": "Let me check the tests before I continue:"},
            {"content_chars": 80, "ts_ms": 60,
             "content_tail": "Let me check the tests before I continue:\n\n"
                             "In summary, the fix is verified."},
        ],
    }
    original = pilot.phase_features([row])
    row["return_ts"] = 1
    assert np.array_equal(original, pilot.phase_features([row]))
    assert original.shape == (2, 14)
    assert original[0, 0] == 1
    assert original[1, 1] == 1
    assert original[1, 5] == 1
    assert original[1, 10] == 1


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


def test_collect_early_snapshots_does_not_count_finish_or_tool_chunks(tmp_path):
    workflow = tmp_path / "django__1"
    workflow.mkdir()
    (workflow / "child_stream_content_stats.json").write_text(
        json.dumps({"complete": True})
    )
    stream = [
        {"event": "child_stream_content", "request_id": "rid",
         "invocation_id": "child", "ts_ms": ts, "content_chars": chars,
         "content_tail": "final answer", "tool_chunk": tool,
         "finish_reason": finish, "sampling_reason": reason}
        for ts, chars, tool, finish, reason in (
            (10, 32, False, None, "milestone"),
            (12, 50, False, None, "content_boundary"),
            (15, 64, False, None, "milestone"),
            (16, 128, True, None, "tool_or_finish"),
            (20, 128, False, "stop", "tool_or_finish"),
        )
    ]
    stream.append({
        "event": "child_stream_result", "request_id": "rid",
        "invocation_id": "child", "context_id": "ctx", "context_epoch": 1,
        "ts_ms": 21, "tool_call_count": 0, "invalid_tool_call_count": 0,
        "finish_reason": "stop",
    })
    (workflow / "child_stream_content.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in stream)
    )
    events = [
        {"kind": "spawn", "target_invocation_id": "child", "ts_ms": 0},
        {"kind": "llm_result", "invocation_id": "child", "context_id": "ctx",
         "context_epoch": 1, "ts_ms": 21,
         "attributes": {"request_id": "rid", "finish_reason": "stop",
                        "output_chars": 128, "tool_call_count": 0}},
        {"kind": "return", "invocation_id": "child", "ts_ms": 25},
    ]
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events)
    )
    early, _ = pilot.collect(tmp_path, min_snapshot_chars=32)
    assert [s["content_chars"] for s in early[0]["snapshots"]] == [32, 50, 64]
    without_boundaries, counts = pilot.collect(
        tmp_path, min_snapshot_chars=32, exclude_boundary_snapshots=True,
    )
    assert [s["content_chars"] for s in without_boundaries[0]["snapshots"]] == [32, 64]
    assert counts["snapshot_content_boundary"] == 0
    assert counts["snapshot_milestone"] == 2
    later, counts = pilot.collect(tmp_path, min_snapshot_chars=128)
    assert not later
    assert counts["return_without_eligible_snapshot"] == 1


def test_collect_distinct_roots_and_rejects_repeated_task(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    for root, task in ((first, "django__1"), (second, "pytest-dev__1")):
        workflow = root / task
        workflow.mkdir(parents=True)
        (workflow / "child_stream_content_stats.json").write_text(
            json.dumps({"complete": True})
        )
        (workflow / "child_stream_content.jsonl").write_text(
            json.dumps({
                "event": "child_stream_result", "request_id": task,
                "invocation_id": "child", "context_id": "ctx", "context_epoch": 1,
                "ts_ms": 21, "tool_call_count": 1, "invalid_tool_call_count": 0,
                "finish_reason": "tool_calls",
            }) + "\n"
        )
        (workflow / "runtime_events.deepagents.jsonl").write_text(
            json.dumps({
                "kind": "llm_result", "invocation_id": "child",
                "context_id": "ctx", "context_epoch": 1, "ts_ms": 21,
                "attributes": {"request_id": task},
            }) + "\n"
        )
    rows, counts = pilot.collect((first, second))
    assert not rows
    assert counts["tool_rounds"] == 2
    with np.testing.assert_raises_regex(ValueError, "more than once"):
        pilot.collect((first, first))


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
    monkeypatch.setattr(pilot, "collect", lambda _, **kwargs: (rows, Counter()))
    result = pilot.evaluate(tmp_path, "sphinx-doc")
    json.dumps(result)
    assert result["heldout_rounds"] == 8
    assert result["train_rounds"] == 16
    assert result["train_projects"] == ["astropy", "django"]
    assert "near_return_2000ms_delivered_tail_only" in result["results"]
    assert "near_return_2000ms_length_conditioned_content" in result["results"]
    assert "near_return_2000ms_progress_only" in result["results"]
    assert "near_return_2000ms_progress_conditioned_content" in result["results"]
    assert "near_return_2000ms_progress_conditioned_phase" in result["results"]
    for model in result["results"].values():
        assert model["heldout_return_rounds"] == 4
        assert model["heldout_tool_rounds"] == 4
        assert model["heldout_join_last_rounds"] == 4
        assert model["heldout_triggered_return_rounds"] <= 4
        assert model["heldout_tool_false_first_triggers"] <= 4
        assert model["heldout_window_oracle"] == 4


def test_disjoint_calibration_threshold_does_not_use_heldout(monkeypatch, tmp_path):
    rows = []
    for project in ("django", "pytest-dev", "pydata", "pylint-dev"):
        for index in range(4):
            for label in ("return", "tool"):
                text = (
                    f"Final answer verified {index}" if label == "return"
                    else f"Need to inspect files {index}"
                )
                rows.append({
                    "task": f"{project}__{index}", "project": project,
                    "rid": f"{project}-{index}-{label}", "label": label,
                    "join_last": label == "return",
                    "return_ts": 1200.0 if label == "return" else None,
                    "snapshots": [
                        {"ts_ms": 100, "content_chars": 32, "content_tail": text},
                        {"ts_ms": 500, "content_chars": 64, "content_tail": text},
                    ],
                })
    monkeypatch.setattr(pilot, "collect", lambda _, **kwargs: (rows, Counter()))
    options = {
        "train_projects": ("django", "pytest-dev"),
        "calibration_projects": ("pydata",),
        "min_snapshot_chars": 32,
    }
    original = pilot.evaluate(tmp_path, "pylint-dev", **options)
    assert original["status"] == "frozen_project_disjoint_calibrated_threshold"
    assert original["train_rounds"] == 16
    assert original["calibration_rounds"] == 8
    for row in rows:
        if row["project"] == "pylint-dev":
            row["snapshots"][0]["content_tail"] = "changed heldout content"
            row["snapshots"][1]["content_tail"] = "changed heldout content"
    altered = pilot.evaluate(tmp_path, "pylint-dev", **options)
    for key, result in original["results"].items():
        assert result["threshold_selected_on_calibration"] == (
            altered["results"][key]["threshold_selected_on_calibration"]
        )
    with np.testing.assert_raises(ValueError):
        pilot.evaluate(
            tmp_path, "pylint-dev",
            train_projects=("django", "pydata"),
            calibration_projects=("pydata",),
        )


def test_service_status_has_clock_guard_and_recent_decode():
    state = {
        "server_end_ms": 1200,
        "offset_lower_ms": 0,
        "offset_upper_ms": 0,
        "decode_sample_times_ms": [800],
    }
    assert snapshot_status(state, 1000) == "unfinished_with_recent_decode"
    assert snapshot_status({**state, "decode_sample_times_ms": []}, 1000) == (
        "unfinished_without_recent_decode"
    )
    assert snapshot_status({**state, "server_end_ms": 1000}, 1000) == (
        "clock_ambiguous"
    )
    assert snapshot_status({**state, "server_end_ms": 800}, 1000) == (
        "server_finished_before_trigger"
    )
    assert snapshot_status(None, 1000) == "missing_server_result"


def test_service_audit_keeps_first_trigger_and_tool_false_positive():
    rows = [
        {"rid": "early", "task": "django__1", "label": "return",
         "join_last": True, "return_ts": 3000},
        {"rid": "timely", "task": "django__2", "label": "return",
         "join_last": True, "return_ts": 3000},
        {"rid": "tool", "task": "django__3", "label": "tool",
         "join_last": False, "return_ts": None},
    ]
    service = {
        rid: {
            "server_end_ms": 4000,
            "offset_lower_ms": 0, "offset_upper_ms": 0,
            "decode_sample_times_ms": [400, 1900],
        }
        for rid in ("early", "timely", "tool")
    }
    result = pilot.first_trigger_service_audit(
        rows, {0: [600, 2000], 1: [2000], 2: [2000]}, service,
    )
    assert result["early_over_2000ms_with_recent_decode"] == 1
    assert result["window_hits_with_recent_decode"] == 1
    assert result["join_last_window_hits_with_recent_decode"] == 1
    assert result["live_window_hits_by_workflow"] == {"django__2": 1}
    assert result["first_tool_trigger_status"] == {
        "unfinished_with_recent_decode": 1
    }
