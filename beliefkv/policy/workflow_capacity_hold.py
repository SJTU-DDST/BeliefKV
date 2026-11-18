"""Bounded metadata-only deferral of lower-priority new admissions."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class CapacityHoldCandidate:
    identity: str
    workflow: str
    logical_tokens: int
    base_weight: float
    weight: float
    ready_wait_ms: float
    sequence: int = 0
    protected: bool = False
    supported: bool = True


def choose_capacity_hold(
    candidates, reference, *, capacity, pool_capacity, minimum_wait_ms,
    running_width, relative_tie=.01, focus=None,
):
    """Return only a subset of S0; never grant eligibility or physical capacity."""
    candidates, reference = tuple(candidates), tuple(reference)
    by_id = {row.identity: row for row in candidates}
    if (
        not 1 <= len(candidates) <= 32 or len(by_id) != len(candidates)
        or len(reference) > 8 or len(set(reference)) != len(reference)
        or not set(reference) <= by_id.keys()
        or type(capacity) is not int or capacity < 0
        or type(pool_capacity) is not int or not 0 <= capacity <= pool_capacity
        or type(minimum_wait_ms) is not int or not 0 < minimum_wait_ms <= 60000
        or type(running_width) is not int or running_width < 0
        or type(relative_tie) not in (int, float)
        or not math.isfinite(relative_tie) or not 0 <= relative_tie < 1.
    ):
        raise ValueError("invalid bounded capacity accumulation snapshot")
    for row in candidates:
        if (
            any(type(value) is not str or not value for value in (row.identity, row.workflow))
            or type(row.logical_tokens) is not int or row.logical_tokens <= 0
            or type(row.sequence) is not int or row.sequence < 0
            or type(row.protected) is not bool or type(row.supported) is not bool
            or type(row.ready_wait_ms) not in (int, float)
            or not math.isfinite(row.ready_wait_ms) or row.ready_wait_ms < 0
            or any(type(value) not in (int, float)
                   or not math.isfinite(value) or not 0 <= value <= 1.
                   for value in (row.base_weight, row.weight))
        ):
            raise ValueError("invalid exact ready capacity candidate")
    if sum(by_id[owner].logical_tokens for owner in reference) > capacity:
        raise ValueError("the original reference must fit its logical budget")
    if running_width < 2 or not reference:
        return None
    options = candidates if focus is None else (by_id[focus],) if focus in by_id else ()
    choices = []
    for row in options:
        if (
            not row.supported or row.protected
            or not capacity < row.logical_tokens <= pool_capacity
            or row.ready_wait_ms < minimum_wait_ms
        ):
            continue
        if any(
            other.identity not in reference and other.logical_tokens <= capacity and (
                other.protected or other.workflow == row.workflow
                or other.supported and (
                    other.base_weight > row.base_weight * (1. + relative_tie)
                    or other.weight > row.weight * (1. + relative_tie)
                )
            ) for other in candidates
        ):
            continue
        held = tuple(
            owner for owner in reference if (
                (other := by_id[owner]).supported and not other.protected
                and other.workflow != row.workflow
                and row.base_weight > other.base_weight * (1. + relative_tie)
                and row.weight > other.weight * (1. + relative_tie)
            )
        )
        held_tokens = sum(by_id[owner].logical_tokens for owner in held)
        if not held or focus is None and held_tokens < row.logical_tokens - capacity:
            continue
        choices.append((row, held, held_tokens))
    if not choices:
        return None
    row, held, held_tokens = min(
        choices, key=lambda item: (-item[0].weight, item[0].sequence, item[0].identity),
    )
    return {
        "focus": row.identity,
        "selected": tuple(owner for owner in reference if owner not in held),
        "held_reference": held,
        "held_reference_tokens": held_tokens,
        "focus_logical_gap_tokens": row.logical_tokens - capacity,
        "continuing_existing_hold": focus is not None,
    }
