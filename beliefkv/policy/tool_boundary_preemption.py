"""Switch-cost-aware two-job screening, not an optimal workflow JCT bound."""

import math
from dataclasses import dataclass
from itertools import combinations


KIND = "tool_boundary_remaining_model_work_pair_v1"
BOUNDED_KIND = "tool_boundary_cost_bounded_schedule_v2"


def pair_gain_ms(
    donor_work, consumer_work, *, model_seconds_per_work_token,
    backup_ms, consumer_restore_ms, donor_restore_ms, margin_ms,
):
    values = (
        donor_work, consumer_work, model_seconds_per_work_token,
        backup_ms, consumer_restore_ms, donor_restore_ms, margin_ms,
    )
    if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
           for value in values) or model_seconds_per_work_token == 0:
        raise ValueError("tool-boundary work and switch costs must be finite and nonnegative")
    # In a serial two-job model, price the current tool as already returned.
    # Delaying A's backup also delays B, so D2H enters the completion sum twice.
    return (
        (donor_work - consumer_work) * model_seconds_per_work_token * 1000.
        - 2 * backup_ms - consumer_restore_ms - donor_restore_ms - margin_ms
    )


@dataclass(frozen=True)
class BoundaryDonor:
    key: object
    workflow: str
    remaining_work: float
    logical_tokens: int
    physical_tokens: int
    backup_ms: float
    restore_ms: float
    migration_tokens: int


@dataclass(frozen=True)
class BoundaryPlan:
    donors: tuple
    gain_ms: float
    migration_tokens: int
    logical_tokens: int
    physical_tokens: int
    backup_ms: float
    restore_ms: float


def choose_boundary_plan(
    donors, *, consumer_workflow, consumer_work, logical_deficit, physical_deficit,
    consumer_restore_ms, consumer_migration_tokens, model_seconds_per_work_token,
    margin_ms=400., min_work_ratio=1.5, max_donors=2, migration_allowance=None,
    committed_donors=(),
):
    """Bounded local screening; a plan conveys no native release authority."""
    if type(max_donors) is not int or not 1 <= max_donors <= 2 or len(donors) > 8:
        raise ValueError("tool-boundary search supports eight candidates and at most two donors")
    values = (
        consumer_work, consumer_restore_ms, model_seconds_per_work_token, margin_ms, min_work_ratio,
    )
    if any(type(x) not in (int, float) or not math.isfinite(x) or x < 0 for x in values):
        raise ValueError("invalid work, cost or hysteresis")
    if model_seconds_per_work_token <= 0 or min_work_ratio < 1:
        raise ValueError("require positive service cost and a strict work gap")
    for value in (logical_deficit, physical_deficit, consumer_migration_tokens):
        if type(value) is not int or value < 0:
            raise ValueError("capacity and migration demand must be nonnegative integers")
    if migration_allowance is not None and (
        type(migration_allowance) is not int or migration_allowance < 0
    ):
        raise ValueError("invalid migration allowance")
    if logical_deficit == physical_deficit == 0:
        return None
    committed = set(committed_donors)
    if len(committed) > 2:
        raise ValueError("an unfinished handover cannot accumulate more than two victims")
    seen, eligible = set(), []
    for donor in donors:
        if donor.key in seen:
            raise ValueError("a physical owner cannot be counted twice")
        seen.add(donor.key)
        costs = donor.remaining_work, donor.backup_ms, donor.restore_ms
        if any(type(x) not in (int, float) or not math.isfinite(x) or x < 0 for x in costs):
            raise ValueError("invalid donor work or transfer costs")
        if any(type(x) is not int or x < 0 for x in (
            donor.logical_tokens, donor.physical_tokens, donor.migration_tokens,
        )):
            raise ValueError("invalid donor capacity")
        if (
            donor.workflow != consumer_workflow
            and donor.remaining_work > consumer_work * min_work_ratio
            and donor.physical_tokens > 0
        ):
            eligible.append(donor)
    # One ready request does not justify displacing a second owner when a
    # positive single-owner handover is already possible.
    for size in range(1, max_donors + 1):
        best = None
        for group in combinations(eligible, size):
            if len(committed | {item.key for item in group}) > 2:
                continue
            logical = sum(item.logical_tokens for item in group)
            physical = sum(item.physical_tokens for item in group)
            migration = consumer_migration_tokens + sum(item.migration_tokens for item in group)
            if (
                logical < logical_deficit or physical < physical_deficit
                or migration_allowance is not None and migration > migration_allowance
            ):
                continue
            backup = sum(item.backup_ms for item in group)
            restore = sum(item.restore_ms for item in group)
            # For a group, charge B's service against every displaced owner.
            # This conservative extension is still a proxy, not a JCT bound.
            gain = (
                (min(item.remaining_work for item in group) - size * consumer_work)
                * model_seconds_per_work_token * 1000.
                - (size + 1) * backup - size * consumer_restore_ms - restore - margin_ms
            )
            if gain <= 0:
                continue
            plan = BoundaryPlan(
                tuple(item.key for item in group), gain, migration,
                logical, physical, backup, restore,
            )
            if best is None or (gain, -migration) > (best.gain_ms, -best.migration_tokens):
                best = plan
        if best is not None:
            return best
    return None
