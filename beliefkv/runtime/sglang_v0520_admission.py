"""Semantic prefill ordering for the v0.5.20 native FULL+MAMBA allocator.

This module never estimates KV capacity, matches a prefix, or triggers a
transfer. The scheduler applies its result before Req.init_next_round_input;
PrefillAdder remains the sole authority for resource admission and load-back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, TypeVar


ReqT = TypeVar("ReqT")


@dataclass(frozen=True)
class PrefillCandidateKey:
    request_id: str
    root_workflow_id: str
    invocation_id: str
    context_id: str
    context_epoch: int
    attempt_id: int


@dataclass(frozen=True)
class NativePrefillPlan:
    semantic_revision: int
    prioritized: tuple[PrefillCandidateKey, ...]


@dataclass(frozen=True)
class NativePrefillSelection:
    candidates: tuple[object, ...]
    rejected: tuple[tuple[str, str], ...]


def _request_key(req: object) -> PrefillCandidateKey | None:
    metadata = getattr(req, "beliefkv_metadata", None)
    handle = getattr(req, "cache_request_handle", None)
    request_id = getattr(req, "rid", None)
    if type(metadata) is not dict or type(request_id) is not str or not request_id:
        return None
    fields = ("root_workflow_id", "invocation_id", "context_id")
    values = tuple(metadata.get(field) for field in fields)
    epoch = metadata.get("context_epoch")
    attempt = getattr(handle, "attempt_id", None)
    if (
        any(type(value) is not str or not value for value in values)
        or type(epoch) is not int
        or epoch < 0
        or type(attempt) is not int
        or attempt < 0
    ):
        return None
    return PrefillCandidateKey(request_id, *values, epoch, attempt)


def select_native_prefill_candidates(
    native_order: Sequence[ReqT],
    *,
    plan: NativePrefillPlan,
    current_semantic_revision: int,
    max_candidates: int = 512,
) -> NativePrefillSelection:
    """Keep untagged native positions and reorder only authorized tagged slots.

    Missing/stale authorizations reject tagged requests, never native traffic.
    The plan must be built at this scheduler safe point; if it is stale, a new
    plan is required rather than interpreting an old capacity certificate.
    """
    if type(max_candidates) is not int or max_candidates <= 0:
        raise ValueError("max_candidates must be positive")
    if type(current_semantic_revision) is not int or current_semantic_revision < 0:
        raise ValueError("invalid semantic revision")
    if type(plan.semantic_revision) is not int or plan.semantic_revision < 0:
        raise ValueError("invalid plan revision")
    if len(plan.prioritized) > max_candidates:
        raise ValueError("prefill plan exceeds bound")
    planned = {key.request_id: key for key in plan.prioritized}
    if len(planned) != len(plan.prioritized):
        raise ValueError("duplicate request ID in prefill plan")

    permitted: dict[str, ReqT] = {}
    rejected: list[tuple[str, str]] = []
    seen: set[str] = set()
    stale = plan.semantic_revision != current_semantic_revision
    over_limit = len(native_order) > max_candidates
    for req in native_order:
        metadata = getattr(req, "beliefkv_metadata", None)
        if metadata is None:
            continue
        request_id = getattr(req, "rid", None)
        if type(request_id) is not str or not request_id:
            raise ValueError("tagged request has no request ID")
        if request_id in seen:
            raise ValueError("duplicate tagged request ID in native queue")
        seen.add(request_id)
        key = _request_key(req)
        reason = (
            "candidate_bound"
            if over_limit
            else "stale_revision"
            if stale
            else "invalid_identity"
            if key is None
            else "no_authorization"
            if request_id not in planned
            else "identity_changed"
            if planned[request_id] != key
            else None
        )
        if reason is None:
            permitted[request_id] = req
        else:
            rejected.append((request_id, reason))

    ordered = iter(
        permitted[key.request_id]
        for key in plan.prioritized
        if key.request_id in permitted
    )
    candidates = tuple(
        req if getattr(req, "beliefkv_metadata", None) is None else next(ordered)
        for req in native_order
        if getattr(req, "beliefkv_metadata", None) is None
        or getattr(req, "rid", None) in permitted
    )
    return NativePrefillSelection(candidates=candidates, rejected=tuple(rejected))
