"""Focused navigation for checkpoints, retaining real native filesystem tools."""

from functools import wraps

from deepagents.middleware.filesystem import ReadFileSchema
from pydantic import Field


FOCUSED_TOOL_PROFILE = "focused_checkpoint_v1"
FOCUSED_READ_LINES = 400
FOCUSED_TOOL_DESCRIPTIONS = {
    "ls": (
        "List a directory when its contents are unknown. A supplied file path can "
        "be read directly; do not list each parent directory before reading it."
    ),
    "read_file": (
        "Read a real workspace file with source line numbers. Known file paths "
        "can be read directly without ls. By default read at most 400 source lines; "
        "use offset (zero-based) and limit for a known symbol range or a larger file. "
        "Read only the missing evidence, and avoid overlapping repeated pages."
    ),
    "glob": (
        "Find files with shell glob syntax, such as **/*.py. An absolute "
        "/workspace/... pattern is anchored at the mounted repository root. "
        "Relative patterns use the optional path directory. Use supplied exact "
        "paths directly instead of searching again for an already known file."
    ),
    "grep": (
        "Search for an exact literal substring in workspace files. pattern is "
        "not a regular expression: do not use regex alternation or escaping. "
        "Narrow path or glob when possible. For actual regex or structured "
        "cross-file analysis, use a focused execute command."
    ),
    "execute": (
        "Run a real focused shell command in /workspace using the pinned project "
        "environment. Use it for project tests, small Python or AST inspections, "
        "or necessary command-line searches. Do not install dependencies, access "
        "the network, add sleeps, or rerun completed work. Report the actual outcome."
    ),
}


class FocusedReadFileSchema(ReadFileSchema):
    limit: int = Field(
        default=FOCUSED_READ_LINES,
        description="Maximum source lines to read; use a focused range when known.",
    )


def configure_focused_read_tool(tool):
    tool.args_schema = FocusedReadFileSchema
    original, original_async = tool.func, tool.coroutine

    @wraps(original)
    def read(*args, **kwargs):
        kwargs.setdefault("limit", FOCUSED_READ_LINES)
        return original(*args, **kwargs)

    @wraps(original_async)
    async def aread(*args, **kwargs):
        kwargs.setdefault("limit", FOCUSED_READ_LINES)
        return await original_async(*args, **kwargs)

    tool.func, tool.coroutine = read, aread
