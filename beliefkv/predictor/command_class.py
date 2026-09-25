"""Low-cardinality, privacy-preserving command categories for tool timing."""

from __future__ import annotations

import ast
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


def execute_command_shape(payload: Mapping[str, Any]) -> str:
    """Return coarse command structure without retaining source or paths."""
    category = execute_command_class(payload)
    if category not in {"python_inline", "test_suite"}:
        return category
    command = payload.get("command")
    if not isinstance(command, str) or len(command) > 65_536:
        return category
    try:
        words = shlex.split(command)
    except ValueError:
        return category
    if category == "test_suite":
        if any(word in ("--help", "-h", "--version") for word in words):
            return "test_suite_metadata"
        runner = next((
            index for index, word in enumerate(words)
            if PurePosixPath(word).name in {
                "pytest", "py.test", "tox", "runtests.py", "nosetests"
            }
        ), None)
        if runner is None:
            return category
        targets = [
            word for word in words[runner + 1:]
            if not word.startswith("-") and word not in {
                "|", "&&", ";", "2>&1", ">", ">>", "head", "tail"
            }
            and not word.isdigit()
        ]
        return (
            "test_suite_many_targets" if len(targets) >= 3
            else "test_suite_targeted" if targets
            else "test_suite_full"
        )
    if "-c" not in words:
        return "python_inline_unparsed"
    position = words.index("-c")
    if position + 1 >= len(words):
        return "python_inline_unparsed"
    try:
        tree = ast.parse(words[position + 1])
    except (SyntaxError, ValueError):
        return "python_inline_unparsed"
    nodes = list(ast.walk(tree))
    modules = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            modules.update(
                alias.name.split(".", 1)[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".", 1)[0])
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in nodes if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    if "sleep" in calls:
        return "python_inline_wait"
    if "subprocess" in modules or "Popen" in calls:
        return "python_inline_subprocess"
    if (
        modules.intersection({"pytest", "unittest"})
        or any(
            isinstance(node, ast.ClassDef)
            and any(
                isinstance(base, ast.Name) and base.id == "TestCase"
                or isinstance(base, ast.Attribute) and base.attr == "TestCase"
                for base in node.bases
            )
            for node in nodes
        )
    ):
        return "python_inline_test"
    if "django" in modules and "setup" in calls:
        return "python_inline_framework"
    return "python_inline_complex" if len(nodes) >= 100 else "python_inline_simple"
