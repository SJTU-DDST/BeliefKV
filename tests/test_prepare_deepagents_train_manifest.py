import subprocess

import pytest

from scripts.prepare_deepagents_train_manifest import frozen_prefix, parse_quota, selected_workloads


def test_train_selection_uses_frozen_base_and_excludes_previous_workflows(tmp_path):
    repo = tmp_path / "sources" / "pydata__xarray"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "source.txt").write_text("source")
    subprocess.run(["git", "-C", str(repo), "add", "source.txt"], check=True)
    subprocess.run([
        "git", "-C", str(repo), "-c", "user.name=Test", "-c",
        "user.email=test@example.com", "commit", "-qm", "source",
    ], check=True)
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
    ).strip()
    split = {
        "projects": [{
            "project": "pydata/xarray", "split": "train",
            "tasks": [
                {"instance_id": "pydata__xarray-1", "base_commit": commit},
                {"instance_id": "pydata__xarray-2", "base_commit": commit},
            ],
        }, {
            "project": "sympy/sympy", "split": "development", "tasks": [],
        }],
    }
    rows = {
        f"pydata__xarray-{number}": {
            "instance_id": f"pydata__xarray-{number}",
            "repo": "pydata/xarray",
            "base_commit": commit,
            "problem_statement": "an issue",
        }
        for number in (1, 2)
    }
    selected = selected_workloads(
        split, rows, repo.parent, {"pydata/xarray": 1},
        {"pydata__xarray-1"},
    )
    assert [item["instance_id"] for item in selected] == ["pydata__xarray-2"]
    assert selected[0]["source_repo"] == str(repo.resolve())
    assert selected[0]["docker_image"].endswith("pydata_1776_xarray-2:latest")
    with pytest.raises(ValueError, match="outside frozen train"):
        selected_workloads(split, rows, repo.parent, {"sympy/sympy": 1}, set())
    rows["pydata__xarray-2"]["base_commit"] = "wrong"
    with pytest.raises(ValueError, match="disagree"):
        selected_workloads(
            split, rows, repo.parent, {"pydata/xarray": 1},
            {"pydata__xarray-1"},
        )


@pytest.mark.parametrize("values", [
    [], ["pydata/xarray=0"], ["pydata/xarray=1", "pydata/xarray=2"],
])
def test_train_manifest_rejects_invalid_quotas(values):
    with pytest.raises(ValueError):
        parse_quota(values)


def test_frozen_prefix_preserves_order_and_rejects_split_leakage():
    rows = [
        {"instance_id": "train-1", "repo": "project/train", "base_commit": "a"},
        {"instance_id": "test-1", "repo": "project/test", "base_commit": "b"},
    ]
    split = {"projects": [
        {"project": "project/train", "split": "train", "tasks": [
            {"instance_id": "train-1", "base_commit": "a"},
        ]},
        {"project": "project/test", "split": "test_id", "tasks": [
            {"instance_id": "test-1", "base_commit": "b"},
        ]},
    ]}
    assert frozen_prefix({"workloads": rows}, split, 1) == rows[:1]
    with pytest.raises(ValueError, match="outside frozen train"):
        frozen_prefix({"workloads": rows}, split, 2)
    with pytest.raises(ValueError, match="duplicate"):
        frozen_prefix({"workloads": [rows[0], rows[0]]}, split, 2)
    with pytest.raises(ValueError, match="prefix count"):
        frozen_prefix({"workloads": rows}, split, 3)
