import json
from pathlib import Path

import pytest

from scripts.freeze_qwen35_terminal_join_sealed import freeze


REVISION = "frozen-revision"
DATASET = "princeton-nlp/SWE-bench_Verified"


def _workload(project, index):
    return {
        "instance_id": f"{project}__issue-{index}",
        "repo": f"{project}/{project}",
        "base_commit": f"{index:040x}",
        "problem_statement": "Frozen issue.",
        "source_repo": f"/sources/{project}",
        "docker_image": f"image/{project}-{index}:frozen",
    }


def _write(path, rows, *, split):
    path.write_text(json.dumps({
        "dataset": DATASET, "dataset_revision": REVISION,
        "split": split, "workloads": rows,
    }), encoding="utf-8")


def test_freeze_keeps_test_projects_out_of_background_and_in_manifest(tmp_path):
    test_a = tmp_path / "test-a.json"
    test_b = tmp_path / "test-b.json"
    training = tmp_path / "train.json"
    test = [
        _workload(project, index)
        for project in ("matplotlib", "scikit-learn")
        for index in range(8)
    ]
    _write(test_a, test[:8], split="test_id")
    _write(test_b, test[8:] + test[:1], split="test_id")
    _write(training, [
        _workload(project, index)
        for project in ("django", "psf", "pydata", "pylint-dev")
        for index in range(10)
    ], split="train")
    frozen = freeze([test_a, test_b], training)
    heldout = frozen["test"]["workloads"]
    background = frozen["background"]["workloads"]
    assert len(heldout) == 16
    assert len(background) == 32
    assert [row["instance_id"].split("__")[0] for row in heldout] == [
        "matplotlib", "scikit-learn",
    ] * 8
    assert {row["instance_id"] for row in heldout}.isdisjoint(
        {row["instance_id"] for row in background}
    )
    assert all(row["docker_image"] for row in heldout + background)
    assert len(frozen["provenance"]["test_source_sha256"]) == 2


def test_freeze_rejects_conflicting_image_and_training_overlap(tmp_path):
    test_a = tmp_path / "test-a.json"
    test_b = tmp_path / "test-b.json"
    training = tmp_path / "train.json"
    tasks = [
        _workload(project, index)
        for project in ("matplotlib", "scikit-learn")
        for index in range(8)
    ]
    _write(test_a, tasks, split="test_id")
    _write(test_b, [{**tasks[0], "docker_image": "wrong-image"}], split="test_id")
    _write(training, [
        _workload("django", index) for index in range(32)
    ], split="train")
    with pytest.raises(ValueError, match="conflicting test workload"):
        freeze([test_a, test_b], training)
    _write(test_b, [], split="test_id")
    _write(training, [
        _workload("django", index) for index in range(32)
    ] + [tasks[0]], split="train")
    with pytest.raises(ValueError, match="occurs in background"):
        freeze([test_a, test_b], training)


def test_freeze_rejects_mismatched_training_split_and_revision(tmp_path):
    test_source = tmp_path / "test.json"
    training = tmp_path / "train.json"
    _write(test_source, [
        _workload(project, index)
        for project in ("matplotlib", "scikit-learn")
        for index in range(8)
    ], split="test_id")
    rows = [_workload("django", index) for index in range(32)]
    _write(training, rows, split="test_id")
    with pytest.raises(ValueError, match="wrong training dataset, split, or revision"):
        freeze([test_source], training)
    training.write_text(json.dumps({
        "dataset": DATASET, "dataset_revision": "different",
        "split": "train", "workloads": rows,
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="wrong training dataset, split, or revision"):
        freeze([test_source], training)


def test_checked_in_sealed_manifests_match_frozen_sources(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(root)
    frozen_dir = root / "configs/migration/qwen35_terminal_join_sealed_2026-09-27"
    test_sources = sorted(
        Path("configs/p6/collection_v2/workload_manifests").glob(
            "*-test_id*.json"
        )
    )
    training_source = Path(
        "configs/migration/"
        "qwen35_native_reactive_overlapped_128root_workload_2026-09-23.json"
    )
    result = freeze(test_sources, training_source)
    for name in ("test", "background", "provenance"):
        assert result[name] == json.loads(
            (frozen_dir / f"{name}.json").read_text(encoding="utf-8")
        )
