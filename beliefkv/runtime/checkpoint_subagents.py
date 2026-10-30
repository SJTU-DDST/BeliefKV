"""Explicit bounded task inputs, preserving the native task tool's callbacks."""

from functools import wraps
from pathlib import PurePosixPath
from typing import Any

from deepagents.middleware.subagents import TaskToolSchema
from pydantic import Field, field_validator, model_validator

from beliefkv.runtime.subagent_state import PrivateStateIsolatingSubAgentMiddleware


def _workspace_path(value: str) -> str:
    if value.startswith("/workspace/"):
        value = value[len("/workspace/"):]
    path = PurePosixPath(value)
    if (
        not value.strip() or path.is_absolute() or "\\" in value
        or any(part in ("..", ".git") for part in path.parts) or not path.parts
    ):
        raise ValueError("checkpoint paths must name a location inside /workspace")
    return str(path)


class CheckpointTaskSchema(TaskToolSchema):
    description: str = Field(
        min_length=1,
        description="One concrete question or bounded supporting edit, not a complete project phase.",
    )
    known_paths: list[str] = Field(
        min_length=1, max_length=6,
        description="Known workspace paths relevant to this checkpoint, including intended new files.",
    )
    existing_evidence: str = Field(
        max_length=4000,
        description="Short observed evidence or source excerpts; say what is unknown instead of inventing evidence.",
    )
    finish_condition: str = Field(
        min_length=1,
        description="The specific evidence or bounded result that lets this child return to its parent.",
    )
    write_paths: list[str] = Field(
        max_length=2,
        description="Explicit supporting edit scope, at most two files; [] means read-only. Root owns larger changes.",
    )

    @field_validator("description", "finish_condition")
    @classmethod
    def nonempty_text(cls, value):
        if not value.strip():
            raise ValueError("checkpoint question and finish condition must be explicit")
        return value

    @field_validator("known_paths", "write_paths")
    @classmethod
    def normalize_paths(cls, values):
        result = [_workspace_path(value) for value in values]
        if len(set(result)) != len(result):
            raise ValueError("checkpoint paths must be distinct")
        return result

    @model_validator(mode="after")
    def check_write_scope(self):
        if not set(self.write_paths) <= set(self.known_paths):
            raise ValueError("include each intended write path among the checkpoint's known paths")
        return self

    def child_description(self):
        paths = "\n".join("- /workspace/" + path for path in self.known_paths)
        writes = ", ".join("/workspace/" + path for path in self.write_paths) or "read-only"
        return (
            f"Question:\n{self.description}\n\nKnown paths:\n{paths}"
            f"\n\nExisting evidence:\n{self.existing_evidence or 'None provided; establish only this checkpoint.'}"
            f"\n\nFinish condition:\n{self.finish_condition}\n\nWrite scope:\n{writes}"
            "\n\nReturn this bounded evidence or an honest PARTIAL:/BLOCKED: handoff. "
            "New independent work and the complete project objective remain with the parent."
        )


class ReadOnlyCheckpointTaskSchema(CheckpointTaskSchema):
    write_paths: list[str] = Field(
        max_length=0,
        description="This workflow requests read-only evidence. Supply [].",
    )


def checkpoint_schema(*, read_only=False):
    return ReadOnlyCheckpointTaskSchema if read_only else CheckpointTaskSchema


def _child_kwargs(kwargs, schema):
    remaining = dict(kwargs)
    supplied = {
        name: remaining.pop(name) for name in schema.model_fields if name in remaining
    }
    assignment = schema.model_validate(supplied)
    return {
        **remaining, "description": assignment.child_description(),
        "subagent_type": assignment.subagent_type,
    }


class CheckpointSubAgentMiddleware(PrivateStateIsolatingSubAgentMiddleware):
    """Keep one native task invocation per call; change its authoring schema only."""

    def __init__(self, *, read_only=False, **kwargs):
        self._checkpoint_schema = checkpoint_schema(read_only=read_only)
        super().__init__(**kwargs)

    def _isolate_current_tool(self):
        super()._isolate_current_tool()
        for tool in self.tools:
            schema = self._checkpoint_schema
            tool.args_schema = schema
            func, coroutine = getattr(tool, "func", None), getattr(tool, "coroutine", None)
            if func is not None:
                @wraps(func)
                def checkpoint_func(*args: Any, _func=func, **kwargs: Any):
                    return _func(*args, **_child_kwargs(kwargs, schema))
                tool.func = checkpoint_func
            if coroutine is not None:
                @wraps(coroutine)
                async def checkpoint_coroutine(*args: Any, _coroutine=coroutine, **kwargs: Any):
                    return await _coroutine(*args, **_child_kwargs(kwargs, schema))
                tool.coroutine = checkpoint_coroutine
