import numpy as np

from scripts import pilot_child_finish_hazard as hazard
from scripts.pilot_child_finish_hazard import fit_hazard, score_binary


def test_hazard_training_discards_unlabeled_middle_window():
    records = []
    for i in range(10):
        vector = np.zeros(2048, dtype=np.float32)
        vector[0] = float(i)
        records.append({
            "terminal": True,
            "samples": [
                (1, 0., 3000., vector),
                (2, 100., 1500., vector),
                (3, 200., 800., vector),
            ],
        })
    model, counts = fit_hazard(records, hidden=True)
    assert counts["positive_near_finish_snapshots"] == 10
    assert counts["negative_far_finish_snapshots"] == 10
    scores = score_binary(
        model, [(1, 0., 0., np.zeros(2048, dtype=np.float32))],
        hidden=True,
    )
    assert scores.shape == (1,)
    assert 0 <= scores[0] <= 1


def test_hazard_evaluation_counts_nonterminal_false_trigger(monkeypatch):
    vector = np.zeros(2048, dtype=np.float32)
    terminal = {
        "rid": "terminal", "terminal": True, "return_ms": 2300.,
        "first_arrival_ms": 1000.,
        "samples": [(16, 500., 800., vector)],
    }
    nonterminal = {
        "rid": "nonterminal", "terminal": False, "return_ms": None,
        "first_arrival_ms": 1000.,
        "samples": [(16, 500., None, vector)],
    }
    monkeypatch.setattr(
        hazard, "fit_ridge",
        lambda x, y: (np.zeros(x.shape[1]), np.ones(x.shape[1]),
                      np.zeros(x.shape[1]), 1.),
    )
    monkeypatch.setattr(
        hazard, "fit_hazard",
        lambda train, hidden: (None, {}),
    )
    monkeypatch.setattr(
        hazard, "score_binary",
        lambda model, samples, hidden: np.ones(len(samples)),
    )
    report = hazard.evaluate(
        [terminal, nonterminal], [terminal, nonterminal],
        {
            "terminal": {"content": 1300.},
            "nonterminal": {"content": 1300.},
        }, hidden=True,
    )
    assert report["frozen_gate_candidates"] == 2
    assert report["nonterminal_gate_candidates"] == 1
    assert report["terminal_hazard_triggered"] == 1
    assert report["nonterminal_hazard_triggered"] == 1
    assert report["hazard_trigger_precision"] == 0.5
