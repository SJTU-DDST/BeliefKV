"""Check the workload's minimum root delegation at attempted final return."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Annotated, Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain.agents.middleware.types import (
    AgentState, ExtendedModelResponse, ModelResponse, PrivateStateAttr,
)
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.channels.untracked_value import UntrackedValue
from langgraph.types import Command
from typing_extensions import NotRequired

from beliefkv.experiments.delegation_policy import (
    MINIMUM_ROOT_JOIN_MEMBERS, ROOT_RETRY_TASK_FANOUT,
)


ROOT_DELEGATION_PRIVATE_KEYS = frozenset({
    "root_delegation_retry_pending", "root_delegation_required_task_attempts",
})
MAXIMUM_ROOT_DELEGATION_ATTEMPTS = 2


class RootDelegationState(AgentState[Any]):
    root_delegation_retry_pending: NotRequired[
        Annotated[bool, UntrackedValue, PrivateStateAttr]
    ]
    root_delegation_required_task_attempts: NotRequired[
        Annotated[int, UntrackedValue, PrivateStateAttr]
    ]


class RootDelegationRequirementMiddleware(AgentMiddleware[RootDelegationState, Any, Any]):
    """Require one real parallel root JOIN; let the model choose the work."""

    state_schema = RootDelegationState

    def __init__(self, *, adapter: Any, audit: Any):
        self.adapter = adapter
        self.audit = audit

    @staticmethod
    def _finalizing(state):
        return bool(
            state.get("guard_forcing_completion")
            or state.get("protocol_repair_active")
        )

    def _required_task_request(self, request):
        if (
            not request.state.get("root_delegation_retry_pending")
            or self._finalizing(request.state)
            or self.adapter.has_completed_join(minimum_members=MINIMUM_ROOT_JOIN_MEMBERS)
        ):
            return request, None
        attempt = int(request.state.get("root_delegation_required_task_attempts", 0)) + 1
        if attempt > MAXIMUM_ROOT_DELEGATION_ATTEMPTS:
            self.audit.emit(
                "root_delegation_requirement_failed", reason="required_task_attempts_exhausted",
                attempts=attempt - 1, successful_join_observed=False,
                minimum_completed_join_members=MINIMUM_ROOT_JOIN_MEMBERS,
            )
            raise RuntimeError("root could not complete its required model-selected delegation")
        if sum(getattr(tool, "name", None) == "task" for tool in request.tools) != 1:
            raise RuntimeError("root delegation requirement needs exactly one task tool")
        self.audit.emit(
            "root_delegation_required_task_attempt", attempt=attempt,
            model_selects_assignment=True,
            model_selected_fanout_bounds=list(ROOT_RETRY_TASK_FANOUT),
            minimum_completed_join_members=MINIMUM_ROOT_JOIN_MEMBERS,
        )
        settings = {
            **request.model_settings, "parallel_tool_calls": True,
            "extra_body": {
                **(request.model_settings.get("extra_body") or {}),
                "beliefkv_task_fanout": list(ROOT_RETRY_TASK_FANOUT),
            },
        }
        return request.override(tool_choice="task", model_settings=settings), attempt

    @staticmethod
    def _record_attempt(response, attempt):
        if attempt is None:
            return response
        extended = isinstance(response, ExtendedModelResponse)
        model_response = response.model_response if extended else response
        if isinstance(model_response, AIMessage):
            model_response = ModelResponse(result=[model_response])
        command = response.command if extended else None
        update = {"root_delegation_required_task_attempts": attempt}
        if command is not None:
            if command.update is not None and not isinstance(command.update, Mapping):
                raise ValueError("root delegation cannot merge a non-mapping model update")
            command = replace(command, update={**(command.update or {}), **update})
        else:
            command = Command(update=update)
        return ExtendedModelResponse(model_response=model_response, command=command)

    def wrap_model_call(self, request, handler):
        request, attempt = self._required_task_request(request)
        return self._record_attempt(handler(request), attempt)

    async def awrap_model_call(self, request, handler):
        request, attempt = self._required_task_request(request)
        return self._record_attempt(await handler(request), attempt)

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime):
        del runtime
        messages = state.get("messages", ())
        last = messages[-1] if messages else None
        if state.get("structured_response") is None and (
            not isinstance(last, AIMessage) or last.tool_calls or not last.text.strip()
        ):
            return None
        if self.adapter.has_completed_join(minimum_members=MINIMUM_ROOT_JOIN_MEMBERS):
            return None
        if self._finalizing(state):
            self.audit.emit(
                "root_delegation_requirement_failed", reason="safety_finalization",
                successful_join_observed=False,
                minimum_completed_join_members=MINIMUM_ROOT_JOIN_MEMBERS,
            )
            return None
        attempts = int(state.get("root_delegation_required_task_attempts", 0))
        if attempts >= MAXIMUM_ROOT_DELEGATION_ATTEMPTS:
            self.audit.emit(
                "root_delegation_requirement_failed", reason="required_task_attempts_exhausted",
                attempts=attempts, successful_join_observed=False,
                minimum_completed_join_members=MINIMUM_ROOT_JOIN_MEMBERS,
            )
            raise RuntimeError("root returned without completing its required delegation")
        self.audit.emit(
            "root_delegation_return_deferred", attempts=attempts,
            successful_join_observed=False, model_selects_assignment=True,
            minimum_completed_join_members=MINIMUM_ROOT_JOIN_MEMBERS,
        )
        return {
            "messages": [HumanMessage(content=(
                "The collaboration requirement is not yet satisfied: you must "
                "complete at least one parallel JOIN with at least two substantive "
                "task children. Separate one-child rounds do not satisfy this "
                "requirement. Choose at least two independent investigation, review, "
                "or validation deliverables and submit their task calls together "
                "in ONE assistant response, before waiting for either report. "
                "If you already have a candidate result, choose distinct properties "
                "to review or validate. Supply relevant paths, evidence, and disjoint "
                "write scopes. Integrate all reports before finalizing. Choose the "
                "assignments and specialists yourself; do not repeat completed work "
                "or claim a failed delegation succeeded."
            ))],
            "structured_response": None,
            "root_delegation_retry_pending": True,
            "jump_to": "model",
        }

    async def aafter_model(self, state, runtime):
        return self.after_model(state, runtime)
