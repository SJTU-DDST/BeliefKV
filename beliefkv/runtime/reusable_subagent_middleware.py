"""DeepAgents task API with exact private-history continuation."""

from __future__ import annotations

import json
from typing import Any

from deepagents.middleware.subagents import (
    SubAgentMiddleware, _subagent_tracing_context, create_sub_agent,
)
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from pydantic import BaseModel, Field

from beliefkv.runtime.reusable_subagents import SubagentSessionError


REUSABLE_DELEGATION_PROMPT = """
Delegate independent work with task. Choose parallel workstreams from the task
evidence. After reviewing results, continue delegation when implementation,
testing or new evidence calls for it. For a related follow-up, pass the
specialist's returned agent_id to task, with the same subagent_type and the new
assignment; that specialist retains its own private conversation. Create a new
agent for a new responsibility or an independent review. A handle accepts only
one active task at a time. Use close_agent when a specialist is no longer useful.
Finishing one delegated assignment does not close that specialist. After edits,
integration or tests expose another question in its scope, consider returning
the new evidence to that handle instead of restarting the investigation. Keep
independent judgment in a separate specialist. Reassess these choices after
each report rather than treating the first diagnostic round as the whole task.
Choose rounds and nesting from actual progress; no fixed DAG or round count is
required. Only the root receives the full initial project snapshot. Delegate
focused evidence and let specialists inspect relevant files in /workspace.
"""

REUSABLE_RETURN_GUIDANCE = (
    "\n\nThis specialist is now idle and retains its private conversation. "
    "For related follow-up after integration or new test evidence, task may "
    "reuse this agent_id with the same subagent_type. Choose a new specialist "
    "for independent work; close this handle when no longer needed."
)


class ReusableTaskSchema(BaseModel):
    description: str = Field(description="Focused assignment or follow-up with new evidence.")
    subagent_type: str = Field(description="An available specialist type.")
    agent_id: str | None = Field(default=None, description="Returned handle to resume; omit for a new specialist.")


class CloseAgentSchema(BaseModel):
    agent_id: str


class ReusableSubAgentMiddleware(SubAgentMiddleware):
    """Reuse native graph construction; keep activation state out of the parent."""

    def __init__(self, *, adapter: Any, **kwargs: Any) -> None:
        self._adapter = adapter
        specs = kwargs.pop("subagents")
        self._graphs = {
            spec["name"]: (
                spec["runnable"] if "runnable" in spec else create_sub_agent(
                    spec, state_schema=kwargs.get("state_schema"),
                )
            ).with_config({
                "metadata": {"lc_agent_name": spec["name"]},
                "run_name": spec["name"],
            })
            for spec in specs
        }
        super().__init__(
            **kwargs, system_prompt=REUSABLE_DELEGATION_PROMPT,
            subagents=[{
                "name": spec["name"], "description": spec["description"],
                "runnable": self._graphs[spec["name"]],
            } for spec in specs],
        )
        self._configured_delegation_description = (
            self.tools[0].description if self._task_description is not None else None
        )
        self._build_reusable_tools()

    @property
    def private_state_keys(self) -> frozenset[str]:
        return self._private_state_keys

    @private_state_keys.setter
    def private_state_keys(self, value: frozenset[str]) -> None:
        self._private_state_keys = value
        if self._graphs:
            self._build_reusable_tools()

    @staticmethod
    def _report(result: dict) -> str:
        structured = result.get("structured_response")
        if structured is not None:
            return (
                structured.model_dump_json() if hasattr(structured, "model_dump_json")
                else json.dumps(structured, default=str)
            )
        return next((
            message.text.rstrip() for message in reversed(result.get("messages", ()))
            if isinstance(message, AIMessage) and message.text
        ), "")

    def _build_reusable_tools(self) -> None:
        registry = self._adapter.reusable_subagents
        assert registry is not None

        def prepare(description, subagent_type, agent_id, runtime):
            if subagent_type not in self._graphs or not runtime.tool_call_id:
                raise SubagentSessionError("unknown specialist or missing task call identity")
            activation, state = registry.claim(
                runtime.tool_call_id, role=subagent_type,
                description=description, agent_id=agent_id,
            )
            state["messages"].append(HumanMessage(content=description))
            return activation, state

        def commit(activation, result, runtime):
            report = self._report(result)
            registry.complete(activation.call_id, result, report)
            return Command(update={"messages": [ToolMessage(
                f"agent_id: {activation.handle}\nsubagent_type: {activation.role}\n\n"
                f"{report}{REUSABLE_RETURN_GUIDANCE}",
                tool_call_id=runtime.tool_call_id,
            )]})

        def task(
            description: str, subagent_type: str, runtime: ToolRuntime,
            agent_id: str | None = None,
        ) -> str | Command:
            try:
                activation, state = prepare(description, subagent_type, agent_id, runtime)
            except SubagentSessionError as error:
                return f"Task rejected: {error}"
            try:
                with _subagent_tracing_context():
                    result = self._graphs[subagent_type].invoke(state, {
                        "configurable": {"ls_agent_type": "subagent"},
                        "metadata": self._adapter.invocation_scope_for_call(activation.call_id),
                    })
                return commit(activation, result, runtime)
            except BaseException:
                registry.abort(activation.call_id)
                raise

        async def atask(
            description: str, subagent_type: str, runtime: ToolRuntime,
            agent_id: str | None = None,
        ) -> str | Command:
            try:
                activation, state = prepare(description, subagent_type, agent_id, runtime)
            except SubagentSessionError as error:
                return f"Task rejected: {error}"
            try:
                with _subagent_tracing_context():
                    result = await self._graphs[subagent_type].ainvoke(state, {
                        "configurable": {"ls_agent_type": "subagent"},
                        "metadata": self._adapter.invocation_scope_for_call(activation.call_id),
                    })
                return commit(activation, result, runtime)
            except BaseException:
                registry.abort(activation.call_id)
                raise

        def close_agent(agent_id: str, runtime: ToolRuntime) -> str:
            try:
                self._adapter.close_reusable_subagent(agent_id, runtime.config)
            except SubagentSessionError as error:
                return f"Close rejected: {error}"
            return f"Closed agent_id: {agent_id}"

        self.tools = [
            StructuredTool.from_function(
                task, coroutine=atask, name="task", infer_schema=False,
                args_schema=ReusableTaskSchema,
                description=(
                    (
                        self._configured_delegation_description + "\n\n"
                        if self._configured_delegation_description else
                        "Available specialist types:\n"
                        + "\n".join(f"- {s['name']}: {s['description']}" for s in self._subagents)
                        + "\n\n"
                    )
                    + "Reusable specialist sessions:\n"
                    "Delegate to a new specialist or resume its returned agent_id. "
                    "Each activation joins independently. Omit agent_id for a new "
                    "specialist; pass the returned agent_id with the same "
                    "subagent_type for a related follow-up. Choose whether to "
                    "delegate or resume from the task and current evidence."
                ),
            ),
            StructuredTool.from_function(
                close_agent, name="close_agent", args_schema=CloseAgentSchema,
                infer_schema=False, description="Close an idle specialist no longer needed.",
            ),
        ]
