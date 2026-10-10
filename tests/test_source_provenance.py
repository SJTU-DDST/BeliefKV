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


def test_patch_validation_preserves_staged_additions_and_the_real_index(tmp_path):
    root = _repository(tmp_path)
    (root / "new_runtime.py").write_text("value = 2\n")
    _git(root, "add", "new_runtime.py")
    (root / "new_runtime.py").write_text("value = 3\n")
    (root / "runtime.py").write_text("value = 2\n")
    patch = tmp_path / "runtime.patch"
    patch.write_bytes(subprocess.check_output(("git", "diff", "HEAD"), cwd=root))
    index = root / ".git/index"
    original_index = index.read_bytes()

    assert git_source_state(root, canonical_patch=patch)["canonical_patch"]["matches_worktree"]
    assert index.read_bytes() == original_index


@pytest.mark.parametrize("extra_source", (False, True))
def test_patch_validation_supports_worktrees_and_declared_untracked_additions(tmp_path, extra_source):
    repository = _repository(tmp_path)
    root = tmp_path / "isolated"
    _git(repository, "worktree", "add", "--detach", str(root), "HEAD")
    (root / "runtime.py").write_text("value = 2\n")
    (root / "new_runtime.py").write_text("value = 3\n")
    _git(root, "add", "new_runtime.py")
    patch = tmp_path / "runtime.patch"
    patch.write_bytes(subprocess.check_output(("git", "diff", "HEAD"), cwd=root))
    _git(root, "restore", "--staged", "new_runtime.py")
    index = Path(subprocess.check_output(
        ("git", "rev-parse", "--git-path", "index"), cwd=root, text=True,
    ).strip())
    original_index = index.read_bytes()

    if extra_source:
        (root / "extra.py").write_text("value = 4\n")
        with pytest.raises(RuntimeError, match="canonical patch"):
            git_source_state(root, canonical_patch=patch)
    else:
        state = git_source_state(root, canonical_patch=patch)
        assert state["canonical_patch"]["matches_worktree"]
        assert state["canonical_patch"]["covered_untracked_paths"] == ["new_runtime.py"]
    assert index.read_bytes() == original_index
