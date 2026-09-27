from pathlib import Path
import subprocess
import sys

import pytest

from scripts.evaluate_join_online_prior import _balanced_prior, replay


def _group(task, when, lead, *, project="alpha", label="natural"):
    return {
        "task_id": task,
        "project": project,
        "trigger_ts_ms": when,
        "lead_ms": lead,
        "label": label,
    }


def test_replay_uses_only_completed_other_workflows_in_same_project():
    rows = replay([
        _group("alpha__one", 0, 500),
        _group("alpha__two", 100, 1000),
        _group("alpha__three", 600, 100),
        _group("beta__one", 650, 10, project="beta"),
        _group("alpha__four", 1200, 200),
    ], prior_ms=10_000, min_history=1, history_limit=2)
    assert [row["adapted_ms"] for row in rows] == [
        10_000, 10_000, 500, 10_000, 550,
    ]
    assert rows[2]["causal_history"] == 1
    assert rows[-1]["causal_history"] == 2


def test_censored_group_cannot_update_online_prior():
    rows = replay([
        _group("alpha__missing", 0, None, label="censored"),
        _group("alpha__one", 10, 20),
        _group("alpha__two", 40, 10),
    ], prior_ms=1000, min_history=1, history_limit=4)
    assert len(rows) == 2
    assert rows[0]["adapted_ms"] == 1000
    assert rows[1]["adapted_ms"] == 20


def test_same_workflow_and_exact_completion_time_are_not_history():
    rows = replay([
        _group("alpha__one", 0, 10),
        _group("alpha__two", 10, 40),
        _group("alpha__one", 100, 20),
    ], prior_ms=1000, min_history=1, history_limit=4)
    assert rows[1]["adapted_ms"] == 1000
    assert rows[-1]["adapted_ms"] == 40


def test_balanced_prior_weights_tasks_not_repeated_invocations():
    episodes = [
        {"task_id": "a", "lead_ms": value}
        for value in (10, 10, 10, 10)
    ] + [{"task_id": "b", "lead_ms": 100}]
    assert _balanced_prior(episodes) == 55


def test_invalid_history_window_is_rejected():
    with pytest.raises(ValueError, match="history window"):
        replay([], prior_ms=10, min_history=2, history_limit=1)


def test_direct_cli_is_importable():
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts/evaluate_join_online_prior.py"
    )
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--train-workflows" in result.stdout
