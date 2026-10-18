"""Runtime transfer timing and residency budgets from current native capacity."""

from __future__ import annotations

from dataclasses import dataclass
import math

from beliefkv.runtime.native_transfer_service import NativeServiceEstimate


@dataclass(frozen=True)
class TransferStartWindow:
    service_ms: float
    enqueue_ms: float
    horizon_ms: float
    prediction_early_ms: float = 0.
    service_ceiling_bytes: int | None = None
    service_ceiling_shape: str | None = None
    planning_slack_ms: float = 0.
    minimum_advance_ms: float = 0.


def transfer_start_window(
    estimate: NativeServiceEstimate, *, max_lead_ms: float, observation_spacing_ms: float,
    prediction_early_ms: float = 0., planning_slack_ms: float = 0.,
    minimum_advance_ms: float = 0.,
) -> TransferStartWindow | None:
    """Include early completion uncertainty within the residence allowance."""
    service_ms = estimate.submit_to_ack_p90_ms
    enqueue_ms = estimate.enqueue_to_submit_p90_ms or 0.
    if not all(math.isfinite(value) and value >= 0 for value in (
        service_ms, enqueue_ms, max_lead_ms, observation_spacing_ms, prediction_early_ms,
        planning_slack_ms, minimum_advance_ms,
    )):
        return None
    return TransferStartWindow(
        service_ms, enqueue_ms,
        min(
            max_lead_ms,
            max(
                minimum_advance_ms,
                service_ms + enqueue_ms + observation_spacing_ms
                + prediction_early_ms + planning_slack_ms,
            ),
        ),
        prediction_early_ms,
        planning_slack_ms=planning_slack_ms,
        minimum_advance_ms=minimum_advance_ms,
    )


@dataclass(frozen=True)
class PrefetchResidencyBudget:
    request_slots: int
    byte_limit: int
    reserve_full_tokens: int = 0
    reserve_mamba_slots: int = 0
    source: str = "legacy_capacity_unavailable"
    evictable_full_tokens: int = 0
    evictable_mamba_slots: int = 0


def native_residency_budget(
    *,
    running_requests: int,
    max_running_requests: int,
    available_request_rows: int,
    prefill_slots: int,
    page_size: int,
    input_token_reserve: int,
    full_free_tokens: int,
    mamba_free_slots: int,
    full_bytes_per_token: int,
    mamba_bytes_per_slot: int,
    protected_bytes: int,
    evictable_full_tokens: int = 0,
    evictable_mamba_slots: int = 0,
    restore_frontier_slots: int = 1,
    occupied_request_rows: int | None = None,
) -> PrefetchResidencyBudget:
    # Prefill rows are allocated before joining the decode running list. Count
    # those rows in growth reserves and the bounded rowless cache frontier;
    # native request admission still needs a real free row.
    active = running_requests
    if (
        running_requests > 0 and type(occupied_request_rows) is int
        and running_requests <= occupied_request_rows <= max_running_requests
    ):
        active = occupied_request_rows
    rows = max(0, available_request_rows)
    if rows == 0 and active >= max_running_requests > 0:
        rows = restore_frontier_slots
    slots = max(0, min(
        rows, prefill_slots,
        max(restore_frontier_slots, max_running_requests - active),
    ))
    full_reserve = max(0, input_token_reserve) + max(1, page_size) * (
        active + slots
    )
    mamba_reserve = slots
    byte_limit = max(0, protected_bytes) + (
        max(0, full_free_tokens + evictable_full_tokens - full_reserve) * full_bytes_per_token
        + max(0, mamba_free_slots + evictable_mamba_slots - mamba_reserve) * mamba_bytes_per_slot
    )
    return PrefetchResidencyBudget(
        slots, byte_limit, full_reserve, mamba_reserve, "native_next_prefill",
        evictable_full_tokens, evictable_mamba_slots,
    )
