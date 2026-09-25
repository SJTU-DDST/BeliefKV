from __future__ import annotations

import pytest

from scripts.evaluate_stream_sibling_eta import evaluate, forecast


def _row(
    project: str, child: str, trigger: float, lead: float,
    *, join: str = "join", final: bool = True,
) -> dict:
    return {
        "trace_path": f"/data/{project}__task/runtime_events.deepagents.jsonl",
        "child": child,
        "join_id": join,
        "request_id": child,
        "trigger_ms": trigger,
        "lead_ms": lead if final else None,
        "final": final,
        "last_join_child": child == "target",
    }


def test_sibling_history_requires_same_join_and_completed_return():
    rows = [
        _row("pydata", "early", 100, 900),
        _row("pydata", "still_running", 800, 3000),
        _row("pydata", "other_join", 200, 1200, join="another"),
        _row("pydata", "target", 1500, 1100),
        _row("pydata", "other_target", 1600, 1100, join="another"),
        _row("pydata", "nonfinal", 300, 0, final=False),
    ]
    predictions = {
        row["request_id"]: row for row in forecast(rows, prior_ms=2000)
    }
    assert predictions["target"]["sibling_count"] == 1
    assert predictions["target"]["sibling_shrunk_ms"] == 1450
    assert predictions["other_target"]["sibling_count"] == 1
    assert "nonfinal" not in predictions


def test_project_holdout_does_not_count_nonfinal_as_correct():
    train = [
        _row("django", str(index), 0, 2000)
        for index in range(20)
    ]
    heldout = [
        _row("pydata", "first", 0, 900),
        _row("pydata", "target", 2000, 1200),
        _row("pydata", "bad", 2500, 0, final=False),
    ]
    with pytest.raises(ValueError, match="projects overlap"):
        evaluate(train, train, 0)
    report = evaluate(train, heldout, 1)
    assert report["frozen_prior_ms"] == 2000
    assert report["heldout"]["natural_returns"] == 2
    assert report["heldout"]["sibling_available"] == 1
    assert report["heldout_last_join_child"]["natural_returns"] == 1
    assert report["heldout_nonfinal"] == 1
    assert report["heldout_censored"] == 1
    assert report["status"].startswith("read_only_return_conditioned")
