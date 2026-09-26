import numpy as np

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
