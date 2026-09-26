import numpy as np

from scripts.pilot_child_terminal_threshold import (
    report, scored_candidates, select_threshold,
)


def test_threshold_selection_requires_early_low_false_positive():
    candidates = [
        {"score": 0.81, "terminal": True, "lead_ms": 1400.}
        for _ in range(5)
    ]
    candidates.append({"score": 0.55, "terminal": False, "lead_ms": None})
    assert select_threshold(candidates, 5) == 0.6
    assert report(candidates, 0.6, 5)["precision"] == 1.
    assert report(candidates, 0.6, 5)["actionable_precision"] == 1.
    assert select_threshold(
        [{**row, "lead_ms": 100.} for row in candidates if row["terminal"]],
        5,
    ) is None
    assert report(
        [{**row, "lead_ms": 100.} for row in candidates if row["terminal"]],
        0.6, 5,
    )["actionable_precision"] == 0.


def test_gate_waits_for_content_and_rejects_preceding_tool():
    vector = np.zeros(2048, dtype=np.float32)
    rows = [
        {"rid": rid, "project": "psf", "terminal": terminal,
         "return_ms": 3000. if terminal else None,
         "first_arrival_ms": 1000.,
         "samples": [(8, 500., None, vector)]}
        for rid, terminal in (("final", True), ("tool", False))
    ]
    model = (
        np.zeros(2051), np.ones(2051),
        np.zeros(2051), 0.8,
    )
    candidates = scored_candidates(rows, model, {
        "final": {"content": 1900.},
        "tool": {"content": 1100., "tool": 1400.},
    })
    assert len(candidates) == 1
    assert candidates[0]["lead_ms"] == 1100.
