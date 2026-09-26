import subprocess

import pytest

from beliefkv.experiments.swebench_source import verify_workload_source_objects


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()


def test_source_preflight_catches_missing_blob_even_when_commit_exists(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README").write_text("content")
    _git(repo, "add", "README")
    _git(repo, "commit", "-qm", "source")
    commit = _git(repo, "rev-parse", "HEAD")
    blob = _git(repo, "rev-parse", "HEAD:README")
    verify_workload_source_objects([(repo, commit)])
    (repo / ".git" / "objects" / blob[:2] / blob[2:]).unlink()
    assert _git(repo, "cat-file", "-t", commit) == "commit"
    with pytest.raises(RuntimeError, match="lacks 1 checkout blobs"):
        verify_workload_source_objects([(repo, commit)])
