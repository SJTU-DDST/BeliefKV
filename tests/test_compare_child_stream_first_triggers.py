from collections import Counter

import pytest

from scripts import compare_child_stream_first_triggers as paired


def test_first_trigger_gain_bootstraps_whole_workflows(monkeypatch, tmp_path):
    rows = [
        {"task": f"other__{i}", "project": "other", "label": "return"}
        for i in range(2)
    ] + [
        {"task": f"held__{i}", "project": "held", "label": label}
        for i in range(6)
        for label in ("return", "return", "tool")
    ]
    monkeypatch.setattr(paired, "collect", lambda *args, **kwargs: (rows, Counter()))
    baseline = {
        "heldout_return_rounds": 12,
        "heldout_tool_rounds": 6,
        "heldout_window_hits_by_workflow": {"held__0": 1, "held__2": 1},
        "lead_between_500_and_2000ms": 2,
        "heldout_tool_false_first_triggers": 0,
        "offline_service_audit": {
            "live_window_hits_by_workflow": {"held__0": 1},
            "window_hits_with_recent_decode": 1,
        },
    }
    candidate = {
        **baseline,
        "heldout_window_hits_by_workflow": {
            f"held__{i}": 1 for i in range(6)
        },
        "lead_between_500_and_2000ms": 6,
        "heldout_tool_false_first_triggers": 1,
        "offline_service_audit": {
            "live_window_hits_by_workflow": {
                f"held__{i}": 1 for i in range(5)
            },
            "window_hits_with_recent_decode": 5,
        },
    }
    report = {
        "heldout_project": "held",
        "min_snapshot_chars": 32,
        "exclude_boundary_snapshots": False,
        "results": {"baseline": baseline, "candidate": candidate},
    }
    result = paired.compare(
        report, [tmp_path], "baseline", "candidate", draws=250,
    )
    assert result["workflow_count"] == 6
    assert result["natural_returns"] == 12
    assert result["absolute_recall_gain"] == pytest.approx(4 / 12)
    assert result["workflow_bootstrap_95pct_ci"][0] > 0
    live = paired.compare(
        report, [tmp_path], "baseline", "candidate",
        service_live=True, draws=250,
    )
    assert live["absolute_recall_gain"] == pytest.approx(4 / 12)
    assert live["baseline_window_hits"] == 1
    assert live["candidate_window_hits"] == 5


def test_first_trigger_gain_rejects_unmatched_denominators(monkeypatch, tmp_path):
    monkeypatch.setattr(paired, "collect", lambda *args, **kwargs: (
        [{"task": "held__1", "project": "held", "label": "return"}],
        Counter(),
    ))
    report = {
        "heldout_project": "held", "min_snapshot_chars": 32,
        "exclude_boundary_snapshots": False,
        "results": {
            "base": {"heldout_return_rounds": 2, "heldout_tool_rounds": 0},
            "candidate": {"heldout_return_rounds": 1, "heldout_tool_rounds": 0},
        },
    }
    with pytest.raises(ValueError, match="denominators"):
        paired.compare(report, [tmp_path], "base", "candidate")
