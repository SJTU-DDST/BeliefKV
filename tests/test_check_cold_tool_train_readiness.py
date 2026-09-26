import pytest
import subprocess
import sys

from scripts import check_cold_tool_train_readiness as readiness


def _calls():
    return [
        {
            "project": project,
            "workflow": f"{project}__{index}",
            "duration_ms": 3000 if index < 10 else 100,
        }
        for project in ("django", "pydata", "pytest-dev")
        for index in range(20)
    ]


def test_training_only_readiness_freezes_qualified_screen(
    monkeypatch, tmp_path,
):
    calls = _calls()
    monkeypatch.setattr(
        readiness, "require_complete_batch",
        lambda _path: ([f"root-{i}" for i in range(128)], []),
    )
    monkeypatch.setattr(
        readiness, "cold_calls", lambda _path: (calls, {"open": 0}),
    )
    selected = []

    def screen(rows, **kwargs):
        selected.append((rows, kwargs))
        return {"threshold_chosen_on_train_cv": 0.5}

    monkeypatch.setattr(readiness, "train_shape_screen", screen)
    report = readiness.assess(tmp_path)
    assert report["ready_for_project_holdout"]
    assert report["checks"]["train_cv_long_screen_qualified"]
    assert report["long_by_project"] == {
        "django": 10, "pydata": 10, "pytest-dev": 10,
    }
    assert selected == [(
        calls, {"include_live_peers": True, "include_long_history": True},
    )]


def test_incomplete_or_one_project_long_batch_does_not_fit(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        readiness, "require_complete_batch",
        lambda _path: ([f"root-{i}" for i in range(127)], []),
    )
    calls = _calls()
    for row in calls:
        if row["project"] != "django":
            row["duration_ms"] = 100
    monkeypatch.setattr(
        readiness, "cold_calls", lambda _path: (calls, {}),
    )
    monkeypatch.setattr(
        readiness, "train_shape_screen",
        lambda *_args, **_kwargs: pytest.fail(
            "unqualified training must not fit the screen",
        ),
    )
    report = readiness.assess(tmp_path)
    assert not report["ready_for_project_holdout"]
    assert not report["checks"]["expected_frozen_workflows"]
    assert not report["checks"]["long_calls_from_two_projects"]
    assert "train_project_cv_screen" not in report


def test_missing_final_summary_is_not_a_partial_training_batch(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        readiness, "require_complete_batch",
        lambda _path: (_ for _ in ()).throw(
            ValueError("batch must have a final manifest and summary"),
        ),
    )
    monkeypatch.setattr(
        readiness, "cold_calls",
        lambda _path: pytest.fail("partial trace must not be fitted"),
    )
    with pytest.raises(ValueError, match="final manifest and summary"):
        readiness.assess(tmp_path)


def test_cli_runs_from_outside_repository(tmp_path):
    run = subprocess.run(
        [sys.executable, str(readiness.ROOT / "scripts" /
                             "check_cold_tool_train_readiness.py"), "--help"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    assert "--train-workflows" in run.stdout
