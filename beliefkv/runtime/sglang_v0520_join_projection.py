"""Pure, bounded JOIN reentry timing hints from calibrated child completions.

Pointwise max/min of marginal quantiles is a scheduling hint, not a calibrated
quantile of the joint completion time. No independence or correlation model is
assumed; in particular the ALL P90 is not a guaranteed upper bound.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Mapping

from beliefkv.control.causal_graph import InvocationState, JoinMode
from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.predictor.structured_frontier import EmpiricalDistribution
from beliefkv.runtime.sglang_v0520_prediction import MAX_TOOL_WAIT_MS


MAX_JOIN_CHILDREN = 8
MAX_JOIN_WAIT_MS = MAX_TOOL_WAIT_MS
MAX_JOIN_ID_LENGTH = 256
CHILD_TIMING_SOURCE = "child_completion.remaining_to_return_ms"
QUANTILE_CAVEAT = (
    "Marginal quantile envelope only: dependence is unknown; these are not "
    "calibrated joint quantiles or upper/lower coverage guarantees."
)
_SUPPORT_RANK = {"pooled": 0, "global": 1, "backoff": 2, "role": 3, "exact": 4}


def _identifier(value: object, name: str) -> None:
    if type(value) is not str or not value or len(value) > MAX_JOIN_ID_LENGTH:
        raise ValueError(f"invalid {name}")


def _ids(values: object, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if type(values) not in (tuple, list) or (
        not allow_empty and not values
    ) or len(values) > MAX_JOIN_CHILDREN:
        raise ValueError(f"invalid {name}")
    for value in values:
        _identifier(value, name)
    if len(set(values)) != len(values):
        raise ValueError(f"duplicate {name}")
    return tuple(values)


def _mode(value: object) -> JoinMode:
    if isinstance(value, JoinMode):
        return value
    if type(value) is str and value.lower() in ("all", "any"):
        return JoinMode(value.lower())
    raise ValueError("invalid join mode")


def _satisfied(mode: JoinMode, members: tuple[str, ...], pending: tuple[str, ...]) -> bool:
    return not pending if mode is JoinMode.ALL else len(pending) < len(members)


@dataclass(frozen=True)
class JoinProjectionState:
    workflow_id: str
    parent_invocation_id: str
    join_id: str
    mode: JoinMode
    member_child_ids: tuple[str, ...]
    pending_child_ids: tuple[str, ...]
    parent_state: InvocationState
    satisfied: bool

    def __post_init__(self) -> None:
        for name in ("workflow_id", "parent_invocation_id", "join_id"):
            _identifier(getattr(self, name), name)
        if type(self.member_child_ids) is not tuple or type(self.pending_child_ids) is not tuple:
            raise ValueError("JOIN child IDs must be immutable tuples")
        members = _ids(self.member_child_ids, "member_child_ids")
        pending = _ids(self.pending_child_ids, "pending_child_ids", allow_empty=True)
        if (
            type(self.satisfied) is not bool
            or not isinstance(self.mode, JoinMode)
            or not isinstance(self.parent_state, InvocationState)
            or self.parent_state not in {
                InvocationState.CREATED, InvocationState.WAIT_JOIN, InvocationState.READY
            }
            or self.parent_invocation_id in members
            or not set(pending) <= set(members)
            or self.satisfied != _satisfied(self.mode, members, pending)
            or (self.parent_state is InvocationState.WAIT_JOIN and self.satisfied)
            or (self.parent_state is InvocationState.READY and not self.satisfied)
        ):
            raise ValueError("JOIN state/algebra mismatch")


def join_state_from_create(
    event: RuntimeEvent,
    *,
    parent_invocation_id: str,
    completed_child_ids: tuple[str, ...] = (),
) -> JoinProjectionState:
    """Capture a JOIN_CREATE; caller supplies already-terminal members, if any."""
    if event.kind is not RuntimeEventKind.JOIN_CREATE:
        raise ValueError("expected JOIN_CREATE")
    members = _ids(event.member_invocation_ids, "member_child_ids")
    completed = _ids(completed_child_ids, "completed_child_ids", allow_empty=True)
    if not set(completed) <= set(members):
        raise ValueError("completed child is not a JOIN member")
    pending = tuple(child for child in members if child not in completed)
    mode = _mode(event.attributes.get("mode", "all"))
    return JoinProjectionState(
        event.workflow_id, parent_invocation_id, event.join_id, mode,
        members, pending, InvocationState.CREATED, _satisfied(mode, members, pending),
    )


def advance_join_state(state: JoinProjectionState, event: RuntimeEvent) -> JoinProjectionState:
    """Apply JOIN_WAIT or a member RETURN without mutating the source graph."""
    if event.workflow_id != state.workflow_id:
        raise ValueError("JOIN workflow mismatch")
    if event.kind is RuntimeEventKind.JOIN_WAIT:
        if (
            event.join_id != state.join_id
            or event.invocation_id != state.parent_invocation_id
            or state.parent_state is not InvocationState.CREATED
        ):
            raise ValueError("JOIN_WAIT identity/state mismatch")
        return replace(
            state, parent_state=(
                InvocationState.READY if state.satisfied else InvocationState.WAIT_JOIN
            ),
        )
    if event.kind is RuntimeEventKind.RETURN:
        if (
            event.invocation_id not in state.member_child_ids
            or (event.join_id is not None and event.join_id != state.join_id)
        ):
            raise ValueError("RETURN is not from this JOIN")
        if event.invocation_id not in state.pending_child_ids:
            return state  # The graph treats a repeated terminal RETURN as a no-op.
        pending = tuple(
            child for child in state.pending_child_ids if child != event.invocation_id
        )
        satisfied = _satisfied(state.mode, state.member_child_ids, pending)
        return replace(
            state, pending_child_ids=pending, satisfied=satisfied,
            parent_state=(
                InvocationState.READY
                if satisfied and state.parent_state is InvocationState.WAIT_JOIN
                else state.parent_state
            ),
        )
    raise ValueError("unsupported JOIN projection event")


@dataclass(frozen=True)
class ChildReturnPrediction:
    child_id: str
    remaining_to_return_ms: tuple[float, float, float] | EmpiricalDistribution
    support_level: str
    calibration_coverage: float
    state: InvocationState
    ood_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChildTimingProvenance:
    child_id: str
    source: str
    support_level: str
    calibration_coverage: float


@dataclass(frozen=True)
class JoinReentryHint:
    workflow_id: str
    parent_invocation_id: str
    join_id: str
    mode: JoinMode
    pending_child_ids: tuple[str, ...]
    p10_ms: float | None
    p50_ms: float | None
    p90_ms: float | None
    minimum_support: str
    missing_reasons: tuple[str, ...]
    provenance: tuple[ChildTimingProvenance, ...]
    quantile_caveat: str = QUANTILE_CAVEAT

    @property
    def available(self) -> bool:
        return self.p10_ms is not None


def _child_quantiles(prediction: ChildReturnPrediction) -> tuple[float, float, float]:
    dist = prediction.remaining_to_return_ms
    if isinstance(dist, EmpiricalDistribution):
        if (
            not dist.values or len(dist.values) != len(dist.probability_mass)
            or type(dist.support) not in (int, float)
            or not math.isfinite(dist.support) or dist.support <= 0
            or any(type(v) not in (int, float) or not math.isfinite(v)
                   or not 0 <= v <= MAX_JOIN_WAIT_MS for v in dist.values)
            or tuple(sorted(dist.values)) != dist.values
            or any(type(p) not in (int, float) or not math.isfinite(p) or p < 0
                   for p in dist.probability_mass)
            or not math.isclose(sum(dist.probability_mass), 1.0, rel_tol=1e-7, abs_tol=1e-7)
        ):
            raise ValueError("invalid empirical child duration")
        values = tuple(dist.quantile(q) for q in (0.1, 0.5, 0.9))
    elif type(dist) is tuple and len(dist) == 3:
        values = tuple(dist)
    else:
        raise ValueError("invalid child duration distribution")
    if (
        any(type(v) not in (int, float) or not math.isfinite(v)
            or not 0 <= v <= MAX_JOIN_WAIT_MS for v in values)
        or values != tuple(sorted(values))
    ):
        raise ValueError("invalid child duration quantiles")
    return values


def project_join_reentry(
    state: JoinProjectionState,
    predictions: Mapping[str, ChildReturnPrediction],
) -> JoinReentryHint:
    """Fail closed on partial, OOD or uncalibrated child completion coverage.

    Never substitute the parent's structural JOIN wait_belief for a duration.
    Inputs must be a current snapshot: terminal/stale or nonmember children are
    rejected, including predictions for children already returned.
    """
    if not isinstance(state, JoinProjectionState) or not isinstance(predictions, Mapping):
        raise ValueError("invalid JOIN projection input")
    if len(predictions) > MAX_JOIN_CHILDREN or set(predictions) - set(state.pending_child_ids):
        raise ValueError("prediction outside pending JOIN children")
    reasons: list[str] = []
    provenance: list[ChildTimingProvenance] = []
    quantiles: list[tuple[float, float, float]] = []
    levels: list[str] = []
    if state.satisfied:
        reasons.append("join_already_satisfied")
    elif state.parent_state is not InvocationState.WAIT_JOIN:
        reasons.append("parent_not_waiting")
    else:
        for child_id in state.pending_child_ids:
            prediction = predictions.get(child_id)
            if prediction is None:
                reasons.append(f"{child_id}:missing_prediction")
                continue
            if type(prediction) is not ChildReturnPrediction or prediction.child_id != child_id:
                raise ValueError("child prediction identity mismatch")
            if not isinstance(prediction.state, InvocationState) or prediction.state not in {
                InvocationState.CREATED, InvocationState.READY,
                InvocationState.RUNNING_LLM, InvocationState.WAIT_TOOL,
                InvocationState.WAIT_CHILD, InvocationState.WAIT_JOIN,
                InvocationState.WAIT_MESSAGE, InvocationState.RETURNING,
            }:
                reasons.append(f"{child_id}:child_not_pending")
                continue
            if prediction.ood_reasons:
                reasons.append(f"{child_id}:ood")
                continue
            if prediction.support_level not in _SUPPORT_RANK:
                reasons.append(f"{child_id}:unsupported")
                continue
            if (
                type(prediction.calibration_coverage) not in (int, float)
                or not math.isfinite(prediction.calibration_coverage)
                or not 0 < prediction.calibration_coverage <= 1
            ):
                reasons.append(f"{child_id}:uncalibrated")
                continue
            try:
                child_quantiles = _child_quantiles(prediction)
            except ValueError:
                reasons.append(f"{child_id}:invalid_duration")
                continue
            quantiles.append(child_quantiles)
            levels.append(prediction.support_level)
            provenance.append(ChildTimingProvenance(
                child_id, CHILD_TIMING_SOURCE, prediction.support_level,
                float(prediction.calibration_coverage),
            ))
    supported = not reasons and len(quantiles) == len(state.pending_child_ids)
    joined = (
        tuple(
            (max if state.mode is JoinMode.ALL else min)(child[q] for child in quantiles)
            for q in range(3)
        )
        if supported else (None, None, None)
    )
    return JoinReentryHint(
        state.workflow_id, state.parent_invocation_id, state.join_id, state.mode,
        state.pending_child_ids, *joined,
        min(levels, key=_SUPPORT_RANK.__getitem__) if supported else "unavailable",
        tuple(reasons), tuple(provenance),
    )
