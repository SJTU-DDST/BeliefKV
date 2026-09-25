"""Low-cardinality, privacy-preserving command categories for tool timing."""

from __future__ import annotations

from pathlib import PurePosixPath
import shlex
from typing import Mapping, Any


def execute_command_class(payload: Mapping[str, Any]) -> str:
    command = payload.get("command")
    if not isinstance(command, str) or not command or len(command) > 65_536:
        return "execute"
    try:
        words = shlex.split(command)
    except ValueError:
        return "unparsed"
    for position, word in enumerate(words):
        name = PurePosixPath(word).name
        if name in {"pytest", "py.test", "tox", "runtests.py", "nosetests"}:
            return "test_suite"
        if name.startswith("python") or name in {"uv", "poetry"}:
            suffix = words[position + 1:]
            if (
                "pytest" in suffix or "unittest" in suffix
                or any(PurePosixPath(item).name == "runtests.py"
                       for item in suffix)
            ):
                return "test_suite"
            if "-c" in suffix or "-" in suffix:
                return "python_inline"
            if any(item.endswith(".py") for item in suffix):
                return "python_script"
        if name == "git":
            return "git"
    return "other"
