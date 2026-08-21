from __future__ import annotations

import json
import math
from hashlib import sha256
from dataclasses import dataclass
from enum import Enum
from typing import Mapping


FROZEN_AGENT_DEMAND_SCHEMA_VERSION = 2


def _require_text(value: str, field_name: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")


def _require_nonnegative(value: int | float, field_name: str) -> None:
    if type(value) not in {int, float}:
        raise TypeError(f"{field_name} must be an integer or float")
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _require_int(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an integer")
    return value


def _require_float(value: object, field_name: str) -> float:
    if type(value) is not float:
        raise TypeError(f"{field_name} must be a float")
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite")
    return value


def _require_str(value: object, field_name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name} must be a string")
    _require_text(value, field_name)
    return value


def _require_list(value: object, field_name: str) -> list[object]:
    if type(value) is not list:
        raise TypeError(f"{field_name} must be a JSON array")
    return value


def _strict_fields(
    raw: Mapping[str, object],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    object_name: str,
) -> None:
    missing = required - raw.keys()
    unknown = raw.keys() - required - optional
    if missing:
        raise ValueError(f"{object_name} is missing fields: {sorted(missing)}")
    if unknown:
        raise ValueError(f"{object_name} contains unknown fields: {sorted(unknown)}")


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"frozen demand JSON contains duplicate key: {key}")
        result[key] = value
    return result


class PerfectFutureOracleArm(str, Enum):
    O0_CURRENT = "o0_current"
    O1_AGENT = "o1_agent"
    O2_KV = "o2_kv"
    O3_JOINT = "o3_joint"


class OracleFutureView(str, Enum):
    AGENT = "agent_future"
    KV = "kv_future"


@dataclass(frozen=True)
class OracleArmCapability:
    arm: PerfectFutureOracleArm
    allowed_views: frozenset[OracleFutureView]

    def allows(self, view: OracleFutureView) -> bool:
        return view in self.allowed_views


_ARM_CAPABILITIES = {
    PerfectFutureOracleArm.O0_CURRENT: OracleArmCapability(
        PerfectFutureOracleArm.O0_CURRENT,
        frozenset(),
    ),
    PerfectFutureOracleArm.O1_AGENT: OracleArmCapability(
        PerfectFutureOracleArm.O1_AGENT,
        frozenset({OracleFutureView.AGENT}),
    ),
    PerfectFutureOracleArm.O2_KV: OracleArmCapability(
        PerfectFutureOracleArm.O2_KV,
        frozenset({OracleFutureView.KV}),
    ),
    PerfectFutureOracleArm.O3_JOINT: OracleArmCapability(
        PerfectFutureOracleArm.O3_JOINT,
        frozenset({OracleFutureView.AGENT, OracleFutureView.KV}),
    ),
}


def capability_for_arm(arm: PerfectFutureOracleArm) -> OracleArmCapability:
    return _ARM_CAPABILITIES[arm]


@dataclass(frozen=True, order=True)
class LogicalInvocationKey:
    """Schedule-independent invocation/context identity used by oracle replay."""

    workload_instance: str
    canonical_agent_path: tuple[str, ...]
    parent_spawn_ordinal: int
    invocation_ordinal: int
    context_epoch: int

    def __post_init__(self) -> None:
        _require_text(self.workload_instance, "workload_instance")
        if type(self.canonical_agent_path) is not tuple:
            raise TypeError("canonical_agent_path must be a tuple")
        if not self.canonical_agent_path:
            raise ValueError("canonical_agent_path must not be empty")
        for index, item in enumerate(self.canonical_agent_path):
            _require_text(item, f"canonical_agent_path[{index}]")
        for name in (
            "parent_spawn_ordinal",
            "invocation_ordinal",
            "context_epoch",
        ):
            _require_int(getattr(self, name), name)
            _require_nonnegative(getattr(self, name), name)

    def to_dict(self) -> dict[str, object]:
        return {
            "workload_instance": self.workload_instance,
            "canonical_agent_path": list(self.canonical_agent_path),
            "parent_spawn_ordinal": self.parent_spawn_ordinal,
            "invocation_ordinal": self.invocation_ordinal,
            "context_epoch": self.context_epoch,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "LogicalInvocationKey":
        _strict_fields(
            raw,
            required=frozenset(
                {
                    "workload_instance",
                    "canonical_agent_path",
                    "parent_spawn_ordinal",
                    "invocation_ordinal",
                    "context_epoch",
                }
            ),
            object_name="LogicalInvocationKey",
        )
        path = _require_list(raw["canonical_agent_path"], "canonical_agent_path")
        return cls(
            workload_instance=_require_str(
                raw["workload_instance"], "workload_instance"
            ),
            canonical_agent_path=tuple(
                _require_str(item, f"canonical_agent_path[{index}]")
                for index, item in enumerate(path)
            ),
            parent_spawn_ordinal=_require_int(
                raw["parent_spawn_ordinal"], "parent_spawn_ordinal"
            ),
            invocation_ordinal=_require_int(
                raw["invocation_ordinal"], "invocation_ordinal"
            ),
            context_epoch=_require_int(raw["context_epoch"], "context_epoch"),
        )


@dataclass(frozen=True, order=True)
class FrozenJoinKey:
    workload_instance: str
    owner: LogicalInvocationKey
    join_ordinal: int

    def __post_init__(self) -> None:
        _require_text(self.workload_instance, "join workload_instance")
        _require_int(self.join_ordinal, "join_ordinal")
        _require_nonnegative(self.join_ordinal, "join_ordinal")
        if self.owner.workload_instance != self.workload_instance:
            raise ValueError("join owner must belong to join workload")

    def to_dict(self) -> dict[str, object]:
        return {
            "workload_instance": self.workload_instance,
            "owner": self.owner.to_dict(),
            "join_ordinal": self.join_ordinal,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "FrozenJoinKey":
        _strict_fields(
            raw,
            required=frozenset({"workload_instance", "owner", "join_ordinal"}),
            object_name="FrozenJoinKey",
        )
        owner = raw["owner"]
        if not isinstance(owner, Mapping):
            raise TypeError("join owner must be an object")
        return cls(
            workload_instance=_require_str(
                raw["workload_instance"], "join workload_instance"
            ),
            owner=LogicalInvocationKey.from_dict(owner),
            join_ordinal=_require_int(raw["join_ordinal"], "join_ordinal"),
        )


class FrozenActionBoundaryKind(str, Enum):
    CONTINUE = "continue"
    CALL = "call"
    TOOL = "tool"
    SPAWN = "spawn"
    MESSAGE = "message"
    HANDOFF = "handoff"
    RETURN = "return"
    JOIN = "join"
    FINAL = "final"


class FrozenInvocationRelation(str, Enum):
    ROOT = "root"
    CALL = "call"
    SPAWN = "spawn"
    MESSAGE = "message"
    HANDOFF = "handoff"


class FrozenContextMode(str, Enum):
    FRESH = "fresh"
    FORK = "fork"
    RESUME = "resume"


class FrozenJoinMode(str, Enum):
    ALL = "all"
    ANY = "any"


class FrozenToolOutcome(str, Enum):
    SUCCESS = "success"
    ERROR = "error"


@dataclass(frozen=True)
class FrozenActionBoundary:
    kind: FrozenActionBoundaryKind
    target_invocations: tuple[LogicalInvocationKey, ...] = ()
    tool_ordinals: tuple[int, ...] = ()
    joins: tuple[FrozenJoinKey, ...] = ()
    output_token_offset: int | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not FrozenActionBoundaryKind:
            raise TypeError("action boundary kind must be FrozenActionBoundaryKind")
        if type(self.target_invocations) is not tuple:
            raise TypeError("target_invocations must be a tuple")
        if type(self.tool_ordinals) is not tuple:
            raise TypeError("tool_ordinals must be a tuple")
        if type(self.joins) is not tuple:
            raise TypeError("joins must be a tuple")
        targets = tuple(sorted(self.target_invocations))
        tools = tuple(sorted(self.tool_ordinals))
        joins = tuple(sorted(self.joins))
        if len(set(targets)) != len(targets):
            raise ValueError("action boundary target invocations must be unique")
        if len(set(tools)) != len(tools):
            raise ValueError("action boundary tool ordinals must be unique")
        if len(set(joins)) != len(joins):
            raise ValueError("action boundary joins must be unique")
        for item in tools:
            _require_int(item, "tool_ordinal")
            _require_nonnegative(item, "tool_ordinal")
        if self.output_token_offset is not None:
            _require_int(self.output_token_offset, "output_token_offset")
            _require_nonnegative(self.output_token_offset, "output_token_offset")
        object.__setattr__(self, "target_invocations", targets)
        object.__setattr__(self, "tool_ordinals", tools)
        object.__setattr__(self, "joins", joins)

        if self.kind == FrozenActionBoundaryKind.TOOL and not tools:
            raise ValueError("TOOL boundary requires at least one tool ordinal")
        if self.kind in {
            FrozenActionBoundaryKind.CALL,
            FrozenActionBoundaryKind.SPAWN,
            FrozenActionBoundaryKind.MESSAGE,
            FrozenActionBoundaryKind.HANDOFF,
        } and not targets:
            raise ValueError(f"{self.kind.value} boundary requires a target")
        if self.kind == FrozenActionBoundaryKind.JOIN and not joins:
            raise ValueError("JOIN boundary requires at least one join")
        if self.kind != FrozenActionBoundaryKind.TOOL and tools:
            raise ValueError("tool ordinals are only valid for TOOL boundaries")
        if self.kind not in {
            FrozenActionBoundaryKind.CALL,
            FrozenActionBoundaryKind.SPAWN,
            FrozenActionBoundaryKind.MESSAGE,
            FrozenActionBoundaryKind.HANDOFF,
            FrozenActionBoundaryKind.RETURN,
        } and targets:
            raise ValueError(f"targets are not valid for {self.kind.value} boundaries")
        if self.kind != FrozenActionBoundaryKind.JOIN and joins:
            raise ValueError("join keys are only valid for JOIN boundaries")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "target_invocations": [item.to_dict() for item in self.target_invocations],
            "tool_ordinals": list(self.tool_ordinals),
            "joins": [item.to_dict() for item in self.joins],
            "output_token_offset": self.output_token_offset,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "FrozenActionBoundary":
        _strict_fields(
            raw,
            required=frozenset(
                {
                    "kind",
                    "target_invocations",
                    "tool_ordinals",
                    "joins",
                    "output_token_offset",
                }
            ),
            object_name="FrozenActionBoundary",
        )
        targets = _require_list(raw["target_invocations"], "target_invocations")
        joins = _require_list(raw["joins"], "joins")
        tools = _require_list(raw["tool_ordinals"], "tool_ordinals")
        if any(not isinstance(item, Mapping) for item in tuple(targets) + tuple(joins)):
            raise TypeError("target invocations and joins must contain objects")
        return cls(
            kind=FrozenActionBoundaryKind(
                _require_str(raw["kind"], "action boundary kind")
            ),
            target_invocations=tuple(
                LogicalInvocationKey.from_dict(item) for item in targets
            ),
            tool_ordinals=tuple(
                _require_int(item, f"tool_ordinals[{index}]")
                for index, item in enumerate(tools)
            ),
            joins=tuple(FrozenJoinKey.from_dict(item) for item in joins),
            output_token_offset=(
                _require_int(raw["output_token_offset"], "output_token_offset")
                if raw["output_token_offset"] is not None
                else None
            ),
        )


@dataclass(frozen=True)
class FrozenLLMCallDemand:
    call_ordinal: int
    prompt_tokens: int
    incremental_prompt_tokens: int
    output_tokens: int
    parent_reentry_prompt_growth_tokens: int
    boundary: FrozenActionBoundary

    def __post_init__(self) -> None:
        for field_name in (
            "call_ordinal",
            "prompt_tokens",
            "incremental_prompt_tokens",
            "output_tokens",
            "parent_reentry_prompt_growth_tokens",
        ):
            _require_int(getattr(self, field_name), field_name)
            _require_nonnegative(getattr(self, field_name), field_name)
        if self.incremental_prompt_tokens > self.prompt_tokens:
            raise ValueError("incremental prompt tokens cannot exceed prompt tokens")
        if (
            self.boundary.output_token_offset is not None
            and self.boundary.output_token_offset > self.output_tokens
        ):
            raise ValueError("action boundary offset exceeds output demand")

    def to_dict(self) -> dict[str, object]:
        return {
            "call_ordinal": self.call_ordinal,
            "prompt_tokens": self.prompt_tokens,
            "incremental_prompt_tokens": self.incremental_prompt_tokens,
            "output_tokens": self.output_tokens,
            "parent_reentry_prompt_growth_tokens": self.parent_reentry_prompt_growth_tokens,
            "boundary": self.boundary.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "FrozenLLMCallDemand":
        _strict_fields(
            raw,
            required=frozenset(
                {
                    "call_ordinal",
                    "prompt_tokens",
                    "incremental_prompt_tokens",
                    "output_tokens",
                    "parent_reentry_prompt_growth_tokens",
                    "boundary",
                }
            ),
            object_name="FrozenLLMCallDemand",
        )
        boundary = raw["boundary"]
        if not isinstance(boundary, Mapping):
            raise TypeError("boundary must be an object")
        return cls(
            call_ordinal=_require_int(raw["call_ordinal"], "call_ordinal"),
            prompt_tokens=_require_int(raw["prompt_tokens"], "prompt_tokens"),
            incremental_prompt_tokens=_require_int(
                raw["incremental_prompt_tokens"], "incremental_prompt_tokens"
            ),
            output_tokens=_require_int(raw["output_tokens"], "output_tokens"),
            parent_reentry_prompt_growth_tokens=_require_int(
                raw["parent_reentry_prompt_growth_tokens"],
                "parent_reentry_prompt_growth_tokens",
            ),
            boundary=FrozenActionBoundary.from_dict(boundary),
        )


@dataclass(frozen=True)
class FrozenToolDemand:
    tool_ordinal: int
    starts_after_call_ordinal: int
    tool_family: str
    backend_class: str
    service_duration_ms: float
    result_prompt_tokens: int
    outcome: FrozenToolOutcome = FrozenToolOutcome.SUCCESS

    def __post_init__(self) -> None:
        if type(self.outcome) is not FrozenToolOutcome:
            raise TypeError("outcome must be FrozenToolOutcome")
        _require_int(self.tool_ordinal, "tool_ordinal")
        _require_int(self.starts_after_call_ordinal, "starts_after_call_ordinal")
        _require_nonnegative(self.tool_ordinal, "tool_ordinal")
        _require_nonnegative(self.starts_after_call_ordinal, "starts_after_call_ordinal")
        _require_text(self.tool_family, "tool_family")
        _require_text(self.backend_class, "backend_class")
        _require_float(self.service_duration_ms, "service_duration_ms")
        _require_int(self.result_prompt_tokens, "result_prompt_tokens")
        _require_nonnegative(self.service_duration_ms, "service_duration_ms")
        _require_nonnegative(self.result_prompt_tokens, "result_prompt_tokens")

    def to_dict(self) -> dict[str, object]:
        return {
            "tool_ordinal": self.tool_ordinal,
            "starts_after_call_ordinal": self.starts_after_call_ordinal,
            "tool_family": self.tool_family,
            "backend_class": self.backend_class,
            "service_duration_ms": self.service_duration_ms,
            "result_prompt_tokens": self.result_prompt_tokens,
            "outcome": self.outcome.value,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "FrozenToolDemand":
        _strict_fields(
            raw,
            required=frozenset(
                {
                    "tool_ordinal",
                    "starts_after_call_ordinal",
                    "tool_family",
                    "backend_class",
                    "service_duration_ms",
                    "result_prompt_tokens",
                    "outcome",
                }
            ),
            object_name="FrozenToolDemand",
        )
        return cls(
            tool_ordinal=_require_int(raw["tool_ordinal"], "tool_ordinal"),
            starts_after_call_ordinal=_require_int(
                raw["starts_after_call_ordinal"], "starts_after_call_ordinal"
            ),
            tool_family=_require_str(raw["tool_family"], "tool_family"),
            backend_class=_require_str(raw["backend_class"], "backend_class"),
            service_duration_ms=_require_float(
                raw["service_duration_ms"], "service_duration_ms"
            ),
            result_prompt_tokens=_require_int(
                raw["result_prompt_tokens"], "result_prompt_tokens"
            ),
            outcome=FrozenToolOutcome(_require_str(raw["outcome"], "outcome")),
        )


@dataclass(frozen=True, order=True)
class FrozenToolKey:
    invocation: LogicalInvocationKey
    tool_ordinal: int

    def __post_init__(self) -> None:
        _require_int(self.tool_ordinal, "tool_ordinal")
        _require_nonnegative(self.tool_ordinal, "tool_ordinal")

    def to_dict(self) -> dict[str, object]:
        return {
            "invocation": self.invocation.to_dict(),
            "tool_ordinal": self.tool_ordinal,
        }


class OracleCallPhase(str, Enum):
    NOT_STARTED = "not_started"
    PREFILL = "prefill"
    DECODE = "decode"
    BOUNDARY_WAIT = "boundary_wait"
    INVOCATION_COMPLETE = "invocation_complete"

    @property
    def rank(self) -> int:
        return {
            OracleCallPhase.NOT_STARTED: 0,
            OracleCallPhase.PREFILL: 1,
            OracleCallPhase.DECODE: 2,
            OracleCallPhase.BOUNDARY_WAIT: 3,
            OracleCallPhase.INVOCATION_COMPLETE: 4,
        }[self]


@dataclass(frozen=True, order=True)
class OracleInvocationProgress:
    logical_key: LogicalInvocationKey
    current_call_ordinal: int
    call_phase: OracleCallPhase
    prefilled_prompt_tokens: int
    generated_tokens: int
    active_tool_ordinal: int | None = None
    tool_elapsed_ms: float = 0.0

    def __post_init__(self) -> None:
        if type(self.call_phase) is not OracleCallPhase:
            raise TypeError("call_phase must be OracleCallPhase")
        for name in (
            "current_call_ordinal",
            "prefilled_prompt_tokens",
            "generated_tokens",
        ):
            _require_int(getattr(self, name), name)
            _require_nonnegative(getattr(self, name), name)
        if self.active_tool_ordinal is not None:
            _require_int(self.active_tool_ordinal, "active_tool_ordinal")
            _require_nonnegative(self.active_tool_ordinal, "active_tool_ordinal")
        _require_float(self.tool_elapsed_ms, "tool_elapsed_ms")
        _require_nonnegative(self.tool_elapsed_ms, "tool_elapsed_ms")
        if self.active_tool_ordinal is None and self.tool_elapsed_ms != 0.0:
            raise ValueError("tool_elapsed_ms requires an active tool")

    def to_dict(self) -> dict[str, object]:
        return {
            "logical_key": self.logical_key.to_dict(),
            "current_call_ordinal": self.current_call_ordinal,
            "call_phase": self.call_phase.value,
            "prefilled_prompt_tokens": self.prefilled_prompt_tokens,
            "generated_tokens": self.generated_tokens,
            "active_tool_ordinal": self.active_tool_ordinal,
            "tool_elapsed_ms": self.tool_elapsed_ms,
        }


@dataclass(frozen=True)
class OracleReplayCursor:
    cursor_revision: int
    invocation_progress: tuple[OracleInvocationProgress, ...]
    completed_invocations: tuple[LogicalInvocationKey, ...] = ()
    completed_tools: tuple[FrozenToolKey, ...] = ()
    satisfied_joins: tuple[FrozenJoinKey, ...] = ()

    def __post_init__(self) -> None:
        _require_int(self.cursor_revision, "cursor_revision")
        _require_nonnegative(self.cursor_revision, "cursor_revision")
        for name in (
            "invocation_progress",
            "completed_invocations",
            "completed_tools",
            "satisfied_joins",
        ):
            if type(getattr(self, name)) is not tuple:
                raise TypeError(f"{name} must be a tuple")
        progress = tuple(
            sorted(self.invocation_progress, key=lambda item: item.logical_key)
        )
        completed_invocations = tuple(sorted(self.completed_invocations))
        completed_tools = tuple(sorted(self.completed_tools))
        satisfied_joins = tuple(sorted(self.satisfied_joins))
        if len({item.logical_key for item in progress}) != len(progress):
            raise ValueError("cursor contains duplicate invocation progress")
        if len(set(completed_invocations)) != len(completed_invocations):
            raise ValueError("cursor contains duplicate completed invocations")
        if len(set(completed_tools)) != len(completed_tools):
            raise ValueError("cursor contains duplicate completed tools")
        if len(set(satisfied_joins)) != len(satisfied_joins):
            raise ValueError("cursor contains duplicate satisfied JOINs")
        object.__setattr__(self, "invocation_progress", progress)
        object.__setattr__(self, "completed_invocations", completed_invocations)
        object.__setattr__(self, "completed_tools", completed_tools)
        object.__setattr__(self, "satisfied_joins", satisfied_joins)

    def progress_for(
        self,
        logical_key: LogicalInvocationKey,
    ) -> OracleInvocationProgress:
        for item in self.invocation_progress:
            if item.logical_key == logical_key:
                return item
        raise KeyError("cursor has no progress for logical invocation")

    def to_dict(self) -> dict[str, object]:
        return {
            "cursor_revision": self.cursor_revision,
            "invocation_progress": [
                item.to_dict() for item in self.invocation_progress
            ],
            "completed_invocations": [
                item.to_dict() for item in self.completed_invocations
            ],
            "completed_tools": [item.to_dict() for item in self.completed_tools],
            "satisfied_joins": [item.to_dict() for item in self.satisfied_joins],
        }


@dataclass(frozen=True)
class FrozenInvocationDemand:
    key: LogicalInvocationKey
    agent_definition_id: str
    relation: FrozenInvocationRelation
    context_mode: FrozenContextMode
    parent: LogicalInvocationKey | None
    semantic_owner: LogicalInvocationKey
    calls: tuple[FrozenLLMCallDemand, ...]
    tools: tuple[FrozenToolDemand, ...] = ()

    def __post_init__(self) -> None:
        if type(self.relation) is not FrozenInvocationRelation:
            raise TypeError("relation must be FrozenInvocationRelation")
        if type(self.context_mode) is not FrozenContextMode:
            raise TypeError("context_mode must be FrozenContextMode")
        _require_text(self.agent_definition_id, "agent_definition_id")
        if type(self.calls) is not tuple or type(self.tools) is not tuple:
            raise TypeError("invocation calls and tools must be tuples")
        calls = tuple(sorted(self.calls, key=lambda item: item.call_ordinal))
        tools = tuple(sorted(self.tools, key=lambda item: item.tool_ordinal))
        if not calls:
            raise ValueError("frozen invocation must contain at least one LLM call")
        if len({item.call_ordinal for item in calls}) != len(calls):
            raise ValueError("LLM call ordinals must be unique within an invocation")
        if len({item.tool_ordinal for item in tools}) != len(tools):
            raise ValueError("tool ordinals must be unique within an invocation")
        if self.relation == FrozenInvocationRelation.ROOT and self.parent is not None:
            raise ValueError("root invocation must not have a parent")
        if self.relation != FrozenInvocationRelation.ROOT and self.parent is None:
            raise ValueError("non-root invocation must have a parent")
        if self.parent is not None and (
            self.parent.workload_instance != self.key.workload_instance
        ):
            raise ValueError("parent and child must belong to the same workload")
        if self.semantic_owner.workload_instance != self.key.workload_instance:
            raise ValueError("semantic owner must belong to the same workload")

        call_ordinals = {item.call_ordinal for item in calls}
        referenced_tools = {
            ordinal for call in calls for ordinal in call.boundary.tool_ordinals
        }
        actual_tools = {item.tool_ordinal for item in tools}
        if referenced_tools != actual_tools:
            raise ValueError("TOOL boundaries and frozen tool demand must match exactly")
        boundary_by_tool = {
            ordinal: call.call_ordinal
            for call in calls
            for ordinal in call.boundary.tool_ordinals
        }
        if any(item.starts_after_call_ordinal not in call_ordinals for item in tools):
            raise ValueError("tool demand references an unknown LLM call")
        if any(
            boundary_by_tool[item.tool_ordinal] != item.starts_after_call_ordinal
            for item in tools
        ):
            raise ValueError("tool demand is attached to the wrong action boundary")
        if calls[-1].boundary.kind not in {
            FrozenActionBoundaryKind.RETURN,
            FrozenActionBoundaryKind.FINAL,
        }:
            raise ValueError("last LLM call must end in RETURN or FINAL")
        object.__setattr__(self, "calls", calls)
        object.__setattr__(self, "tools", tools)

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key.to_dict(),
            "agent_definition_id": self.agent_definition_id,
            "relation": self.relation.value,
            "context_mode": self.context_mode.value,
            "parent": self.parent.to_dict() if self.parent is not None else None,
            "semantic_owner": self.semantic_owner.to_dict(),
            "calls": [item.to_dict() for item in self.calls],
            "tools": [item.to_dict() for item in self.tools],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "FrozenInvocationDemand":
        _strict_fields(
            raw,
            required=frozenset(
                {
                    "key",
                    "agent_definition_id",
                    "relation",
                    "context_mode",
                    "parent",
                    "semantic_owner",
                    "calls",
                    "tools",
                }
            ),
            object_name="FrozenInvocationDemand",
        )
        key = raw["key"]
        owner = raw["semantic_owner"]
        parent = raw["parent"]
        calls = _require_list(raw["calls"], "calls")
        tools = _require_list(raw["tools"], "tools")
        if not isinstance(key, Mapping) or not isinstance(owner, Mapping):
            raise TypeError("invocation key and semantic owner must be objects")
        if parent is not None and not isinstance(parent, Mapping):
            raise TypeError("invocation parent must be an object or null")
        if any(not isinstance(item, Mapping) for item in calls):
            raise TypeError("each call must be an object")
        if any(not isinstance(item, Mapping) for item in tools):
            raise TypeError("each tool must be an object")
        return cls(
            key=LogicalInvocationKey.from_dict(key),
            agent_definition_id=_require_str(
                raw["agent_definition_id"], "agent_definition_id"
            ),
            relation=FrozenInvocationRelation(
                _require_str(raw["relation"], "invocation relation")
            ),
            context_mode=FrozenContextMode(
                _require_str(raw["context_mode"], "context_mode")
            ),
            parent=LogicalInvocationKey.from_dict(parent) if parent is not None else None,
            semantic_owner=LogicalInvocationKey.from_dict(owner),
            calls=tuple(FrozenLLMCallDemand.from_dict(item) for item in calls),
            tools=tuple(FrozenToolDemand.from_dict(item) for item in tools),
        )


@dataclass(frozen=True)
class FrozenJoinDemand:
    key: FrozenJoinKey
    mode: FrozenJoinMode
    members: tuple[LogicalInvocationKey, ...]
    waiters: tuple[LogicalInvocationKey, ...]

    def __post_init__(self) -> None:
        if type(self.mode) is not FrozenJoinMode:
            raise TypeError("JOIN mode must be FrozenJoinMode")
        if type(self.members) is not tuple or type(self.waiters) is not tuple:
            raise TypeError("JOIN members and waiters must be tuples")
        members = tuple(sorted(self.members))
        waiters = tuple(sorted(self.waiters))
        if not members or not waiters:
            raise ValueError("frozen JOIN requires members and waiters")
        if len(set(members)) != len(members) or len(set(waiters)) != len(waiters):
            raise ValueError("JOIN members and waiters must be unique")
        if any(
            item.workload_instance != self.key.workload_instance
            for item in members + waiters
        ):
            raise ValueError("JOIN participants must belong to the same workload")
        object.__setattr__(self, "members", members)
        object.__setattr__(self, "waiters", waiters)

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key.to_dict(),
            "mode": self.mode.value,
            "members": [item.to_dict() for item in self.members],
            "waiters": [item.to_dict() for item in self.waiters],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "FrozenJoinDemand":
        _strict_fields(
            raw,
            required=frozenset({"key", "mode", "members", "waiters"}),
            object_name="FrozenJoinDemand",
        )
        key = raw["key"]
        members = _require_list(raw["members"], "JOIN members")
        waiters = _require_list(raw["waiters"], "JOIN waiters")
        if not isinstance(key, Mapping):
            raise TypeError("JOIN key must be an object")
        if any(
            not isinstance(item, Mapping)
            for item in tuple(members) + tuple(waiters)
        ):
            raise TypeError("JOIN participants must be objects")
        return cls(
            key=FrozenJoinKey.from_dict(key),
            mode=FrozenJoinMode(_require_str(raw["mode"], "JOIN mode")),
            members=tuple(LogicalInvocationKey.from_dict(item) for item in members),
            waiters=tuple(LogicalInvocationKey.from_dict(item) for item in waiters),
        )


@dataclass(frozen=True)
class FrozenDemandProvenance:
    truth_id: str
    source_trace_id: str
    workload_manifest_id: str
    model_revision: str
    tokenizer_revision: str
    runtime_revision: str
    harness_revision: str
    exporter_revision: str

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            _require_text(getattr(self, name), name)

    def to_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "FrozenDemandProvenance":
        names = frozenset(cls.__dataclass_fields__)
        _strict_fields(raw, required=names, object_name="FrozenDemandProvenance")
        return cls(**{name: _require_str(raw[name], name) for name in names})


@dataclass(frozen=True)
class FrozenAgentDemand:
    provenance: FrozenDemandProvenance
    invocations: tuple[FrozenInvocationDemand, ...]
    joins: tuple[FrozenJoinDemand, ...] = ()
    schema_version: int = FROZEN_AGENT_DEMAND_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_int(self.schema_version, "schema_version")
        if self.schema_version != FROZEN_AGENT_DEMAND_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported frozen demand schema version: {self.schema_version}"
            )
        if type(self.invocations) is not tuple or type(self.joins) is not tuple:
            raise TypeError("frozen invocations and joins must be tuples")
        invocations = tuple(sorted(self.invocations, key=lambda item: item.key))
        joins = tuple(sorted(self.joins, key=lambda item: item.key))
        if not invocations:
            raise ValueError("FrozenAgentDemand must contain at least one invocation")
        keys = {item.key for item in invocations}
        if len(keys) != len(invocations):
            raise ValueError("FrozenAgentDemand contains duplicate logical invocation keys")
        join_keys = {item.key for item in joins}
        if len(join_keys) != len(joins):
            raise ValueError("FrozenAgentDemand contains duplicate JOIN keys")

        by_key = {item.key: item for item in invocations}
        by_workload: dict[str, list[FrozenInvocationDemand]] = {}
        child_ordinals: set[tuple[LogicalInvocationKey, int]] = set()
        for item in invocations:
            by_workload.setdefault(item.key.workload_instance, []).append(item)
            if item.parent is not None:
                if item.parent not in by_key:
                    raise ValueError("FrozenAgentDemand contains an orphan child")
                ordinal_key = (item.parent, item.key.parent_spawn_ordinal)
                if ordinal_key in child_ordinals:
                    raise ValueError("parent child ordinal must be unique")
                child_ordinals.add(ordinal_key)
            if item.semantic_owner not in by_key:
                raise ValueError("semantic owner is not present in frozen demand")
            owner = by_key[item.semantic_owner]
            if owner.semantic_owner != owner.key or owner.context_mode not in {
                FrozenContextMode.FRESH,
                FrozenContextMode.FORK,
            }:
                raise ValueError("semantic owner must be a self-owned physical context")
            if item.context_mode in {
                FrozenContextMode.FRESH,
                FrozenContextMode.FORK,
            } and item.semantic_owner != item.key:
                raise ValueError("FRESH/FORK invocation must own its context")
            if item.context_mode == FrozenContextMode.RESUME:
                if item.parent is None or item.semantic_owner == item.key:
                    raise ValueError("RESUME invocation must reuse a parent-owned context")
                if item.semantic_owner != by_key[item.parent].semantic_owner:
                    raise ValueError("RESUME invocation must preserve parent semantic owner")
            if item.relation == FrozenInvocationRelation.HANDOFF and (
                item.context_mode != FrozenContextMode.RESUME
            ):
                raise ValueError("HANDOFF invocation must use RESUME context mode")
            if item.relation == FrozenInvocationRelation.ROOT and (
                item.context_mode != FrozenContextMode.FRESH
                or item.semantic_owner != item.key
            ):
                raise ValueError("root invocation must be a self-owned FRESH context")
            for call in item.calls:
                for target in call.boundary.target_invocations:
                    if target not in by_key:
                        raise ValueError("action boundary targets an unknown invocation")
                for join_key in call.boundary.joins:
                    if join_key not in join_keys:
                        raise ValueError("action boundary references an unknown JOIN")

        for workload, items in by_workload.items():
            roots = [item for item in items if item.parent is None]
            if len(roots) != 1:
                raise ValueError(
                    f"workload {workload} must contain exactly one root invocation"
                )
            if roots[0].calls[-1].boundary.kind != FrozenActionBoundaryKind.FINAL:
                raise ValueError("workflow root must terminate with FINAL")

        relation_boundary = {
            FrozenInvocationRelation.CALL: FrozenActionBoundaryKind.CALL,
            FrozenInvocationRelation.SPAWN: FrozenActionBoundaryKind.SPAWN,
            FrozenInvocationRelation.MESSAGE: FrozenActionBoundaryKind.MESSAGE,
            FrozenInvocationRelation.HANDOFF: FrozenActionBoundaryKind.HANDOFF,
        }
        for item in invocations:
            if item.parent is None:
                continue
            expected_kind = relation_boundary[item.relation]
            parent = by_key[item.parent]
            matching_edges = [
                call
                for call in parent.calls
                if call.boundary.kind == expected_kind
                and item.key in call.boundary.target_invocations
            ]
            if len(matching_edges) != 1:
                raise ValueError(
                    "each non-root invocation must have exactly one matching creation edge"
                )
            if item.context_mode in {
                FrozenContextMode.FRESH,
                FrozenContextMode.FORK,
            }:
                terminal = item.calls[-1].boundary
                if (
                    terminal.kind != FrozenActionBoundaryKind.RETURN
                    or terminal.target_invocations != (item.parent,)
                ):
                    raise ValueError(
                        "FRESH/FORK child must RETURN exactly to its parent"
                    )

        creation_kinds = {
            FrozenActionBoundaryKind.CALL: FrozenInvocationRelation.CALL,
            FrozenActionBoundaryKind.SPAWN: FrozenInvocationRelation.SPAWN,
            FrozenActionBoundaryKind.HANDOFF: FrozenInvocationRelation.HANDOFF,
        }
        for source in invocations:
            for call in source.calls:
                expected_relation = creation_kinds.get(call.boundary.kind)
                if expected_relation is None:
                    continue
                for target_key in call.boundary.target_invocations:
                    target = by_key[target_key]
                    if (
                        target.parent != source.key
                        or target.relation != expected_relation
                    ):
                        raise ValueError(
                            "creation boundary target has inconsistent parent/relation"
                        )

        for join in joins:
            if join.key.owner not in by_key:
                raise ValueError("JOIN owner is not present in frozen demand")
            if any(item not in by_key for item in join.members + join.waiters):
                raise ValueError("JOIN contains an unknown participant")
            for waiter in join.waiters:
                boundary_refs = sum(
                    1
                    for call in by_key[waiter].calls
                    if join.key in call.boundary.joins
                )
                if boundary_refs != 1:
                    raise ValueError(
                        "each JOIN waiter must reference the JOIN exactly once"
                    )
        for invocation in invocations:
            for call in invocation.calls:
                for join_key in call.boundary.joins:
                    join = next(item for item in joins if item.key == join_key)
                    if invocation.key not in join.waiters:
                        raise ValueError("JOIN boundary caller is not a declared waiter")

        for start in keys:
            seen: set[LogicalInvocationKey] = set()
            current: LogicalInvocationKey | None = start
            while current is not None:
                if current in seen:
                    raise ValueError("invocation parent relation contains a cycle")
                seen.add(current)
                current = by_key[current].parent
            roots = [
                key
                for key in seen
                if by_key[key].parent is None
            ]
            if len(roots) != 1:
                raise ValueError("invocation parent chain does not close at one root")

        object.__setattr__(self, "invocations", invocations)
        object.__setattr__(self, "joins", joins)

    @property
    def truth_id(self) -> str:
        return self.provenance.truth_id

    @property
    def truth_digest(self) -> str:
        return sha256(self.canonical_bytes()).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "provenance": self.provenance.to_dict(),
            "invocations": [item.to_dict() for item in self.invocations],
            "joins": [item.to_dict() for item in self.joins],
        }

    def canonical_bytes(self) -> bytes:
        return (
            json.dumps(
                self.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "FrozenAgentDemand":
        _strict_fields(
            raw,
            required=frozenset(
                {"schema_version", "provenance", "invocations", "joins"}
            ),
            object_name="FrozenAgentDemand",
        )
        provenance = raw["provenance"]
        invocations = _require_list(raw["invocations"], "invocations")
        joins = _require_list(raw["joins"], "joins")
        if not isinstance(provenance, Mapping):
            raise TypeError("provenance must be an object")
        if any(
            not isinstance(item, Mapping)
            for item in tuple(invocations) + tuple(joins)
        ):
            raise TypeError("invocations and joins must contain objects")
        return cls(
            schema_version=_require_int(raw["schema_version"], "schema_version"),
            provenance=FrozenDemandProvenance.from_dict(provenance),
            invocations=tuple(
                FrozenInvocationDemand.from_dict(item) for item in invocations
            ),
            joins=tuple(FrozenJoinDemand.from_dict(item) for item in joins),
        )

    @classmethod
    def from_json_bytes(cls, payload: bytes) -> "FrozenAgentDemand":
        raw = json.loads(payload, object_pairs_hook=_reject_duplicate_json_keys)
        if not isinstance(raw, Mapping):
            raise TypeError("frozen demand payload must contain a JSON object")
        return cls.from_dict(raw)
