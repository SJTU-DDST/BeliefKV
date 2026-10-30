"""Explicit bounded task inputs, preserving the native task tool's callbacks."""

import asyncio
from dataclasses import replace
from functools import wraps
import json
from pathlib import PurePosixPath
from typing import Any

from deepagents.middleware.subagents import TaskToolSchema
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from pydantic import BaseModel, Field, field_validator, model_validator

from beliefkv.runtime.subagent_state import PrivateStateIsolatingSubAgentMiddleware


PARALLEL_TASK_TOOL = "parallel_tasks"
CHECKPOINT_MEMBER_TOOL = "checkpoint_member"


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


class ParallelCheckpointSchema(BaseModel):
    assignments: list[CheckpointTaskSchema] = Field(
        min_length=2, max_length=4,
        description="Two to four independent checkpoints to run concurrently and join together.",
    )

    @model_validator(mode="after")
    def independent_scopes(self):
        writes, identities = set(), set()
        for assignment in self.assignments:
            identity = (
                assignment.subagent_type, assignment.description,
                tuple(assignment.known_paths), assignment.finish_condition,
            )
            if identity in identities:
                raise ValueError("parallel checkpoints must not duplicate the same assignment")
            identities.add(identity)
            if writes & set(assignment.write_paths):
                raise ValueError("parallel checkpoint writers must have disjoint files")
            writes.update(assignment.write_paths)
        return self


class ReadOnlyParallelCheckpointSchema(ParallelCheckpointSchema):
    assignments: list[ReadOnlyCheckpointTaskSchema] = Field(
        min_length=2, max_length=4,
        description="Two to four independent read-only checkpoints to run concurrently.",
    )


def parallel_checkpoint_schema(*, read_only=False):
    return ReadOnlyParallelCheckpointSchema if read_only else ParallelCheckpointSchema


def checkpoint_member_call_id(group_call_id, index):
    if not isinstance(group_call_id, str) or not group_call_id:
        raise ValueError("parallel checkpoint needs its actual tool call identity")
    return f"{group_call_id}:member:{index}"


def _member_calls(assignments, runtime):
    return [{
        "name": CHECKPOINT_MEMBER_TOOL, "type": "tool_call",
        "id": checkpoint_member_call_id(runtime.tool_call_id, index),
        "args": {
            **assignment.model_dump(),
            "runtime": replace(
                runtime, tool_call_id=checkpoint_member_call_id(runtime.tool_call_id, index),
            ),
        },
    } for index, assignment in enumerate(assignments)]


def _parallel_reports(assignments, results, tool_call_id):
    reports, files = [], {}
    has_files = False
    for assignment, result in zip(assignments, results, strict=True):
        if isinstance(result, BaseException):
            reports.append({
                "question": assignment.description, "status": "error",
                "report": f"{type(result).__name__}: {result}",
            })
            continue
        if not isinstance(result, Command) or not isinstance(result.update, dict):
            raise ValueError("checkpoint member did not return its native report command")
        if set(result.update) - {"messages", "files"}:
            raise ValueError("parallel checkpoint returned an unsupported shared parent state update")
        if "files" in result.update:
            # Keep deletion markers for the parent's native filesystem reducer.
            files.update(result.update["files"])
            has_files = True
        messages = result.update["messages"]
        reports.append({
            "question": assignment.description, "status": "returned",
            "report": "\n".join(message.text for message in messages),
        })
    message = ToolMessage(
        content=json.dumps({"checkpoint_reports": reports}, ensure_ascii=True),
        tool_call_id=tool_call_id, name=PARALLEL_TASK_TOOL,
        status="error" if any(row["status"] == "error" for row in reports) else "success",
    )
    return Command(update={
        "messages": [message], **({"files": files} if has_files else {}),
    })


def _parallel_tool(member, schema, roles):
    def role_error(selected, runtime):
        invalid = sorted({item.subagent_type for item in selected} - set(roles))
        if not invalid:
            return None
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({
                "checkpoint_group_error": "Unsupported subagent roles",
                "unsupported_roles": invalid, "allowed_roles": list(roles),
            }),
            tool_call_id=runtime.tool_call_id, name=PARALLEL_TASK_TOOL, status="error",
        )]})

    def parallel_tasks(assignments: list, runtime: ToolRuntime) -> Command:
        selected = schema.model_validate({"assignments": assignments}).assignments
        error = role_error(selected, runtime)
        if error is not None:
            return error
        results = RunnableLambda(member.invoke).batch(
            _member_calls(selected, runtime),
            config={"max_concurrency": len(selected)}, return_exceptions=True,
        )
        return _parallel_reports(selected, results, runtime.tool_call_id)

    async def aparallel_tasks(assignments: list, runtime: ToolRuntime) -> Command:
        selected = schema.model_validate({"assignments": assignments}).assignments
        error = role_error(selected, runtime)
        if error is not None:
            return error
        results = await asyncio.gather(*(
            member.ainvoke(call) for call in _member_calls(selected, runtime)
        ), return_exceptions=True)
        return _parallel_reports(selected, results, runtime.tool_call_id)

    return StructuredTool.from_function(
        name=PARALLEL_TASK_TOOL, func=parallel_tasks, coroutine=aparallel_tasks,
        infer_schema=False, args_schema=schema,
        description=(
            "Run two to four independent bounded checkpoints concurrently and wait "
            "for all reports. Supply each checkpoint's question, known paths, evidence, "
            "finish condition, role and disjoint write scope in assignments. Prefer "
            "this tool when independent questions are ready together, including "
            "later rounds. Use task for an indivisible single checkpoint. Choose "
            "the actual questions, grouping and timing; do not repeat completed work. "
            "Allowed child roles: " + ", ".join(roles) + "."
        ),
    )


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
    """Expose explicit single/group inputs backed by native task invocations."""

    def __init__(self, *, read_only=False, **kwargs):
        self._checkpoint_schema = checkpoint_schema(read_only=read_only)
        self._parallel_schema = parallel_checkpoint_schema(read_only=read_only)
        self._checkpoint_roles = tuple(spec["name"] for spec in kwargs["subagents"])
        super().__init__(**kwargs)

    def _isolate_current_tool(self):
        self.tools = [tool for tool in self.tools if tool.name != PARALLEL_TASK_TOOL]
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
        member = self.tools[0].model_copy(update={"name": CHECKPOINT_MEMBER_TOOL})
        self.tools.append(_parallel_tool(member, self._parallel_schema, self._checkpoint_roles))
