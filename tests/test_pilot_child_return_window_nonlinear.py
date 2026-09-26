import sys

import numpy as np
import pytest

from scripts.pilot_child_return_window_nonlinear import (
    main, observable_features, scored_sequences,
)


def test_nonlinear_features_never_read_future_return_label():
    record = {
        "rid": "child", "project": "train", "terminal": True,
        "return_ms": 2200., "first_arrival_ms": 1000.,
        "samples": [
            (8, 200., 1000., np.ones(2048)),
            (10, 400., 800., np.ones(2048) * 2),
        ],
    }
    cues = {"child": {"content": 1300.}}
    a = observable_features(record, cues, hidden=True)
    record["return_ms"] = 5400.
    b = observable_features(record, cues, hidden=True)
    assert [ts for ts, _, _ in a] == [1300., 1400.]
    assert all(np.array_equal(left[1], right[1])
               for left, right in zip(a, b))
    assert [row[2] for row in a] == [900., 800.]
    assert [row[2] for row in b] == [4100., 4000.]


def test_nonlinear_scorer_skips_nonobservable_round():
    record = {
        "rid": "child", "project": "train", "terminal": False,
        "return_ms": None, "first_arrival_ms": 1000.,
        "samples": [(8, 200., None, np.ones(2048))],
    }
    head = (np.zeros(2051), np.ones(2051), np.zeros(2051), 1.)
    assert scored_sequences(
        [record], head, None,
        {"child": {"content": 1400., "tool": 1300.}}, hidden=False,
    ) == []


def test_nonlinear_requires_one_trace_root_per_training_batch(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "pilot", "--train-traces", "trace", "--train-workflows", "first",
        "--train-workflows", "second", "--heldout-traces", "heldout-trace",
        "--heldout-workflows", "heldout-workflows", "--output", "report",
    ])
    with pytest.raises(SystemExit, match="2"):
        main()
    assert "one --train-traces per --train-workflows" in capsys.readouterr().err
