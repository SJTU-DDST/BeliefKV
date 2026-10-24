"""Invocation-local soft work budgets without interrupting model or tool output."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Annotated, Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    AgentState, ExtendedModelResponse, ModelResponse, PrivateStateAttr,
)
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.channels.untracked_value import UntrackedValue
from langgraph.types import Command
from typing_extensions import NotRequired


CHILD_WORK_BUDGET_PRIVATE_KEYS = frozenset({
    "child_work_model_calls", "child_work_notice",
})


class ChildWorkBudgetState(AgentState[Any]):
    child_work_model_calls: NotRequired[
        Annotated[int, UntrackedValue, PrivateStateAttr]
    ]
    child_work_notice: NotRequired[
        Annotated[tuple[int, str] | None, UntrackedValue, PrivateStateAttr]
    ]


def budget_instruction(warning_calls: int, target_calls: int) -> str:
    return f"""
BOUNDED WORK SESSION
Deliver one specific, verifiable result rather than solving the parent's whole task.
Plan to return your report within about {target_calls} model turns. A reminder near
turn {warning_calls} is a signal to narrow remaining work and organize the evidence.
This is a soft work budget: complete an in-progress command, outstanding delegated
JOIN, and necessary focused validation before reporting. Do not abandon an active
edit or claim unfinished work succeeded. Return promptly once the assigned question
is answered. If substantive work remains, begin the report with PARTIAL: or BLOCKED:,
state what is supported, list the exact remaining question and next useful action,
and let the parent integrate the result and decide the next delegation.
"""


class ChildWorkBudgetMiddleware(AgentMiddleware[ChildWorkBudgetState, Any, Any]):
    """Count completed model turns in existing nodes; append at most two notices."""

    state_schema = ChildWorkBudgetState
    model_calls_key = "child_work_model_calls"
    notice_key = "child_work_notice"
    audit_prefix = "child_work_budget"

    def __init__(self, *, warning_calls: int, target_calls: int, audit: Any, scope: str):
        if (
            type(warning_calls) is not int or type(target_calls) is not int
            or not 0 < warning_calls < target_calls
        ):
            raise ValueError("child work budget requires 0 < warning < target")
        self.warning_calls = warning_calls
        self.target_calls = target_calls
        self.audit = audit
        self.scope = scope

    def _model_call_progress(self, request):
        return int(request.state.get(self.model_calls_key, 0)) + 1, {}

    def _record_return(self, count, message, request):
        text = message.text.lstrip().casefold()
        self.audit.emit(
            self.audit_prefix + "_return", scope=self.scope, model_calls=count,
            declared_status=(
                "partial" if text.startswith("partial:") else
                "blocked" if text.startswith("blocked:") else "unspecified"
            ),
            forced=False,
        )

    def _record_response(self, request, response):
        extended = isinstance(response, ExtendedModelResponse)
        model_response = response.model_response if extended else response
        if isinstance(model_response, AIMessage):
            model_response = ModelResponse(result=[model_response])
        count, extra_update = self._model_call_progress(request)
        last = model_response.result[-1]
        calls = last.tool_calls if isinstance(last, AIMessage) else []
        notice = None
        if calls and count in (self.warning_calls, self.target_calls):
            notice = count, calls[-1]["id"]
        if not calls and count >= self.warning_calls and isinstance(last, AIMessage):
            self._record_return(count, last, request)
        update = {
            **extra_update,
            self.model_calls_key: count,
            self.notice_key: notice,
        }
        command = response.command if extended else None
        if command is not None:
            if command.update is not None and not isinstance(command.update, Mapping):
                raise ValueError("child budget cannot merge a non-mapping model update")
            command = replace(command, update={**(command.update or {}), **update})
        else:
            command = Command(update=update)
        return ExtendedModelResponse(model_response=model_response, command=command)

    def wrap_model_call(self, request, handler):
        return self._record_response(request, handler(request))

    async def awrap_model_call(self, request, handler):
        return self._record_response(request, await handler(request))

    def _notice_instruction(self, count):
        return (
            f"\n\nDELEGATED WORK BUDGET: {count} model turns have completed. "
            + (
                f"Aim to return the assigned evidence by about turn {self.target_calls}. "
                "Narrow remaining work and prepare a concise report. "
                if count == self.warning_calls else
                "The soft work target has been reached. Finish pending command "
                "results, nested child reports, and necessary focused validation, "
                "then return at the next suitable model response. "
            )
            + "Do not start another broad investigation. Do not abandon active work "
            "or claim it is complete. If work remains, begin your final report with "
            "PARTIAL: or BLOCKED:, include observed evidence and the exact next "
            "action, and let the parent decide the follow-up. Available tools "
            "remain usable for genuine completion needs."
        )

    def _append_notice(self, request, result):
        notice = request.state.get(self.notice_key)
        if notice is None or request.tool_call.get("id") != notice[1]:
            return result
        count = notice[0]
        instruction = self._notice_instruction(count)
        if isinstance(result, ToolMessage):
            messages = [result]
        elif isinstance(result, Command) and isinstance(result.update, Mapping):
            messages = result.update.get("messages", [])
        else:
            return result
        changed = False
        updated = []
        for message in messages:
            if isinstance(message, ToolMessage) and message.tool_call_id == notice[1]:
                content = message.content
                content = (
                    content + instruction if isinstance(content, str)
                    else [*content, {"type": "text", "text": instruction}]
                )
                message = message.model_copy(update={"content": content})
                changed = True
            updated.append(message)
        if not changed:
            return result
        self.audit.emit(
            self.audit_prefix + "_notice", scope=self.scope, model_calls=count,
            tool_call_id=notice[1], target_calls=self.target_calls, forced=False,
        )
        return updated[0] if isinstance(result, ToolMessage) else replace(
            result, update={**result.update, "messages": updated},
        )

    def wrap_tool_call(self, request, handler):
        return self._append_notice(request, handler(request))

    async def awrap_tool_call(self, request, handler):
        return self._append_notice(request, await handler(request))
