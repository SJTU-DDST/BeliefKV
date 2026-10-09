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


def transfer_start_window(
    estimate: NativeServiceEstimate, *, max_lead_ms: float, observation_spacing_ms: float,
) -> TransferStartWindow | None:
    service_ms = estimate.submit_to_ack_p90_ms
    enqueue_ms = estimate.enqueue_to_submit_p90_ms or 0.
    if (
        not all(math.isfinite(value) and value >= 0 for value in (
            service_ms, enqueue_ms, max_lead_ms, observation_spacing_ms,
        ))
        or service_ms + enqueue_ms > max_lead_ms
    ):
        return None
    return TransferStartWindow(
        service_ms, enqueue_ms,
        min(max_lead_ms, service_ms + enqueue_ms + observation_spacing_ms),
    )


@dataclass(frozen=True)
class PrefetchResidencyBudget:
    request_slots: int
    byte_limit: int
    reserve_full_tokens: int = 0
    reserve_mamba_slots: int = 0
    source: str = "legacy_capacity_unavailable"


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
) -> PrefetchResidencyBudget:
    # A full decode batch may overlap one frontier restore with its next
    # completion. This allowance never substitutes for native admission.
    slots = max(0, min(
        available_request_rows, prefill_slots,
        max(1, max_running_requests - running_requests),
    ))
    full_reserve = max(0, input_token_reserve) + max(1, page_size) * (
        running_requests + slots
    )
    mamba_reserve = slots
    byte_limit = max(0, protected_bytes) + (
        max(0, full_free_tokens - full_reserve) * full_bytes_per_token
        + max(0, mamba_free_slots - mamba_reserve) * mamba_bytes_per_slot
    )
    return PrefetchResidencyBudget(
        slots, byte_limit, full_reserve, mamba_reserve, "native_next_prefill",
    )
