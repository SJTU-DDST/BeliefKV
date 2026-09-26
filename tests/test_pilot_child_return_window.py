import json
import sys

import numpy as np
import pytest

from scripts.pilot_child_return_window import (
    causal_observations, fit_window, main, report, scored_sequences,
    select_delayed_rule, select_threshold, window_cues,
)


def test_window_probe_never_uses_future_tool_or_response(monkeypatch):
    monkeypatch.setattr(
        "scripts.pilot_child_return_window.score",
        lambda model, samples: np.asarray([0.8] * len(samples)),
    )
    def record(rid, terminal, *, returned=None):
        return {
            "rid": rid, "project": "train", "terminal": terminal,
            "join_last": terminal, "return_ms": returned,
            "first_arrival_ms": 1000.,
            "samples": [(16, 200., 0. if terminal else None, np.zeros(2048))],
        }
    head = (
        np.zeros(2051), np.ones(2051), np.zeros(2051), 1.,
    )
    sequences = scored_sequences(
        [
            record("done", True, returned=2000.),
            record("wrong-round", False),
            record("too-late", True, returned=1350.),
            record("tool-first", False),
        ],
        head, (),
        {
            "done": {"content": 1150.},
            "wrong-round": {"content": 1100.},
            "too-late": {"content": 1050.},
            "tool-first": {"content": 1100., "tool": 1190.},
        },
    )
    result = report(sequences, 0.5)
    assert result["first_trigger_count"] == 3
    assert result["window_true_triggers"] == 1
    assert result["join_last_window_triggers"] == 1
    assert result["nonterminal_false_triggers"] == 1
    assert result["under_500ms"] == 1
    assert select_threshold(sequences) is None


def test_window_confirmation_uses_only_past_samples():
    sequences = [{
        "project": "train", "rid": "one", "terminal": True,
        "return_ms": 2200., "join_last": True,
        "observations": [(1100., 0.8), (1300., 0.1), (1450., 0.8), (1600., 0.9)],
    }]
    assert report(sequences, 0.5, 2)["lead_p50_ms"] == 600.
    assert report(sequences, 0.5, 3)["first_trigger_count"] == 0


def test_delayed_timer_cancels_on_observed_tool_result_or_identity_change():
    def sequence(name, *, terminal=True, **extra):
        return {
            "project": "train", "rid": name, "terminal": terminal,
            "return_ms": 2900. if terminal else None,
            "join_last": terminal, "observations": [(1000., 0.9)],
            **{"result_ms": 2600., **extra},
        }

    sequences = [
        sequence("ready"),
        sequence("tool", tool_ms=1100.),
        sequence("ended", result_ms=1250.),
        sequence("changed", invalidated_ms=1200.),
        sequence("unknown", result_ms=None),
        sequence("not-returning", terminal=False),
    ]
    result = report(sequences, 0.5, delay_ms=400)
    assert result["first_trigger_count"] == 2
    assert result["window_true_triggers"] == 1
    assert result["join_last_window_triggers"] == 1
    assert result["nonterminal_false_triggers"] == 1
    assert result["lead_p50_ms"] == 1500.
    assert (result["cancelled_tool"], result["cancelled_result"],
            result["cancelled_identity"], result["missing_result"]) == (1, 1, 1, 1)
    assert report(sequences, 0.5, delay_ms=0)["first_trigger_count"] == 6


def test_window_cues_binds_end_and_invalidation_to_same_request(tmp_path):
    root = tmp_path / "workflows" / "project__task"
    root.mkdir(parents=True)
    events = [
        {"kind": "llm_submit", "invocation_id": "child-1",
         "ts_ms": 1000., "attributes": {"request_id": "request"}},
        {"kind": "beliefkv_child_first_content_shadow",
         "invocation_id": "child-1", "ts_ms": 1050.,
         "attributes": {"request_id": "request",
                        "beliefkv_child_first_content_shadow": True}},
        {"kind": "llm_result", "invocation_id": "child-1",
         "ts_ms": 1700., "attributes": {"request_id": "request"}},
        {"kind": "invocation_cancel", "invocation_id": "child-2",
         "ts_ms": 1100., "attributes": {}},
        {"kind": "return", "invocation_id": "child-1",
         "ts_ms": 1800., "attributes": {}},
    ]
    (root / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    assert window_cues(tmp_path / "workflows")["request"] == {
        "content": 1050., "result": 1700., "invalidated": 1800.,
    }


def test_window_cues_invalidates_epoch_change_before_request_end(tmp_path):
    root = tmp_path / "workflows" / "project__task"
    root.mkdir(parents=True)
    events = [
        {"kind": "old_epoch", "invocation_id": "child",
         "context_id": "context", "context_epoch": -1,
         "ts_ms": 900., "attributes": {}},
        {"kind": "llm_submit", "invocation_id": "child",
         "context_id": "context", "context_epoch": 0,
         "ts_ms": 1000., "attributes": {"request_id": "request"}},
        {"kind": "llm_result", "invocation_id": "other",
         "context_id": "other", "context_epoch": 1,
         "ts_ms": 1100., "attributes": {"request_id": "other-request"}},
        {"kind": "context_advance", "invocation_id": "child",
         "context_id": "context", "context_epoch": 1,
         "ts_ms": 1300., "attributes": {}},
        {"kind": "llm_result", "invocation_id": "child",
         "context_id": "context", "context_epoch": 0,
         "ts_ms": 1700., "attributes": {"request_id": "request"}},
    ]
    (root / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    assert window_cues(tmp_path / "workflows")["request"] == {
        "result": 1700., "invalidated": 1300.,
    }


def test_delayed_selection_requires_precision_across_projects():
    sequences = [{
        "project": f"project-{index % 2}", "rid": str(index),
        "terminal": True, "return_ms": 4200.,
        "join_last": True, "observations": [(800., 0.9)],
        "result_ms": 4000.,
    } for index in range(10)]
    assert select_delayed_rule(sequences) is not None
    assert select_delayed_rule(sequences[:7]) is None


def test_hidden_snapshot_is_first_available_at_content_and_never_after_tool():
    record = {
        "rid": "child", "terminal": True, "return_ms": 2000.,
        "first_arrival_ms": 1000.,
        "samples": [
            (16, 100., 900., np.zeros(2048)),
            (17, 200., 800., np.ones(2048)),
            (18, 300., 700., np.ones(2048) * 2),
        ],
    }
    observations = causal_observations(
        record, {"child": {"content": 1250., "tool": 1350.}},
    )
    assert [(ts, row[0], row[2]) for ts, row in observations] == [
        (1250., 17, 750.),
        (1300., 18, 700.),
    ]
    assert causal_observations(
        record, {"child": {"content": 1350., "tool": 1340.}},
    ) == []
    assert [
        ts for ts, _ in causal_observations(
            record, {"child": {"content": 1250., "result": 1275.}},
        )
    ] == [1250.]
    assert causal_observations(
        record, {"child": {"content": 1250., "invalidated": 1240.}},
    ) == []


def test_no_observable_snapshot_does_not_call_window_head(monkeypatch):
    def fail_score(*_args):
        raise AssertionError("no observable snapshot should reach the head")

    monkeypatch.setattr(
        "scripts.pilot_child_return_window.score", fail_score,
    )
    head = (
        np.zeros(2051), np.ones(2051), np.zeros(2051), 1.,
    )
    record = {
        "rid": "tool-before-content", "project": "train",
        "terminal": False, "return_ms": None,
        "first_arrival_ms": 1000.,
        "samples": [(16, 200., None, np.zeros(2048))],
    }
    assert scored_sequences(
        [record], head, (), {
            "tool-before-content": {"content": 1250., "tool": 1200.},
        },
    ) == []


def test_window_fit_labels_past_hidden_at_content_availability(monkeypatch):
    labels = []

    def capture_fit(_features, target):
        labels.extend(target.tolist())
        return "fitted"

    monkeypatch.setattr(
        "scripts.pilot_child_return_window.fit_ridge", capture_fit,
    )
    records = []
    cues = {}
    for index in range(20):
        rid = f"terminal-{index}"
        records.append({
            "rid": rid, "terminal": True, "return_ms": 2000.,
            "first_arrival_ms": 1000.,
            "samples": [(16, 100., 900., np.zeros(2048))],
        })
        cues[rid] = {"content": 1250. if index < 10 else 1750.}
    assert fit_window(records, cues) == "fitted"
    assert sorted(labels) == [0.] * 10 + [1.] * 10


def test_stage_tokens_must_match_hidden_sampling_grid(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "pilot", "--stage-tokens", "33", "--train-traces", "train",
        "--train-workflows", "train", "--heldout-traces", "heldout",
        "--heldout-workflows", "heldout", "--output", "report",
    ])
    with pytest.raises(SystemExit, match="2"):
        main()
    assert "positive multiple of 32" in capsys.readouterr().err
