from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowArrival:
    workflow_index: int
    batch_index: int
    position_in_batch: int
    scheduled_offset_seconds: float


def build_workflow_arrivals(
    workflow_count: int,
    *,
    mode: str,
    batch_size: int,
    batch_interval_seconds: float,
    intra_batch_interval_seconds: float = 0.0,
) -> tuple[WorkflowArrival, ...]:
    if workflow_count <= 0:
        raise ValueError("workflow_count must be positive")
    if mode not in {"simultaneous", "batched"}:
        raise ValueError("arrival mode must be simultaneous or batched")
    if batch_size <= 0:
        raise ValueError("arrival batch size must be positive")
    if batch_interval_seconds < 0:
        raise ValueError("arrival batch interval must be non-negative")
    if intra_batch_interval_seconds < 0:
        raise ValueError("intra-batch arrival interval must be non-negative")

    effective_batch_size = workflow_count if mode == "simultaneous" else batch_size
    interval = 0.0 if mode == "simultaneous" else batch_interval_seconds
    intra_interval = 0.0 if mode == "simultaneous" else intra_batch_interval_seconds
    batch_count = (workflow_count + effective_batch_size - 1) // effective_batch_size
    if (
        mode == "batched"
        and batch_count > 1
        and interval < (effective_batch_size - 1) * intra_interval
    ):
        raise ValueError(
            "arrival batch interval must not precede the final intra-batch arrival"
        )
    return tuple(
        WorkflowArrival(
            workflow_index=index,
            batch_index=index // effective_batch_size,
            position_in_batch=index % effective_batch_size,
            scheduled_offset_seconds=(index // effective_batch_size) * interval
            + (index % effective_batch_size) * intra_interval,
        )
        for index in range(workflow_count)
    )


def group_arrivals(
    arrivals: tuple[WorkflowArrival, ...],
) -> tuple[tuple[WorkflowArrival, ...], ...]:
    if not arrivals:
        return ()
    batches: dict[int, list[WorkflowArrival]] = {}
    for arrival in arrivals:
        batches.setdefault(arrival.batch_index, []).append(arrival)
    return tuple(
        tuple(sorted(batch, key=lambda item: item.position_in_batch))
        for _, batch in sorted(batches.items())
    )
