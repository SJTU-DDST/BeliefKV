from types import SimpleNamespace as NS

import numpy as np
import pytest

from scripts.fit_semantic_work_window_score import (
    first_crossings, request_weights, selection_rank,
)


def sample(rid, when, remaining, *, task="task"):
    return {
        "task": task, "remaining_tokens": remaining,
        "remaining_client_wall_ms": remaining * 20,
        "observation": NS(request_id=rid, ts_ms=when),
    }


def test_first_crossing_uses_earliest_eligible_observation_instead_of_best_later_one():
    rows = [sample("r", 2, 10), sample("r", 1, 100), sample("r", 0, 200)]
    report = first_crossings(rows, [True, True, False], [.9, .8, 1.], .7, 32)
    assert report["first_trigger_count"] == 1
    assert report["premature_token_window_count"] == 1
    assert report["rows"][0]["snapshot_ts_ms"] == 1
    assert report["eligible_near_window_requests"] == 1


def test_low_coverage_cannot_win_by_avoiding_all_triggers():
    no_trigger = {
        "first_trigger_count": 0, "premature_token_window_count": 0,
        "within_token_window_count": 0,
    }
    full = {
        "first_trigger_count": 3, "premature_token_window_count": 1,
        "within_token_window_count": 2,
    }
    assert selection_rank(full, 3) < selection_rank(no_trigger, 3)


def test_matching_coverage_prefers_fewer_premature_first_crossings():
    reports = [
        {"first_trigger_count": 4, "premature_token_window_count": 2,
         "within_token_window_count": 2},
        {"first_trigger_count": 4, "premature_token_window_count": 1,
         "within_token_window_count": 3},
    ]
    assert selection_rank(reports[1], 3) < selection_rank(reports[0], 3)


def test_loss_weights_balance_workflows_and_requests_despite_snapshot_density():
    rows = [
        sample("a", 1, 1), sample("a", 2, 1), sample("b", 1, 1),
        sample("c", 1, 1, task="other"),
    ]
    weights = request_weights(rows)
    assert weights.sum() == pytest.approx(4)
    assert weights[:3].sum() == pytest.approx(weights[3])
    assert weights[:2].sum() == pytest.approx(weights[2])
    assert np.isfinite(weights).all()


def test_unaligned_sample_populations_are_rejected():
    with pytest.raises(ValueError, match="populations differ"):
        first_crossings([sample("r", 1, 10)], [], [.9], .5, 32)
