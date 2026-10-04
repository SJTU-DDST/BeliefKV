from types import SimpleNamespace

import numpy as np

from scripts.fit_child_completion_windows import HORIZONS, select_threshold, window_metrics


def sample(rid, when, remaining, *, task="task"):
    return {
        "task": task, "observation": SimpleNamespace(request_id=rid, ts_ms=when),
        "remaining_tokens": remaining, "remaining_client_wall_ms": 200.,
    }


def test_window_metrics_count_first_crossing_not_repeated_near_end_snapshots():
    rows = [
        sample("early", 1, 100), sample("early", 2, 8),
        sample("correct", 1, 16), sample("correct", 2, 8),
        sample("tool", 1, None),
    ]
    result = window_metrics(rows, np.ones(5), 32, .8)
    assert result["first_trigger_count"] == 3
    assert result["within_token_horizon_count"] == 1
    assert result["too_early_final_trigger_count"] == 1
    assert result["tool_round_false_trigger_count"] == 1
    assert result["reachable_natural_request_count"] == 2
    assert result["reachable_request_recall"] == .5
    assert window_metrics(rows, np.ones(5), 32, None)["first_trigger_count"] == 0


def test_threshold_is_selected_on_independent_requests():
    rows = [sample(f"r{i}", 1, 8, task=f"task{i}") for i in range(5)]
    rows.append(sample("bad", 1, 100))
    scores = np.asarray([.95, .94, .93, .92, .91, .9])
    selected = select_threshold(rows * 10, np.tile(scores, 10), 16, precision=1.)
    assert selected["first_trigger_count"] == 5
    assert selected["threshold"] == .91
    assert select_threshold(rows[:1] * 10, np.ones(10), 16) is None
    assert tuple(np.searchsorted(HORIZONS, [8, 9, 16, 17, 64, 65], side="left")) == (
        0, 1, 1, 2, 3, 4,
    )
