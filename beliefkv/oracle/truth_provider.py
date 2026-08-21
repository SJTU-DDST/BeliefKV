from __future__ import annotations

import json
import threading
from collections import Counter, deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from beliefkv.oracle.contracts import (
    FrozenActionBoundary,
    FrozenAgentDemand,
    FrozenInvocationDemand,
    FrozenJoinDemand,
    FrozenJoinKey,
    FrozenLLMCallDemand,
    FrozenToolDemand,
    FrozenToolKey,
    LogicalInvocationKey,
    OracleCallPhase,
    OracleFutureView,
    OracleInvocationProgress,
    OracleReplayCursor,
    PerfectFutureOracleArm,
    capability_for_arm,
)


class OracleFutureAccessViolation(RuntimeError):
    """Raised immediately when an oracle arm requests a forbidden future view."""


class OracleReplayCursorError(ValueError):
    """Raised when replay progress is incomplete, invalid, or regresses."""


class AgentFutureField(str, Enum):
    NEXT_ACTION_BOUNDARY = "next_action_boundary"
    REMAINING_DEMAND = "remaining_demand"
    CHILD_COMPLETION_DEMAND = "child_completion_demand"
    JOIN_RELEASE_CONDITION = "join_release_condition"
    TERMINAL_REMAINING_DEMAND = "terminal_remaining_demand"
    SERVICE_UNLOCK = "service_unlock"


class KVFutureField(str, Enum):
    FUTURE_REUSE = "future_reuse"
    NEXT_USE_OR_REENTRY = "next_use_or_reentry"
    FUTURE_GROWTH = "future_growth"
    PARKED_INTERVAL = "parked_interval"
    FUTURE_BENEFICIARY = "future_beneficiary"
    NO_FUTURE_USE_PROOF = "no_future_use_proof"


@dataclass(frozen=True)
class RemainingCallDemand:
    logical_key: LogicalInvocationKey
    call_ordinal: int
    remaining_prompt_tokens: int
    remaining_decode_tokens: int
    boundary: FrozenActionBoundary


@dataclass(frozen=True)
class RemainingToolDemand:
    key: FrozenToolKey
    remaining_service_ms: float


@dataclass(frozen=True)
class AgentRemainingDemand:
    prompt_tokens: int
    decode_tokens: int
    tool_service_ms: float
    call_count: int
    child_count: int


@dataclass(frozen=True)
class ChildCompletionDemand:
    key: LogicalInvocationKey
    remaining: AgentRemainingDemand


@dataclass(frozen=True)
class KVUseDemand:
    semantic_owner: LogicalInvocationKey
    invocation: LogicalInvocationKey
    call_ordinal: int
    remaining_prompt_tokens: int
    remaining_decode_tokens: int
    future_growth_tokens: int


@dataclass(frozen=True)
class ParkedIntervalDemand:
    kind: str
    tool_key: FrozenToolKey | None
    residual_external_duration_ms: float | None
    dependency_members: tuple[LogicalInvocationKey, ...] = ()
    join_key: FrozenJoinKey | None = None


@dataclass(frozen=True)
class NoFutureUseProof:
    semantic_owner: LogicalInvocationKey
    proven: bool
    exhausted_invocations: tuple[LogicalInvocationKey, ...]
    cursor_revision: int


@dataclass(frozen=True)
class OracleTruthAccessRecord:
    sequence: int
    arm: PerfectFutureOracleArm
    planner_epoch: int
    cursor_revision: int
    logical_key: LogicalInvocationKey
    view: OracleFutureView
    field: str
    reason: str
    allowed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "arm": self.arm.value,
            "planner_epoch": self.planner_epoch,
            "cursor_revision": self.cursor_revision,
            "logical_key": self.logical_key.to_dict(),
            "view": self.view.value,
            "field": self.field,
            "reason": self.reason,
            "allowed": self.allowed,
        }


class AgentFutureView:
    def __init__(self, provider: "OracleTruthProvider") -> None:
        self._provider = provider

    def query(
        self,
        *,
        planner_epoch: int,
        logical_key: LogicalInvocationKey,
        cursor: OracleReplayCursor,
        field: AgentFutureField,
        reason: str,
    ) -> object:
        return self._provider._query_agent(
            planner_epoch=planner_epoch,
            logical_key=logical_key,
            cursor=cursor,
            field=field,
            reason=reason,
        )


class KVFutureView:
    def __init__(self, provider: "OracleTruthProvider") -> None:
        self._provider = provider

    def query(
        self,
        *,
        planner_epoch: int,
        logical_key: LogicalInvocationKey,
        cursor: OracleReplayCursor,
        field: KVFutureField,
        reason: str,
    ) -> object:
        return self._provider._query_kv(
            planner_epoch=planner_epoch,
            logical_key=logical_key,
            cursor=cursor,
            field=field,
            reason=reason,
        )


class OracleTruthProvider:
    """Frozen truth with cursor validation and arm-specific projections."""

    def __init__(
        self,
        demand: FrozenAgentDemand,
        *,
        arm: PerfectFutureOracleArm,
        expected_truth_id: str,
        expected_truth_digest: str,
        access_ledger_capacity: int = 4096,
        access_record_sink: Callable[[OracleTruthAccessRecord], None] | None = None,
    ) -> None:
        if expected_truth_id != demand.truth_id:
            raise ValueError("frozen truth_id does not match the replay contract")
        if (
            len(expected_truth_digest) != 64
            or any(item not in "0123456789abcdef" for item in expected_truth_digest)
        ):
            raise ValueError("expected_truth_digest must be a lowercase SHA-256 digest")
        if expected_truth_digest != demand.truth_digest:
            raise ValueError("frozen truth digest does not match the replay contract")
        if type(access_ledger_capacity) is not int or access_ledger_capacity <= 0:
            raise ValueError("access_ledger_capacity must be a positive integer")

        self.arm = arm
        self.truth_id = demand.truth_id
        self.truth_digest = demand.truth_digest
        self._capability = capability_for_arm(arm)
        self._invocations = {item.key: item for item in demand.invocations}
        self._joins = {item.key: item for item in demand.joins}
        self._tools = {
            FrozenToolKey(item.key, tool.tool_ordinal): tool
            for item in demand.invocations
            for tool in item.tools
        }
        child_lists: dict[LogicalInvocationKey, list[LogicalInvocationKey]] = {}
        owner_lists: dict[LogicalInvocationKey, list[LogicalInvocationKey]] = {}
        for item in demand.invocations:
            owner_lists.setdefault(item.semantic_owner, []).append(item.key)
            if item.parent is not None:
                child_lists.setdefault(item.parent, []).append(item.key)
        self._children = {
            key: tuple(sorted(values)) for key, values in child_lists.items()
        }
        self._owner_members = {
            key: tuple(sorted(values)) for key, values in owner_lists.items()
        }

        self._audit_lock = threading.Lock()
        self._access_sequence = 0
        self._recent_access: deque[OracleTruthAccessRecord] = deque(
            maxlen=access_ledger_capacity
        )
        self._access_counts: Counter[tuple[str, str, bool]] = Counter()
        self._access_record_sink = access_record_sink
        self._cursor_lock = threading.Lock()
        self._latest_cursor: OracleReplayCursor | None = None
        self.agent_future = AgentFutureView(self)
        self.kv_future = KVFutureView(self)

    @property
    def access_ledger(self) -> tuple[OracleTruthAccessRecord, ...]:
        with self._audit_lock:
            return tuple(self._recent_access)

    @property
    def access_summary(self) -> dict[str, object]:
        with self._audit_lock:
            return {
                "total_queries": self._access_sequence,
                "recent_capacity": self._recent_access.maxlen,
                "recent_count": len(self._recent_access),
                "evicted_recent_records": max(
                    0, self._access_sequence - len(self._recent_access)
                ),
                "counts": {
                    f"{view}:{field}:{'allowed' if allowed else 'denied'}": count
                    for (view, field, allowed), count in sorted(
                        self._access_counts.items()
                    )
                },
            }

    def canonical_ledger_bytes(self) -> bytes:
        with self._audit_lock:
            payload = {
                "summary": {
                    "total_queries": self._access_sequence,
                    "recent_capacity": self._recent_access.maxlen,
                    "recent_count": len(self._recent_access),
                    "evicted_recent_records": max(
                        0, self._access_sequence - len(self._recent_access)
                    ),
                    "counts": {
                        f"{view}:{field}:{'allowed' if allowed else 'denied'}": count
                        for (view, field, allowed), count in sorted(
                            self._access_counts.items()
                        )
                    },
                },
                "recent": [item.to_dict() for item in self._recent_access],
            }
        return (
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")

    def _record(
        self,
        *,
        planner_epoch: int,
        cursor_revision: int,
        logical_key: LogicalInvocationKey,
        view: OracleFutureView,
        field: str,
        reason: str,
        allowed: bool,
    ) -> None:
        if type(planner_epoch) is not int or planner_epoch < 0:
            raise ValueError("planner_epoch must be a non-negative integer")
        if type(reason) is not str or not reason.strip():
            raise ValueError("oracle truth access reason must be non-empty")
        with self._audit_lock:
            record = OracleTruthAccessRecord(
                sequence=self._access_sequence,
                arm=self.arm,
                planner_epoch=planner_epoch,
                cursor_revision=cursor_revision,
                logical_key=logical_key,
                view=view,
                field=field,
                reason=reason,
                allowed=allowed,
            )
            self._access_sequence += 1
            self._recent_access.append(record)
            self._access_counts[(view.value, field, allowed)] += 1
        if self._access_record_sink is not None:
            self._access_record_sink(record)

    def _authorize(
        self,
        *,
        planner_epoch: int,
        logical_key: LogicalInvocationKey,
        cursor: OracleReplayCursor,
        view: OracleFutureView,
        field: str,
        reason: str,
    ) -> FrozenInvocationDemand:
        allowed = self._capability.allows(view)
        self._record(
            planner_epoch=planner_epoch,
            cursor_revision=cursor.cursor_revision,
            logical_key=logical_key,
            view=view,
            field=field,
            reason=reason,
            allowed=allowed,
        )
        if not allowed:
            raise OracleFutureAccessViolation(
                f"{self.arm.value} cannot access {view.value}.{field}"
            )
        try:
            invocation = self._invocations[logical_key]
        except KeyError as exc:
            raise KeyError("logical invocation key is absent from frozen truth") from exc
        self._validate_and_advance_cursor(cursor)
        return invocation

    def _validate_and_advance_cursor(self, cursor: OracleReplayCursor) -> None:
        with self._cursor_lock:
            previous = self._latest_cursor
            if previous is cursor or previous == cursor:
                return
            self._validate_cursor_content(cursor)
            if previous is not None:
                self._validate_cursor_monotonic(previous, cursor)
            if previous is None or cursor.cursor_revision > previous.cursor_revision:
                self._latest_cursor = cursor

    def _validate_cursor_content(self, cursor: OracleReplayCursor) -> None:
        progress_by_key = {
            item.logical_key: item for item in cursor.invocation_progress
        }
        if set(progress_by_key) != set(self._invocations):
            raise OracleReplayCursorError(
                "cursor must contain progress for every frozen invocation"
            )
        completed_invocations = set(cursor.completed_invocations)
        if not completed_invocations <= set(self._invocations):
            raise OracleReplayCursorError("cursor contains an unknown invocation")
        if not set(cursor.completed_tools) <= set(self._tools):
            raise OracleReplayCursorError("cursor contains an unknown tool")
        if not set(cursor.satisfied_joins) <= set(self._joins):
            raise OracleReplayCursorError("cursor contains an unknown JOIN")

        completed_tools = set(cursor.completed_tools)
        for key, progress in progress_by_key.items():
            invocation = self._invocations[key]
            calls = {item.call_ordinal: item for item in invocation.calls}
            if progress.current_call_ordinal not in calls:
                raise OracleReplayCursorError(
                    "cursor current_call_ordinal is absent from frozen demand"
                )
            call = calls[progress.current_call_ordinal]
            if progress.prefilled_prompt_tokens > call.incremental_prompt_tokens:
                raise OracleReplayCursorError("cursor prefill exceeds prompt demand")
            if progress.generated_tokens > call.output_tokens:
                raise OracleReplayCursorError("cursor decode exceeds output demand")

            if progress.call_phase == OracleCallPhase.NOT_STARTED:
                if (
                    progress.current_call_ordinal
                    != invocation.calls[0].call_ordinal
                    or
                    progress.prefilled_prompt_tokens != 0
                    or progress.generated_tokens != 0
                    or progress.active_tool_ordinal is not None
                ):
                    raise OracleReplayCursorError("NOT_STARTED cursor has progress")
            elif progress.call_phase == OracleCallPhase.PREFILL:
                if (
                    progress.generated_tokens != 0
                    or progress.active_tool_ordinal is not None
                ):
                    raise OracleReplayCursorError("PREFILL cursor has decode/tool state")
            elif progress.call_phase == OracleCallPhase.DECODE:
                if (
                    progress.prefilled_prompt_tokens
                    != call.incremental_prompt_tokens
                    or progress.active_tool_ordinal is not None
                ):
                    raise OracleReplayCursorError("DECODE cursor lacks complete prefill")
            elif progress.call_phase == OracleCallPhase.BOUNDARY_WAIT:
                if (
                    progress.prefilled_prompt_tokens
                    != call.incremental_prompt_tokens
                    or progress.generated_tokens != call.output_tokens
                ):
                    raise OracleReplayCursorError(
                        "BOUNDARY_WAIT cursor lacks complete LLM demand"
                    )
            elif progress.call_phase == OracleCallPhase.INVOCATION_COMPLETE:
                if (
                    progress.current_call_ordinal != invocation.calls[-1].call_ordinal
                    or progress.prefilled_prompt_tokens
                    != call.incremental_prompt_tokens
                    or progress.generated_tokens != call.output_tokens
                    or progress.active_tool_ordinal is not None
                ):
                    raise OracleReplayCursorError(
                        "INVOCATION_COMPLETE cursor is not at terminal call"
                    )

            complete = key in completed_invocations
            if complete != (
                progress.call_phase == OracleCallPhase.INVOCATION_COMPLETE
            ):
                raise OracleReplayCursorError(
                    "completed_invocations disagrees with invocation phase"
                )
            invocation_tool_keys = {
                FrozenToolKey(key, item.tool_ordinal) for item in invocation.tools
            }
            if complete and not invocation_tool_keys <= completed_tools:
                raise OracleReplayCursorError(
                    "completed invocation has unfinished tool demand"
                )
            if progress.active_tool_ordinal is not None:
                tool_key = FrozenToolKey(key, progress.active_tool_ordinal)
                tool = self._tools.get(tool_key)
                if (
                    progress.call_phase != OracleCallPhase.BOUNDARY_WAIT
                    or tool is None
                    or tool.starts_after_call_ordinal != call.call_ordinal
                    or tool_key in completed_tools
                    or progress.tool_elapsed_ms > tool.service_duration_ms
                ):
                    raise OracleReplayCursorError("active tool cursor is inconsistent")

        for tool_key in completed_tools:
            tool = self._tools[tool_key]
            progress = progress_by_key[tool_key.invocation]
            if progress.current_call_ordinal < tool.starts_after_call_ordinal:
                raise OracleReplayCursorError(
                    "completed tool appears before its action boundary"
                )
            if (
                progress.current_call_ordinal == tool.starts_after_call_ordinal
                and progress.call_phase.rank < OracleCallPhase.BOUNDARY_WAIT.rank
            ):
                raise OracleReplayCursorError(
                    "completed tool appears before its boundary was produced"
                )

        completed_invocations = set(cursor.completed_invocations)
        for join_key in cursor.satisfied_joins:
            join = self._joins[join_key]
            completed_members = sum(
                member in completed_invocations for member in join.members
            )
            required = len(join.members) if join.mode.value == "all" else 1
            if completed_members < required:
                raise OracleReplayCursorError(
                    "satisfied JOIN lacks completed dependency members"
                )
            for waiter in join.waiters:
                progress = progress_by_key[waiter]
                join_call = next(
                    call
                    for call in self._invocations[waiter].calls
                    if join_key in call.boundary.joins
                )
                if progress.current_call_ordinal < join_call.call_ordinal or (
                    progress.current_call_ordinal == join_call.call_ordinal
                    and progress.call_phase.rank
                    < OracleCallPhase.BOUNDARY_WAIT.rank
                ):
                    raise OracleReplayCursorError(
                        "JOIN is satisfied before its waiter reached the boundary"
                    )

        for key, progress in progress_by_key.items():
            invocation = self._invocations[key]
            for tool in invocation.tools:
                if tool.starts_after_call_ordinal < progress.current_call_ordinal:
                    tool_key = FrozenToolKey(key, tool.tool_ordinal)
                    if tool_key not in completed_tools:
                        raise OracleReplayCursorError(
                            "cursor advanced past an unfinished tool boundary"
                        )

    def _validate_cursor_monotonic(
        self,
        previous: OracleReplayCursor,
        current: OracleReplayCursor,
    ) -> None:
        if current.cursor_revision < previous.cursor_revision:
            raise OracleReplayCursorError("cursor revision moved backwards")
        if current.cursor_revision == previous.cursor_revision:
            if current != previous:
                raise OracleReplayCursorError(
                    "same cursor revision has different replay state"
                )
            return
        if not set(previous.completed_invocations) <= set(
            current.completed_invocations
        ):
            raise OracleReplayCursorError("completed invocation state regressed")
        if not set(previous.completed_tools) <= set(current.completed_tools):
            raise OracleReplayCursorError("completed tool state regressed")
        if not set(previous.satisfied_joins) <= set(current.satisfied_joins):
            raise OracleReplayCursorError("satisfied JOIN state regressed")

        current_progress = {
            item.logical_key: item for item in current.invocation_progress
        }
        for old in previous.invocation_progress:
            new = current_progress[old.logical_key]
            if new.current_call_ordinal < old.current_call_ordinal:
                raise OracleReplayCursorError("current call ordinal moved backwards")
            if new.current_call_ordinal == old.current_call_ordinal:
                if new.call_phase.rank < old.call_phase.rank:
                    raise OracleReplayCursorError("call phase moved backwards")
                if (
                    new.prefilled_prompt_tokens < old.prefilled_prompt_tokens
                    or new.generated_tokens < old.generated_tokens
                ):
                    raise OracleReplayCursorError("token progress moved backwards")
                if (
                    old.active_tool_ordinal is not None
                    and new.active_tool_ordinal == old.active_tool_ordinal
                    and new.tool_elapsed_ms < old.tool_elapsed_ms
                ):
                    raise OracleReplayCursorError("tool elapsed time moved backwards")
            if old.active_tool_ordinal is not None and (
                new.active_tool_ordinal != old.active_tool_ordinal
            ):
                completed_key = FrozenToolKey(
                    old.logical_key, old.active_tool_ordinal
                )
                if completed_key not in current.completed_tools:
                    raise OracleReplayCursorError(
                        "active tool disappeared without a completion event"
                    )

    def _remaining_calls(
        self,
        invocation: FrozenInvocationDemand,
        cursor: OracleReplayCursor,
    ) -> tuple[RemainingCallDemand, ...]:
        if invocation.key in cursor.completed_invocations:
            return ()
        progress = cursor.progress_for(invocation.key)
        result: list[RemainingCallDemand] = []
        for call in invocation.calls:
            if call.call_ordinal < progress.current_call_ordinal:
                continue
            prompt = call.incremental_prompt_tokens
            decode = call.output_tokens
            if call.call_ordinal == progress.current_call_ordinal:
                if progress.call_phase == OracleCallPhase.PREFILL:
                    prompt -= progress.prefilled_prompt_tokens
                elif progress.call_phase == OracleCallPhase.DECODE:
                    prompt = 0
                    decode -= progress.generated_tokens
                elif progress.call_phase in {
                    OracleCallPhase.BOUNDARY_WAIT,
                    OracleCallPhase.INVOCATION_COMPLETE,
                }:
                    prompt = 0
                    decode = 0
            result.append(
                RemainingCallDemand(
                    logical_key=invocation.key,
                    call_ordinal=call.call_ordinal,
                    remaining_prompt_tokens=prompt,
                    remaining_decode_tokens=decode,
                    boundary=call.boundary,
                )
            )
        return tuple(result)

    def _remaining_tools(
        self,
        invocation: FrozenInvocationDemand,
        cursor: OracleReplayCursor,
    ) -> tuple[RemainingToolDemand, ...]:
        if invocation.key in cursor.completed_invocations:
            return ()
        completed = set(cursor.completed_tools)
        progress = cursor.progress_for(invocation.key)
        result = []
        for tool in invocation.tools:
            key = FrozenToolKey(invocation.key, tool.tool_ordinal)
            if key in completed:
                continue
            residual = tool.service_duration_ms
            if progress.active_tool_ordinal == tool.tool_ordinal:
                residual -= progress.tool_elapsed_ms
            result.append(RemainingToolDemand(key, residual))
        return tuple(result)

    def _descendants(
        self,
        logical_key: LogicalInvocationKey,
    ) -> tuple[LogicalInvocationKey, ...]:
        pending = list(self._children.get(logical_key, ()))
        result: list[LogicalInvocationKey] = []
        while pending:
            item = pending.pop(0)
            result.append(item)
            pending.extend(self._children.get(item, ()))
        return tuple(result)

    def _summary(
        self,
        invocation: FrozenInvocationDemand,
        cursor: OracleReplayCursor,
        *,
        include_descendants: bool,
    ) -> AgentRemainingDemand:
        keys = [invocation.key]
        if include_descendants:
            keys.extend(self._descendants(invocation.key))
        calls: list[RemainingCallDemand] = []
        tools: list[RemainingToolDemand] = []
        active_keys = []
        for key in keys:
            if key in cursor.completed_invocations:
                continue
            active_keys.append(key)
            current = self._invocations[key]
            calls.extend(self._remaining_calls(current, cursor))
            tools.extend(self._remaining_tools(current, cursor))
        return AgentRemainingDemand(
            prompt_tokens=sum(item.remaining_prompt_tokens for item in calls),
            decode_tokens=sum(item.remaining_decode_tokens for item in calls),
            tool_service_ms=sum(item.remaining_service_ms for item in tools),
            call_count=sum(
                1
                for item in calls
                if item.remaining_prompt_tokens > 0
                or item.remaining_decode_tokens > 0
            ),
            child_count=sum(key != invocation.key for key in active_keys),
        )

    def _query_agent(
        self,
        *,
        planner_epoch: int,
        logical_key: LogicalInvocationKey,
        cursor: OracleReplayCursor,
        field: AgentFutureField,
        reason: str,
    ) -> object:
        invocation = self._authorize(
            planner_epoch=planner_epoch,
            logical_key=logical_key,
            cursor=cursor,
            view=OracleFutureView.AGENT,
            field=field.value,
            reason=reason,
        )
        calls = self._remaining_calls(invocation, cursor)
        service_calls = tuple(
            item
            for item in calls
            if item.remaining_prompt_tokens > 0 or item.remaining_decode_tokens > 0
        )
        if field == AgentFutureField.NEXT_ACTION_BOUNDARY:
            return (
                (service_calls[0].call_ordinal, service_calls[0].boundary)
                if service_calls
                else None
            )
        if field == AgentFutureField.REMAINING_DEMAND:
            return self._summary(invocation, cursor, include_descendants=False)
        if field == AgentFutureField.CHILD_COMPLETION_DEMAND:
            return tuple(
                ChildCompletionDemand(
                    key=child_key,
                    remaining=self._summary(
                        self._invocations[child_key],
                        cursor,
                        include_descendants=True,
                    ),
                )
                for child_key in self._children.get(logical_key, ())
                if child_key not in cursor.completed_invocations
            )
        if field == AgentFutureField.JOIN_RELEASE_CONDITION:
            satisfied = set(cursor.satisfied_joins)
            return tuple(
                join
                for join in self._joins.values()
                if logical_key in join.waiters and join.key not in satisfied
            )
        if field == AgentFutureField.TERMINAL_REMAINING_DEMAND:
            return self._summary(invocation, cursor, include_descendants=True)
        if field == AgentFutureField.SERVICE_UNLOCK:
            return tuple((item.call_ordinal, item.boundary.kind) for item in service_calls)
        raise AssertionError(f"unhandled agent future field: {field}")

    def _owner_uses(
        self,
        semantic_owner: LogicalInvocationKey,
        cursor: OracleReplayCursor,
    ) -> tuple[KVUseDemand, ...]:
        result = []
        for key in self._owner_members[semantic_owner]:
            invocation = self._invocations[key]
            for item in self._remaining_calls(invocation, cursor):
                if (
                    item.remaining_prompt_tokens == 0
                    and item.remaining_decode_tokens == 0
                ):
                    continue
                frozen_call = next(
                    call
                    for call in invocation.calls
                    if call.call_ordinal == item.call_ordinal
                )
                result.append(
                    KVUseDemand(
                        semantic_owner=semantic_owner,
                        invocation=key,
                        call_ordinal=item.call_ordinal,
                        remaining_prompt_tokens=item.remaining_prompt_tokens,
                        remaining_decode_tokens=item.remaining_decode_tokens,
                        future_growth_tokens=(
                            item.remaining_prompt_tokens
                            + item.remaining_decode_tokens
                            + frozen_call.parent_reentry_prompt_growth_tokens
                        ),
                    )
                )
        return tuple(
            sorted(
                result,
                key=lambda item: (item.invocation, item.call_ordinal),
            )
        )

    def _query_kv(
        self,
        *,
        planner_epoch: int,
        logical_key: LogicalInvocationKey,
        cursor: OracleReplayCursor,
        field: KVFutureField,
        reason: str,
    ) -> object:
        invocation = self._authorize(
            planner_epoch=planner_epoch,
            logical_key=logical_key,
            cursor=cursor,
            view=OracleFutureView.KV,
            field=field.value,
            reason=reason,
        )
        owner = invocation.semantic_owner
        members = self._owner_members[owner]
        uses = self._owner_uses(owner, cursor)
        if field == KVFutureField.FUTURE_REUSE:
            return bool(uses)
        if field == KVFutureField.NEXT_USE_OR_REENTRY:
            first_by_member: dict[LogicalInvocationKey, KVUseDemand] = {}
            for item in uses:
                first_by_member.setdefault(item.invocation, item)
            return tuple(first_by_member[key] for key in sorted(first_by_member))
        if field == KVFutureField.FUTURE_GROWTH:
            return uses
        if field == KVFutureField.PARKED_INTERVAL:
            intervals: list[ParkedIntervalDemand] = []
            for key in members:
                current = self._invocations[key]
                intervals.extend(
                    ParkedIntervalDemand(
                        kind="tool",
                        tool_key=item.key,
                        residual_external_duration_ms=item.remaining_service_ms,
                    )
                    for item in self._remaining_tools(current, cursor)
                )
            satisfied = set(cursor.satisfied_joins)
            intervals.extend(
                ParkedIntervalDemand(
                    kind=f"join_{join.mode.value}",
                    tool_key=None,
                    residual_external_duration_ms=None,
                    dependency_members=join.members,
                    join_key=join.key,
                )
                for join in self._joins.values()
                if any(member in join.waiters for member in members)
                and join.key not in satisfied
            )
            return tuple(intervals)
        if field == KVFutureField.FUTURE_BENEFICIARY:
            values: set[LogicalInvocationKey] = set()
            for key in members:
                current = self._invocations[key]
                values.update(self._children.get(key, ()))
                if current.parent is not None:
                    values.add(current.parent)
            values.difference_update(members)
            return tuple(sorted(values))
        if field == KVFutureField.NO_FUTURE_USE_PROOF:
            exhausted = tuple(
                key
                for key in members
                if key in cursor.completed_invocations
                or not any(
                    item.remaining_prompt_tokens > 0
                    or item.remaining_decode_tokens > 0
                    for item in self._remaining_calls(
                        self._invocations[key], cursor
                    )
                )
            )
            return NoFutureUseProof(
                semantic_owner=owner,
                proven=not uses,
                exhausted_invocations=exhausted,
                cursor_revision=cursor.cursor_revision,
            )
        raise AssertionError(f"unhandled KV future field: {field}")
