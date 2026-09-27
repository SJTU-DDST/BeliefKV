import hashlib
import json

import pytest

from scripts.score_qwen35_native_official import score_run


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inputs(tmp_path):
    client, evaluation = tmp_path / "client", tmp_path / "grading"
    ids = ["repo__code-1", "repo__code-2", "repo__code-3"]
    _write(client / "manifest.json", {"run_id": "run", "instance_ids": ids})
    _write(client / "summary.json", {
        "run_id": "run", "workflow_count": 3, "duration_seconds": 100.0,
        "workflows": [
            {"instance_id": ids[0], "outcome": "completed",
             "duration_seconds": 20.0, "native_agent_jct_eligible": True},
            {"instance_id": ids[1], "outcome": "completed",
             "duration_seconds": 30.0, "native_agent_jct_eligible": True},
            {"instance_id": ids[2], "outcome": "completed",
             "duration_seconds": 40.0, "native_agent_jct_eligible": True},
        ],
    })
    predictions = {}
    hashes = {}
    for instance_id in ids:
        patch = client / "workflows" / instance_id / "model.patch"
        patch.parent.mkdir(parents=True)
        patch.write_text("" if instance_id == ids[2] else "diff\n")
        hashes[instance_id] = _sha(patch)
        predictions[instance_id] = {
            "instance_id": instance_id,
            "model_name_or_path": "beliefkv-run",
            "model_patch": patch.read_text(),
        }
    _write(evaluation / "preds.json", predictions)
    _write(evaluation / "manifest.json", {
        "run_id": "run", "instance_ids": ids,
        "source_summary_sha256": _sha(client / "summary.json"),
        "source_client_manifest_sha256": _sha(client / "manifest.json"),
        "predictions_sha256": _sha(evaluation / "preds.json"),
        "patch_sha256_by_instance": hashes,
    })
    _write(evaluation / "beliefkv-run.grade.json", {
        "total_instances": 3, "submitted_ids": ids,
        "completed_ids": ids[:2], "resolved_ids": ids[:1],
        "unresolved_ids": ids[1:2], "empty_patch_ids": ids[2:],
        "incomplete_ids": [], "error_ids": [],
    })
    for instance_id in ids[:2]:
        _write(
            evaluation / "logs/run_evaluation/grade/beliefkv-run"
            / instance_id / "report.json",
            {instance_id: {"resolved": instance_id == ids[0]}},
        )
    return client, evaluation


def test_joins_official_resolution_to_eligible_native_jct(tmp_path):
    client, evaluation = _inputs(tmp_path)
    result = score_run(client, evaluation, "grade")
    assert result["measurement_complete"]
    assert result["correctly_completed_ids"] == ["repo__code-1"]
    assert result["empty_patch_ids"] == ["repo__code-3"]
    assert result["correctly_completed_workflows_per_hour"] == 36.0
    assert result["correctly_completed_jct_p50_seconds"] == 20.0


@pytest.mark.parametrize("index", (0, 1))
def test_ineligible_workflow_cannot_claim_a_comparable_rate(tmp_path, index):
    client, evaluation = _inputs(tmp_path)
    summary_path = client / "summary.json"
    summary = json.loads(summary_path.read_text())
    summary["workflows"][index]["native_agent_jct_eligible"] = False
    _write(summary_path, summary)
    manifest_path = evaluation / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["source_summary_sha256"] = _sha(summary_path)
    _write(manifest_path, manifest)
    result = score_run(client, evaluation, "grade")
    assert not result["measurement_complete"]
    assert result["correctly_completed_workflows_per_hour"] is None


@pytest.mark.parametrize("changed", ("summary", "patch", "report", "instance_report"))
def test_rejects_mismatched_or_stale_evidence(tmp_path, changed):
    client, evaluation = _inputs(tmp_path)
    paths = {
        "summary": client / "summary.json",
        "patch": client / "workflows/repo__code-1/model.patch",
        "report": evaluation / "beliefkv-run.grade.json",
        "instance_report": evaluation /
        "logs/run_evaluation/grade/beliefkv-run/repo__code-1/report.json",
    }
    path = paths[changed]
    if changed == "summary":
        value = json.loads(path.read_text())
        value["duration_seconds"] = 200
        _write(path, value)
    elif changed == "patch":
        path.write_text("changed\n")
    elif changed == "report":
        value = json.loads(path.read_text())
        value["resolved_ids"] = ["repo__code-2"]
        _write(path, value)
    else:
        _write(path, {"repo__code-1": {"resolved": False}})
    with pytest.raises(ValueError):
        score_run(client, evaluation, "grade")
