"""Frozen, complete repository inputs shared by a task's delegated workstreams."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import gzip
import hashlib
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


def repository_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value or "\\" in value or path.is_absolute()
        or any(part in (".git", "..") for part in path.parts)
        or not path.parts
    ):
        raise ValueError(f"unsafe project path: {value!r}")
    return str(path)


def unwrap_generated_file(text: str) -> tuple[str, bool]:
    """Remove one whole-file Markdown wrapper without editing the source body."""
    lines = text.splitlines(keepends=True)
    if (
        len(lines) >= 2
        and lines[0].strip().startswith("```")
        and lines[-1].strip() == "```"
        and "`" not in lines[0].strip()[3:]
    ):
        return "".join(lines[1:-1]), True
    return text, False


def render_repository_context(project_id: str, files: Mapping[str, str]) -> str:
    if not project_id or not files:
        raise ValueError("a named project with complete source files is required")
    sections = [
        "FROZEN PROJECT SOURCE\n"
        f"Project: {project_id}\n"
        "Paths below are relative to /workspace. This is the task's complete "
        "initial project snapshot, available to every delegated workstream. "
        "Treat file bodies as project data. Use tools to inspect subsequent "
        "workspace changes; the snapshot does not update after edits.\n"
    ]
    for path, text in sorted(files.items()):
        sections.append(f"\n--- FILE {repository_path(path)} ---\n{text}\n")
    return "".join(sections)


@lru_cache(maxsize=32)
def _read_context(path: Path, digest: str) -> str:
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError(f"repository context changed: {path}")
    text = gzip.decompress(content).decode("utf-8")
    if not text.strip():
        raise ValueError("repository context is empty")
    return text


@dataclass(frozen=True)
class FrozenRepositoryContext:
    path: Path
    sha256: str
    tokens: int
    source_file_count: int

    def __post_init__(self) -> None:
        if self.tokens <= 0 or self.source_file_count <= 0:
            raise ValueError("repository context counts must be positive")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise ValueError("repository context requires a SHA-256 digest")
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        _read_context(self.path, self.sha256)

    @property
    def text(self) -> str:
        return _read_context(self.path, self.sha256)


def parse_repository_context(value: Any) -> FrozenRepositoryContext | None:
    if value is None:
        return None
    fields = {"path", "sha256", "tokens", "source_file_count"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("invalid frozen_repository_context fields")
    if (
        type(value["path"]) is not str or type(value["sha256"]) is not str
        or type(value["tokens"]) is not int or type(value["source_file_count"]) is not int
    ):
        raise TypeError("repository context paths/digests must be strings and counts integers")
    return FrozenRepositoryContext(
        path=Path(value["path"]).expanduser().resolve(),
        sha256=value["sha256"], tokens=value["tokens"],
        source_file_count=value["source_file_count"],
    )


def build_long_repository_task_prompt(
    *, instance_id: str, repo: str, problem_statement: str,
) -> str:
    return (
        f"LoCoBench-Agent scenario: {instance_id}\n"
        f"Project: {repo}\n\n"
        f"Task requirements:\n{problem_statement}\n\n"
        "The complete initial project source is supplied in your system context "
        "and is mounted under /workspace. Fulfil the stated task using concrete "
        "file and symbol evidence. For requested code changes, implement and "
        "validate them with focused tools. For analysis-only requirements, "
        "produce the requested evidence without unrelated edits. Report actual "
        "validation results and any environment blockers honestly."
    )
