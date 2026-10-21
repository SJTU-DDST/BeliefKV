from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Iterable


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(path: Path, *args: str, input_text: str | None = None) -> str:
    return subprocess.check_output(
        ("git", *args),
        cwd=path,
        text=True,
        input=input_text,
        stderr=subprocess.STDOUT,
    ).strip()


def _paths(payload: str) -> list[str]:
    return sorted(line for line in payload.splitlines() if line)


def _tree_fingerprint(root: Path, paths: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(paths):
        source = root / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if source.is_file() or source.is_symlink():
            digest.update(sha256_file(source).encode("ascii"))
        else:
            digest.update(b"<absent>")
        digest.update(b"\0")
    return digest.hexdigest()


def _tree_with_patch(
    repository: Path, patch: Path | None, *, extra_paths: Iterable[str] = (),
) -> str:
    with tempfile.TemporaryDirectory(prefix="beliefkv-provenance-") as directory:
        temporary_index = Path(directory) / "index"
        environment = {"GIT_INDEX_FILE": str(temporary_index)}
        if patch is None:
            index = Path(_git(repository, "rev-parse", "--git-path", "index"))
            if not index.is_absolute():
                index = repository / index
            # Keep staged entries and the timestamp Git uses for racy-content checks.
            shutil.copy2(index, temporary_index)
            subprocess.run(
                ("git", "add", "-u"),
                cwd=repository,
                env={**__import__("os").environ, **environment},
                check=True,
                capture_output=True,
            )
            if extra_paths:
                subprocess.run(
                    ("git", "add", "--", *extra_paths),
                    cwd=repository,
                    env={**__import__("os").environ, **environment},
                    check=True,
                    capture_output=True,
                )
        else:
            subprocess.run(
                ("git", "read-tree", _git(repository, "rev-parse", "HEAD^{tree}")),
                cwd=repository,
                env={**__import__("os").environ, **environment},
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ("git", "apply", "--cached", str(patch)),
                cwd=repository,
                env={**__import__("os").environ, **environment},
                check=True,
                capture_output=True,
            )
        return subprocess.check_output(
            ("git", "write-tree"),
            cwd=repository,
            env={**__import__("os").environ, **environment},
            text=True,
        ).strip()


def git_source_state(
    root: Path,
    *,
    exclude_paths: Iterable[str] = (),
    canonical_patch: Path | None = None,
) -> dict[str, object]:
    repository = root.expanduser().resolve()
    excluded = set(exclude_paths)
    tracked = [
        item
        for item in _paths(_git(repository, "diff", "--name-only", "HEAD", "--"))
        if item not in excluded
    ]
    untracked = [
        item
        for item in _paths(
            _git(repository, "ls-files", "--others", "--exclude-standard")
        )
        if item not in excluded
    ]
    state: dict[str, object] = {
        "commit": _git(repository, "rev-parse", "HEAD"),
        "dirty": bool(tracked or untracked),
        "excluded_provenance_paths": sorted(excluded),
        "tracked_change_paths": tracked,
        "untracked_paths": untracked,
        "tracked_change_tree_sha256": _tree_fingerprint(repository, tracked),
    }
    if canonical_patch is not None:
        patch = canonical_patch.expanduser().resolve()
        expected_tree = _tree_with_patch(repository, patch)
        added = set(_paths(_git(
            repository, "diff", "--name-only", "--diff-filter=A", "HEAD", expected_tree,
        )))
        covered_untracked = [path for path in untracked if path in added]
        actual_tree = _tree_with_patch(repository, None, extra_paths=covered_untracked)
        patch_matches = expected_tree == actual_tree and not (set(untracked) - added)
        state["canonical_patch"] = {
            "path": str(patch),
            "sha256": sha256_file(patch),
            "expected_tree": expected_tree,
            "actual_tree": actual_tree,
            "matches_worktree": patch_matches,
            "covered_untracked_paths": covered_untracked,
        }
        if not patch_matches:
            raise RuntimeError(
                "SGLang worktree does not exactly match the canonical patch"
            )
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    state["state_sha256"] = hashlib.sha256(encoded).hexdigest()
    return state
