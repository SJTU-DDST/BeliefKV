from copy import deepcopy
import json
import sys

import pytest

from scripts import review_workflow_ranking_disabled as module
from scripts.review_workflow_ranking_disabled import matching


def test_matched_ranking_review_rejects_changed_traffic_or_capacity():
    plan = {"order": ["full"], "source_arm": "original", "expected_replay_capacity": {"Host": 400}}
    matching([plan, deepcopy(plan)])
    for change in (
        {"order": ["thunderagent_acting_decay"]},
        {"source_arm": "different"},
        {"expected_replay_capacity": {"Host": 200}},
    ):
        with pytest.raises(ValueError):
            matching([plan, {**plan, **change}])


def test_watcher_updates_mutable_state_until_complete_cohort(monkeypatch, tmp_path):
    cohort, config = tmp_path / "cohort.json", tmp_path / "config.json"
    cohort.write_text("{}")
    config.write_text("{}")
    run = tmp_path / "run"
    (run / "cohort_termination").mkdir(parents=True)
    registrations, directories = [], []
    for name in ("enabled", "disabled", "old-disabled"):
        registration = tmp_path / (name + ".json")
        registration.write_text(json.dumps({
            "run_root": str(run), "experiment_configuration": str(config),
        }))
        registration.with_suffix(".state.json").write_text('{"status":"running_full"}')
        directory = tmp_path / (name + "-review")
        directory.mkdir()
        for report in ("review.json", "cohort.json", "workload_decision.json"):
            (directory / report).write_text("{}")
        registrations.append(registration)
        directories.append(directory)
    output = tmp_path / "matched.json"
    argv = ["review", "--cohort", str(cohort), "--output", str(output), "--watch"]
    for name, path, directory in zip(
        ("enabled", "disabled", "old-disabled"), registrations, directories,
    ):
        argv.extend(("--" + name, str(path), "--" + name + "-review", str(directory)))
    updates = []

    def advance(_seconds):
        state = json.loads(output.with_suffix(".watcher.json").read_bytes())
        updates.append(state["status"])
        if len(updates) == 2:
            (run / "cohort_termination/completion.json").write_text("{}")

    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "99999")
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(module.time, "sleep", advance)
    monkeypatch.setattr(module, "review", lambda *_args: {"verified": True})
    module.main()
    assert updates == ["waiting_fixed18", "waiting_fixed18"]
    assert json.loads(output.read_bytes())["verified"] is True
    assert json.loads(output.with_suffix(".watcher.json").read_bytes())["status"] == "complete_verified"
