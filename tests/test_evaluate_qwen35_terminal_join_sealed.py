import json

import pytest

from scripts.evaluate_qwen35_terminal_join_sealed import (
    score_sealed,
    validate_sealed_run,
)


def _frozen_run(tmp_path):
    test = tmp_path / "test.json"
    provenance = tmp_path / "provenance.json"
    workflows = tmp_path / "run" / "workflows"
    workflows.mkdir(parents=True)
    ids = ["matplotlib__one", "scikit-learn__two"]
    test.write_text(json.dumps({
        "split": "test_id",
        "workloads": [{"instance_id": id_} for id_ in ids],
    }), encoding="utf-8")
    provenance.write_text(json.dumps({
        "test_ids": ids, "test_workflows": 2,
        "test_projects": ["matplotlib", "scikit-learn"],
    }), encoding="utf-8")
    import hashlib
    (workflows.parent / "manifest.json").write_text(json.dumps({
        "workload_manifest_sha256": hashlib.sha256(test.read_bytes()).hexdigest(),
        "instance_ids": ids,
        "config": {
            "loop_guard": {"activation_wall_clock_s": 14400},
            "request_timeout_s": 7200, "model": "Qwen3.5-35B-A3B",
            "subagent_fanout_profile": "native_dynamic_1to4",
        },
    }), encoding="utf-8")
    return test, provenance, workflows, ids


def test_sealed_runner_identity_and_training_only_scoring(tmp_path, monkeypatch):
    from scripts import evaluate_qwen35_terminal_join_sealed as sealed

    test, provenance, workflows, ids = _frozen_run(tmp_path)
    monkeypatch.setattr(sealed, "require_complete_batch", lambda _: (ids, []))
    calls = []
    monkeypatch.setattr(sealed, "evaluate", lambda train, heldout: (
        calls.append((train, heldout)) or {
            "status": "project_disjoint_development_not_action_eligible",
            "heldout_source": {
                "frozen_projects": ["matplotlib", "scikit-learn"],
            },
            "heldout": {"selected": 0},
        }
    ))
    train = [(tmp_path / "train", tmp_path / "train-audit")]
    report = score_sealed(test, provenance, train, (workflows, tmp_path / "audit"))
    assert calls == [(train, (workflows, tmp_path / "audit"))]
    assert report["status"] == "project_disjoint_sealed_shadow_not_action_eligible"
    assert report["sealed_identity"]["test_workflows"] == 2
    assert report["heldout"]["selected"] == 0


def test_sealed_runner_rejects_wrong_manifest_and_deadline(tmp_path, monkeypatch):
    from scripts import evaluate_qwen35_terminal_join_sealed as sealed

    test, provenance, workflows, ids = _frozen_run(tmp_path)
    monkeypatch.setattr(sealed, "require_complete_batch", lambda _: (ids, []))
    run_path = workflows.parent / "manifest.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["config"]["loop_guard"]["activation_wall_clock_s"] = 900
    run_path.write_text(json.dumps(run), encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected agent configuration"):
        validate_sealed_run(test, provenance, workflows)
    run["config"]["loop_guard"]["activation_wall_clock_s"] = 14400
    run["workload_manifest_sha256"] = "wrong"
    run_path.write_text(json.dumps(run), encoding="utf-8")
    with pytest.raises(ValueError, match="frozen test manifest"):
        validate_sealed_run(test, provenance, workflows)
