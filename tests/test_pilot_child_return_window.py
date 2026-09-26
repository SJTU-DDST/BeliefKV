import numpy as np

from scripts.pilot_child_return_window import report, scored_sequences, select_threshold


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
