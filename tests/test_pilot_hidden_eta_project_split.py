import json

import numpy as np

from scripts.pilot_hidden_eta_project_split import (
    evaluate,
    features,
    fit_ridge,
    predict,
    select_tasks,
)


def test_select_tasks_keeps_projects_separate(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"workloads": [
        {"instance_id": "train__1", "problem_statement": "a"},
        {"instance_id": "train__2", "problem_statement": "b"},
        {"instance_id": "heldout__1", "problem_statement": "c"},
    ]}))
    assert list(select_tasks(path, 2)) == ["train"]


def test_timing_features_and_trigger_use_only_current_snapshot():
    vector = np.zeros(2048, dtype=np.float32)
    records = [[(1, 128., 1200., vector), (2, 256., 250., vector)]]
    x = features(records[0], False)
    assert x.shape == (2, 3)
    assert features(records[0], True).shape == (2, 2051)
    model = fit_ridge(x, np.log1p([1200., 250.]))
    assert np.isfinite(predict(model, x)).all()
    report = evaluate(model, records, False)
    assert report["natural_stop_requests"] == 1
    assert report["first_triggered_requests"] == 1
