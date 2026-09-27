import hashlib
import json

import pytest

from scripts.export_qwen35_swebench_predictions import export_predictions


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _inputs(tmp_path):
    client = tmp_path / "client"
    frozen = tmp_path / "workload.json"
    task = {
        "instance_id": "org__repo-1",
        "base_commit": "base",
        "repo": "org/repo",
        "docker_image": "image:tag",
    }
    _write(frozen, {
        "dataset": "princeton-nlp/SWE-bench_Verified",
        "dataset_revision": "revision",
        "workloads": [task],
    })
    patch = client / "workflows" / task["instance_id"] / "model.patch"
    patch.parent.mkdir(parents=True)
    patch.write_text("diff text\n", encoding="utf-8")
    _write(client / "manifest.json", {
        "run_id": "run-a",
        "instance_ids": [task["instance_id"]],
        "dataset": "princeton-nlp/SWE-bench_Verified",
        "dataset_revision": "revision",
        "workload_manifest_sha256": hashlib.sha256(frozen.read_bytes()).hexdigest(),
        "config": {"model": "Qwen3.5-35B-A3B"},
    })
    _write(client / "summary.json", {
        "run_id": "run-a",
        "workflow_count": 1,
        "workflows": [{**task, "outcome": "completed", "patch_chars": 9}],
    })
    return client, frozen, patch


def test_export_preserves_natural_terminal_without_claiming_success(tmp_path):
    client, frozen, patch = _inputs(tmp_path)
    output = tmp_path / "out"
    result = export_predictions(client, frozen, output)
    assert not result["official_correctness_evaluated"]
    prediction = json.loads((output / "preds.json").read_text())["org__repo-1"]
    assert prediction["model_patch"] == patch.read_text()
    assert prediction["model_name_or_path"] == "beliefkv-Qwen3.5-35B-A3B-run-a"
    assert result["patch_sha256_by_instance"]["org__repo-1"] == hashlib.sha256(
        patch.read_bytes()
    ).hexdigest()
    with pytest.raises(FileExistsError):
        export_predictions(client, frozen, output)


@pytest.mark.parametrize("change", (
    lambda client, frozen, patch: patch.write_text("changed\n"),
    lambda client, frozen, patch: _write(
        client / "manifest.json", {"run_id": "wrong"}
    ),
    lambda client, frozen, patch: _write(
        frozen, {"dataset": "other", "workloads": []}
    ),
    lambda client, frozen, patch: _write(
        client / "summary.json", {"run_id": "run-a", "workflow_count": 0, "workflows": []}
    ),
))
def test_export_rejects_stale_or_mismatched_artifacts(tmp_path, change):
    client, frozen, patch = _inputs(tmp_path)
    change(client, frozen, patch)
    with pytest.raises(ValueError):
        export_predictions(client, frozen, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_empty_patch_is_exported_for_official_classification(tmp_path):
    client, frozen, patch = _inputs(tmp_path)
    patch.write_text("")
    summary_path = client / "summary.json"
    summary = json.loads(summary_path.read_text())
    summary["workflows"][0]["patch_chars"] = 0
    _write(summary_path, summary)
    output = tmp_path / "out"
    export_predictions(client, frozen, output)
    assert json.loads((output / "preds.json").read_text())["org__repo-1"][
        "model_patch"
    ] == ""
