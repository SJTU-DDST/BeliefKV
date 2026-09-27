import pytest

from scripts.evaluate_tool_window_online_history import (
    _first_returns, choose_policy, replay, summarize,
)


def _row(project, workflow, start, end, *, shape="tests", score=.3):
    return {
        "project": project,
        "workflow": workflow,
        "invocation": f"{workflow}-child",
        "tool_call_id": f"{workflow}-{start}",
        "input_sha256": f"{workflow}-{start}",
        "shape": shape,
        "score": score,
        "duration_ms": end - start,
        "start_ts_ms": start,
        "terminal_ts_ms": end,
    }


def test_causal_history_uses_only_other_workflows_completed_before_landmark():
    current = _row("astropy", "current", 1000, 2000)
    history = [
        _row("astropy", "a", 0, 800),
        _row("astropy", "b", 0, 810),
        _row("astropy", "c", 0, 820),
        _row("astropy", "current", 0, 900),
        _row("astropy", "future", 600, 1200),
        _row("sphinx", "other_project", 0, 900),
        _row("astropy", "other_shape", 0, 900, shape="git"),
    ]
    result, = replay(
        [current], history, min_history=3, min_fraction=1.,
    )
    assert result["causal_history_workflows"] == 3
    assert result["history_override"] is True
    assert summarize([result])["additional_from_history"]["true_windows"] == 1
    with pytest.raises(ValueError, match="invalid causal history"):
        replay([current], history, min_history=9, min_fraction=1.)


def test_only_first_input_can_update_history():
    rows = [
        _row("astropy", "a", 0, 850),
        _row("astropy", "a", 900, 1800),
        _row("astropy", "b", 0, 810),
    ]
    rows[1]["input_sha256"] = rows[0]["input_sha256"]
    first = _first_returns(rows)
    assert [(row["workflow"], row["start_ts_ms"]) for row in first] == [
        ("a", 0), ("b", 0),
    ]


def test_train_only_policy_requires_multiple_supported_projects():
    history = []
    candidates = []
    for project in ("django", "pydata", "pytest-dev"):
        history.extend([
            _row(project, f"{project}-history-{i}", 0, 900)
            for i in range(3)
        ])
        candidates.extend([
            _row(project, f"{project}-test-{i}", 1000, 1850)
            for i in range(7)
        ])
    policy, reports = choose_policy(candidates, history)
    assert policy == (3, 1.)
    assert reports["3:1.0"]["additional_from_history"]["true_windows"] == 21
    bad_history = [
        {**row, "duration_ms": 200} for row in history
        if row["project"] == "django"
    ] + [row for row in history if row["project"] != "django"]
    policy, _ = choose_policy(candidates, bad_history)
    assert policy is None


def test_added_window_eta_reports_workflow_clustered_gain():
    rows = [
        {
            **_row("astropy", f"workflow-{index}", 0, 840),
            "status": "success", "history_override": True,
            "baseline_selected": False, "eta_total_ms": 850.,
            "eta_global_ms": 1000.,
        }
        for index in range(5)
    ]
    result = summarize(rows)
    added = result["additional_from_history"]
    assert added["true_windows"] == 5
    assert added["eta_p50_absolute_error_ms"] == 10
    assert added["global_eta_p50_absolute_error_ms"] == 160
    assert result["additional_net_window_gain"][
        "workflow_bootstrap_95pct_ci"
    ][0] > 0
    assert result["added_true_window_eta_gain_vs_global"][
        "paired_positive_95pct"
    ] is True
