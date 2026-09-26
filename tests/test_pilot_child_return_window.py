import sys

import numpy as np
import pytest

from scripts.pilot_child_return_window import (
    causal_observations, fit_window, main, report, scored_sequences,
    select_threshold,
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
