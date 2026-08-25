from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from beliefkv.oracle.contracts import (
    FrozenActionBoundaryKind,
    LogicalInvocationKey,
    OracleReplayCursor,
)
from beliefkv.oracle.truth_provider import (
    AgentFutureField,
    AgentRemainingDemand,
    KVFutureField,
    OracleTruthProvider,
)
from beliefkv.policy.reference.base import ResidencyAction


@dataclass(frozen=True)
class OracleReadyRequest:
    request_id: str
    logical_key: LogicalInvocationKey
    call_ordinal: int
    prompt_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class OracleSemanticResidency:
    context_id: str
    context_epoch: int
    action: ResidencyAction
    deadline_ms: float
    reason: str
    target_bytes_hint: int = 0
    beneficiary_request_id: str | None = None
    required_reclaim_bytes: int = 0
    service_deadline_ms: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "context_id": self.context_id,
            "context_epoch": self.context_epoch,
            "action": self.action.value,
            "target_bytes_hint": self.target_bytes_hint,
            "deadline_ms": self.deadline_ms,
            "reason": self.reason,
            "beneficiary_request_id": self.beneficiary_request_id,
            "required_reclaim_bytes": self.required_reclaim_bytes,
            "service_deadline_ms": self.service_deadline_ms,
        }


@dataclass(frozen=True)
class OracleJointDirective:
    directive_id: str
    sequence: int
    replay_id: str
    truth_id: str
    truth_digest: str
    ordered_request_ids: tuple[str, ...]
    semantic_residency: tuple[OracleSemanticResidency, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "directive_id": self.directive_id,
            "sequence": self.sequence,
            "replay_id": self.replay_id,
            "truth_id": self.truth_id,
            "truth_digest": self.truth_digest,
            "ordered_request_ids": list(self.ordered_request_ids),
            "semantic_residency": [
                item.to_dict() for item in self.semantic_residency
            ],
        }


class PerfectFutureJointPlanner:
    """Compile perfect-future semantic evidence into one JointPlan directive.

    The planner never binds Radix handles. The scheduler rematerializes at its
    safe point and falls back to the observed seed if the live state changed.
    """

    _BOUNDARY_PRIORITY = {
        FrozenActionBoundaryKind.RETURN: 0,
        FrozenActionBoundaryKind.FINAL: 0,
        FrozenActionBoundaryKind.SPAWN: 1,
        FrozenActionBoundaryKind.CALL: 1,
        FrozenActionBoundaryKind.HANDOFF: 1,
        FrozenActionBoundaryKind.MESSAGE: 1,
        FrozenActionBoundaryKind.TOOL: 2,
        FrozenActionBoundaryKind.JOIN: 3,
        FrozenActionBoundaryKind.CONTINUE: 4,
    }

    def __init__(
        self,
        provider: OracleTruthProvider,
        *,
        replay_id: str,
    ) -> None:
        self.provider = provider
        self.replay_id = replay_id
        self._sequence = 0

    def compile(
        self,
        *,
        cursor: OracleReplayCursor,
        ready: Mapping[str, OracleReadyRequest],
        residency: tuple[OracleSemanticResidency, ...] = (),
    ) -> OracleJointDirective:
        planner_epoch = self._sequence
        ranked = sorted(
            ready.values(),
            key=lambda item: self._request_rank(
                item,
                cursor=cursor,
                planner_epoch=planner_epoch,
            ),
        )
        sequence = self._sequence
        self._sequence += 1
        return OracleJointDirective(
            directive_id=f"{self.replay_id}:directive:{sequence}",
            sequence=sequence,
            replay_id=self.replay_id,
            truth_id=self.provider.truth_id,
            truth_digest=self.provider.truth_digest,
            ordered_request_ids=tuple(item.request_id for item in ranked),
            semantic_residency=residency,
        )

    def has_future_reuse(
        self,
        logical_key: LogicalInvocationKey,
        *,
        cursor: OracleReplayCursor,
    ) -> bool:
        return bool(
            self.provider.kv_future.query(
                planner_epoch=self._sequence,
                logical_key=logical_key,
                cursor=cursor,
                field=KVFutureField.FUTURE_REUSE,
                reason="gate proactive residency on exact future context reuse",
            )
        )

    def no_future_use(
        self,
        logical_key: LogicalInvocationKey,
        *,
        cursor: OracleReplayCursor,
    ) -> bool:
        proof = self.provider.kv_future.query(
            planner_epoch=self._sequence,
            logical_key=logical_key,
            cursor=cursor,
            field=KVFutureField.NO_FUTURE_USE_PROOF,
            reason="drop only context owners with an exact no-future-use proof",
        )
        return bool(getattr(proof, "proven", False))

    def _request_rank(
        self,
        request: OracleReadyRequest,
        *,
        cursor: OracleReplayCursor,
        planner_epoch: int,
    ) -> tuple[int, int, int, str]:
        boundary = self.provider.agent_future.query(
            planner_epoch=planner_epoch,
            logical_key=request.logical_key,
            cursor=cursor,
            field=AgentFutureField.NEXT_ACTION_BOUNDARY,
            reason="order ready requests by exact next causal unlock",
        )
        remaining = self.provider.agent_future.query(
            planner_epoch=planner_epoch,
            logical_key=request.logical_key,
            cursor=cursor,
            field=AgentFutureField.REMAINING_DEMAND,
            reason="break causal-unlock ties by exact remaining local demand",
        )
        assert isinstance(remaining, AgentRemainingDemand)
        boundary_kind = (
            boundary[1].kind
            if isinstance(boundary, tuple) and len(boundary) == 2
            else FrozenActionBoundaryKind.CONTINUE
        )
        return (
            self._BOUNDARY_PRIORITY[boundary_kind],
            request.prompt_tokens + request.output_tokens,
            remaining.prompt_tokens + remaining.decode_tokens,
            request.request_id,
        )
