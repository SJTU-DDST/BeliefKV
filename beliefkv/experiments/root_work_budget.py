"""Soft budgets for root work between completed model-selected JOINs."""

from __future__ import annotations

from typing import Annotated, Any

from langchain.agents.middleware.types import AgentState, PrivateStateAttr
from langgraph.channels.untracked_value import UntrackedValue
from typing_extensions import NotRequired

from beliefkv.experiments.child_work_budget import ChildWorkBudgetMiddleware


ROOT_WORK_BUDGET_PRIVATE_KEYS = frozenset({
    "root_work_model_calls", "root_work_notice", "root_work_completed_joins",
    "root_work_total_calls",
})


class RootWorkBudgetState(AgentState[Any]):
    root_work_model_calls: NotRequired[Annotated[int, UntrackedValue, PrivateStateAttr]]
    root_work_notice: NotRequired[
        Annotated[tuple[int, str] | None, UntrackedValue, PrivateStateAttr]
    ]
    root_work_completed_joins: NotRequired[Annotated[int, UntrackedValue, PrivateStateAttr]]
    root_work_total_calls: NotRequired[Annotated[int, UntrackedValue, PrivateStateAttr]]


def root_budget_instruction(warning_calls: int, target_calls: int) -> str:
    return f"""
BOUNDED ROOT WORK PHASES
Keep each direct-work phase focused on a concrete integration or verification
milestone. After about {warning_calls} root model turns without a completed JOIN,
reassess substantial independent questions for delegation. Aim to finish that
direct-work phase within about {target_calls} root model turns, then integrate
delegated evidence and choose the next useful phase from the remaining task.
These are soft phase targets, not a limit on the workflow's total model calls.
Complete active commands, delegated JOINs, edits and necessary validation.
Do not omit requirements, terminate unfinished work, or claim an incomplete
assignment succeeded to meet the budget. Choose assignments, fanout, rounds and
nesting yourself. Return when the original task is complete or honestly blocked.
"""


class RootWorkBudgetMiddleware(ChildWorkBudgetMiddleware):
    state_schema = RootWorkBudgetState
    model_calls_key = "root_work_model_calls"
    notice_key = "root_work_notice"
    audit_prefix = "root_work_budget"

    def __init__(self, *, adapter, **kwargs):
        super().__init__(**kwargs)
        self.adapter = adapter

    def _model_call_progress(self, request):
        joins = self.adapter.completed_join_count()
        previous = int(request.state.get("root_work_completed_joins", 0))
        count = int(request.state.get(self.model_calls_key, 0)) if joins == previous else 0
        return count + 1, {
            "root_work_completed_joins": joins,
            "root_work_total_calls": int(request.state.get("root_work_total_calls", 0)) + 1,
        }

    def _record_return(self, count, message, request):
        self.audit.emit(
            "root_work_budget_return", scope=self.scope,
            phase_model_calls=count,
            total_model_calls=int(request.state.get("root_work_total_calls", 0)) + 1,
            completed_joins=self.adapter.completed_join_count(), forced=False,
        )

    def _notice_instruction(self, count):
        return (
            f"\n\nROOT WORK PHASE: {count} root model turns have completed since "
            "the last completed delegated JOIN. "
            + (
                f"Aim to close this direct-work phase by about turn {self.target_calls}. "
                if count == self.warning_calls else
                "The soft direct-work phase target has been reached. "
            )
            + "Use the observed evidence to narrow the next integration or validation "
            "milestone. Delegate substantial independent remaining questions through "
            "task when useful, then integrate their reports. Complete active commands, "
            "edits, pending JOINs and necessary focused validation. This does not "
            "limit total workflow turns: do not skip requirements, pad delegation, "
            "or finalize unfinished work to meet a turn count. Continue genuine "
            "follow-up phases when needed. All tools remain available."
        )

    def _append_notice(self, request, result):
        if (
            request.state.get(self.notice_key) is not None
            and self.adapter.completed_join_count()
            != int(request.state.get("root_work_completed_joins", 0))
        ):
            return result
        return super()._append_notice(request, result)
