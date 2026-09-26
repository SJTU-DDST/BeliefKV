import json
import subprocess
import sys

import pytest

from scripts import evaluate_join_rolling_stage as rolling


def _stage(ts=4.0):
    return {
        "task_id": "django__one",
        "project": "django",
        "invocation_id": "child",
        "request_id": "rid",
        "context_id": "ctx",
        "context_epoch": 3,
        "stage_threshold_chars": 1700,
        "signal_ts_ms": ts,
        "label": "true",
    }


def _events(*, sibling_return=2.0, cancellation=False):
    events = [
        {"kind": "join_create", "ts_ms": 0., "join_id": "join",
         "member_invocation_ids": ["child", "sibling"],
         "attributes": {"mode": "all"}},
        {"kind": "join_wait", "ts_ms": 1., "join_id": "join"},
        {"kind": "return", "ts_ms": sibling_return,
         "invocation_id": "sibling"},
        {"kind": "structured_action", "ts_ms": 4., "join_id": "join",
         "invocation_id": "child", "context_id": "ctx", "context_epoch": 3,
         "attributes": {
             "request_id": "rid", "content_threshold_chars": 1700,
             "beliefkv_child_substantial_content_shadow": True,
         }},
        {"kind": "join_satisfied", "ts_ms": 9., "join_id": "join"},
    ]
    if cancellation:
        events.append({
            "kind": "invocation_cancel", "ts_ms": 3.,
            "invocation_id": "sibling",
        })
    return events


def test_stage_requires_known_sibling_return_and_matching_identity():
    members = {"child", "sibling"}
    kwargs = {"join_id": "join", "created": 0., "waited": 1.,
              "satisfied": 9.}
    assert rolling._sole_pending(_events(), _stage(), members, **kwargs)
    assert not rolling._sole_pending(
        _events(sibling_return=5.), _stage(), members, **kwargs,
    )
    assert not rolling._sole_pending(
        _events(cancellation=True), _stage(), members, **kwargs,
    )
    assert not rolling._sole_pending(
        _events(), {**_stage(), "request_id": "stale"}, members, **kwargs,
    )
    assert not rolling._sole_pending(
        _events(), _stage(ts=9.), members, **kwargs,
    )


def test_first_causal_stage_keeps_posthoc_label_separate(
    tmp_path, monkeypatch,
):
    workflows = tmp_path / "workflows"
    workflow = workflows / "django__one"
    workflow.mkdir(parents=True)
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "\n".join(json.dumps(event) for event in _events()) + "\n",
        encoding="utf-8",
    )
    group = {
        "task_id": "django__one", "join_id": "join", "label": "natural",
        "trigger_ts_ms": 3., "lead_ms": 6., "parent_reentry_lead_ms": 7.,
    }
    monkeypatch.setattr(
        rolling, "collect_groups",
        lambda _workflows: ([group], {"all_mode_groups": 1}),
    )
    monkeypatch.setattr(
        rolling, "collect_stages",
        lambda _workflows, threshold: (
            [{**_stage(), "stage_threshold_chars": threshold}],
            {"observed_stages": 1},
        ),
    )
    rows, counts = rolling.collect(workflows, 1700)
    assert len(rows) == 1
    assert rows[0]["join_lead_ms"] == 5.
    assert rows[0]["parent_lead_ms"] == 6.
    assert counts["sole_pending_natural"] == 1
    assert counts["sole_pending_parent_reentry"] == 1
    with pytest.raises(ValueError, match="unsupported"):
        rolling.collect(workflows, 777)
    assert 2400 in rolling.OBSERVED_THRESHOLDS
    assert 2400 not in rolling.THRESHOLDS


def test_project_split_rejects_overlap_even_without_stage_candidates(
    tmp_path, monkeypatch,
):
    train = tmp_path / "train" / "workflows"
    heldout = tmp_path / "heldout" / "workflows"
    for root, task in ((train, "django__one"), (heldout, "django__two")):
        path = root / task
        path.mkdir(parents=True)
        (path / "runtime_events.deepagents.jsonl").write_text(
            "", encoding="utf-8",
        )
    monkeypatch.setattr(
        rolling, "collect",
        lambda *_args: pytest.fail("identity must precede stage collection"),
    )
    with pytest.raises(ValueError, match="disjoint projects"):
        rolling.evaluate(train, heldout)


def test_heldout_join_time_cannot_change_training_stage_prior(
    tmp_path, monkeypatch,
):
    train = tmp_path / "train" / "workflows"
    heldout = tmp_path / "heldout" / "workflows"
    for root, tasks in (
        (train, ("django__one", "django__two")),
        (heldout, ("psf__three",)),
    ):
        for task in tasks:
            path = root / task
            path.mkdir(parents=True)
            (path / "runtime_events.deepagents.jsonl").write_text(
                "", encoding="utf-8",
            )
    train_rows = [
        {"task_id": "django__one", "project": "django",
         "group_label": "natural", "label": "true",
         "join_lead_ms": 1500., "parent_lead_ms": 1600.},
        {"task_id": "django__two", "project": "django",
         "group_label": "natural", "label": "true",
         "join_lead_ms": 2500., "parent_lead_ms": 2600.},
    ]
    heldout_rows = [
        {"task_id": "psf__three", "project": "psf",
         "group_label": "natural", "label": "true",
         "join_lead_ms": 2000., "parent_lead_ms": 2100.},
    ]
    monkeypatch.setattr(
        rolling, "collect",
        lambda root, _threshold: (
            train_rows if root == train else heldout_rows,
            {"sole_pending_candidates": len(train_rows if root == train
                                            else heldout_rows)},
        ),
    )
    first = rolling.evaluate(train, heldout)
    assert first["1700"]["train_stage_prior_ms"] == 2000.
    assert first["1700"]["train_parent_reentry_prior_ms"] == 2100.
    assert first["1700"]["heldout_join_point_error_ms"][
        "median_absolute_error_ms"
    ] == 0.
    assert first["1700"]["heldout_by_project"]["psf"][
        "parent_reentry_point_error_ms"
    ]["median_absolute_error_ms"] == 0.
    heldout_rows[0]["join_lead_ms"] = 9000.
    second = rolling.evaluate(train, heldout)
    assert second["1700"]["train_stage_prior_ms"] == 2000.
    assert second["1700"]["train_parent_reentry_prior_ms"] == 2100.
    assert second["1700"]["heldout_join_point_error_ms"][
        "median_absolute_error_ms"
    ] == 7000.
    assert second["1700"]["heldout_by_project"]["psf"]["tasks"] == 1


def test_heldout_project_with_no_stage_is_reported(
    tmp_path, monkeypatch,
):
    train = tmp_path / "train" / "workflows"
    heldout = tmp_path / "heldout" / "workflows"
    for root, name in ((train, "django__one"), (heldout, "astropy__two")):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "runtime_events.deepagents.jsonl").write_text(
            "", encoding="utf-8",
        )
    monkeypatch.setattr(
        rolling, "collect", lambda *_args: ([], {"sole_pending_candidates": 0}),
    )
    result = rolling.evaluate(train, heldout)
    project = result["1700"]["heldout_by_project"]["astropy"]
    assert project["tasks"] == 1
    assert project["first_sole_pending_candidates"] == 0
    assert project["natural_join_point_error_ms"] is None


def test_frozen_tasks_without_trace_still_count_in_project_denominator(
    tmp_path, monkeypatch,
):
    train = tmp_path / "train" / "workflows"
    heldout = tmp_path / "heldout" / "workflows"
    train.mkdir(parents=True)
    heldout.mkdir(parents=True)
    monkeypatch.setattr(
        rolling, "collect",
        lambda *_args: ([], {"sole_pending_candidates": 0}),
    )
    report = rolling.evaluate(
        train, heldout, frozen_train_ids=["django__one"],
        frozen_heldout_ids=["astropy__one", "sphinx-doc__no_trace"],
    )
    assert report["1700"]["heldout_by_project"]["astropy"]["tasks"] == 1
    assert report["1700"]["heldout_by_project"]["sphinx-doc"]["tasks"] == 1
    assert report["1700"]["heldout_by_project"]["sphinx-doc"][
        "first_sole_pending_candidates"
    ] == 0


def test_join_cli_rejects_incomplete_batches(tmp_path):
    run = subprocess.run(
        [
            sys.executable,
            str(rolling.ROOT / "scripts" / "evaluate_join_rolling_stage.py"),
            "--train-workflows", str(tmp_path / "train" / "workflows"),
            "--heldout-workflows", str(tmp_path / "heldout" / "workflows"),
            "--output", str(tmp_path / "report.json"),
        ],
        capture_output=True, text=True,
    )
    assert run.returncode != 0
    assert "final manifest and summary" in run.stderr
    assert not (tmp_path / "report.json").exists()


def test_cli_runs_from_outside_repository(tmp_path):
    run = subprocess.run(
        [sys.executable, str(rolling.ROOT / "scripts" /
                             "evaluate_join_rolling_stage.py"), "--help"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    assert "--heldout-workflows" in run.stdout
