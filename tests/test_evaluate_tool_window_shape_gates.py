from scripts.evaluate_tool_window_shape_gates import (
    choose_gates, quality, training_oof,
)


def test_oof_never_fits_on_its_heldout_project(monkeypatch):
    projects = ("django", "pydata", "pytest-dev")
    seen = set()

    def fit(train, **_kwargs):
        return {row["project"] for row in train}

    def score(model, heldout, **_kwargs):
        assert len({row["project"] for row in heldout}) == 1
        project = heldout[0]["project"]
        assert project not in model
        seen.add(project)
        return [.7] * len(heldout)

    monkeypatch.setattr(
        "scripts.evaluate_tool_window_shape_gates._fit_shape_head", fit,
    )
    monkeypatch.setattr(
        "scripts.evaluate_tool_window_shape_gates._shape_scores", score,
    )
    rows = [
        {"project": project, "shape": "test", "workflow": project}
        for project in projects
    ]
    assert len(training_oof(rows)) == 3
    assert seen == set(projects)


def test_shape_gate_needs_precision_in_each_supported_training_project():
    rows = []
    for project in ("django", "pydata", "pytest-dev"):
        for shape in ("test_suite_targeted", "python_inline_simple"):
            for index in range(5):
                rows.append({
                    "project": project,
                    "workflow": f"{project}-{shape}-{index}",
                    "shape": shape,
                    "score": .85 if index < 2 else .65,
                    "duration_ms": (
                        850 if index < 4 else 150
                    ) if shape == "test_suite_targeted" else (
                        850 if index < (3 if project == "django" else 4)
                        else 150
                    ),
                })
    gates, evidence = choose_gates(
        rows, min_selected=12, min_projects=3,
    )
    assert gates == {"test_suite_targeted": .5}
    assert evidence["test_suite_targeted"]["0.5"]["precision"] == .8
    assert not evidence["python_inline_simple"]["0.5"]["qualified"]


def test_shape_gate_reports_project_recall_without_using_other_projects():
    rows = [
        {"project": "astropy", "workflow": "a", "shape": "test",
         "score": .6, "duration_ms": 850},
        {"project": "astropy", "workflow": "b", "shape": "test",
         "score": .85, "duration_ms": 100},
        {"project": "sphinx", "workflow": "s", "shape": "test",
         "score": .7, "duration_ms": 1000},
    ]
    result = quality(rows, {"test": .5})
    assert result["baseline"]["selected"] == 1
    assert result["shape_gate"]["true_windows"] == 2
    assert result["by_project"]["astropy"]["shape_gate"]["recall"] == 1
    assert result["by_project"]["sphinx"]["baseline"]["recall"] == 0
