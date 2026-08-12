from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from beliefkv.experiments.source_provenance import git_source_state, sha256_file


def _git(root: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=root, check=True, capture_output=True)


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "BeliefKV Test")
    _git(root, "config", "user.email", "beliefkv@example.com")
    (root / "runtime.py").write_text("value = 1\n", encoding="utf-8")
    _git(root, "add", "runtime.py")
    _git(root, "commit", "-qm", "base")
    return root


def test_git_source_state_validates_canonical_patch(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "runtime.py").write_text("value = 2\n", encoding="utf-8")
    patch = tmp_path / "runtime.patch"
    patch.write_bytes(subprocess.check_output(("git", "diff"), cwd=root))

    state = git_source_state(root, canonical_patch=patch)

    assert state["dirty"] is True
    assert state["tracked_change_paths"] == ["runtime.py"]
    assert state["canonical_patch"]["sha256"] == sha256_file(patch)
    assert state["canonical_patch"]["matches_worktree"] is True
    assert len(str(state["state_sha256"])) == 64


def test_git_source_state_rejects_extra_runtime_change(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "runtime.py").write_text("value = 2\n", encoding="utf-8")
    patch = tmp_path / "runtime.patch"
    patch.write_bytes(subprocess.check_output(("git", "diff"), cwd=root))
    (root / "runtime.py").write_text("value = 3\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="canonical patch"):
        git_source_state(root, canonical_patch=patch)


def test_git_source_state_can_exclude_generated_manifest(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    generated = root / "environment.json"
    generated.write_text("{}\n", encoding="utf-8")

    state = git_source_state(root, exclude_paths=("environment.json",))

    assert state["dirty"] is False
    assert state["excluded_provenance_paths"] == ["environment.json"]
