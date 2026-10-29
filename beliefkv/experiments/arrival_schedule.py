from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import random
from collections.abc import Sequence


@dataclass(frozen=True)
class WorkflowArrival:
    workflow_index: int
    batch_index: int
    position_in_batch: int
    scheduled_offset_seconds: float


def build_poisson_workflow_arrivals(
    workflow_count: int, *, rate_per_second: float, seed: int,
    initial_workflows: int = 0,
) -> tuple[WorkflowArrival, ...]:
    if workflow_count <= 0:
        raise ValueError("workflow_count must be positive")
    if not math.isfinite(rate_per_second) or rate_per_second <= 0:
        raise ValueError("Poisson arrival rate must be finite and positive")
    if type(seed) is not int or seed < 0:
        raise ValueError("arrival seed must be a non-negative integer")
    if type(initial_workflows) is not int or not 0 <= initial_workflows <= workflow_count:
        raise ValueError("initial workflows must be an integer within the population")
    generator = random.Random(seed)
    offset = 0.0
    arrivals = []
    for index in range(workflow_count):
        if index >= initial_workflows:
            offset += generator.expovariate(rate_per_second)
        arrivals.append(WorkflowArrival(index, index, 0, offset))
    return tuple(arrivals)


def arrival_schedule_digest(rows: list[dict]) -> str:
    return hashlib.sha256(json.dumps(
        rows, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def load_workflow_arrivals(
    path: Path, *, instance_ids: Sequence[str],
) -> tuple[WorkflowArrival, ...]:
    plan = json.loads(path.read_bytes())
    rows = plan["arrival_schedule"]
    if (
        not isinstance(rows, list) or len(rows) != len(instance_ids)
        or len(set(instance_ids)) != len(instance_ids)
    ):
        raise ValueError("frozen arrivals must cover the selected workflows exactly")
    arrivals = []
    previous = 0.0
    for index, (row, instance_id) in enumerate(zip(rows, instance_ids)):
        offset = row.get("offset_seconds")
        if (
            row.get("instance_id") != instance_id
            or type(offset) not in (int, float)
            or not math.isfinite(offset) or offset < previous
        ):
            raise ValueError("frozen arrivals must preserve task order and finite monotonic times")
        arrivals.append(WorkflowArrival(index, index, 0, float(offset)))
        previous = offset
    expected_digest = plan.get("arrival_schedule_sha256")
    if expected_digest is not None and expected_digest != arrival_schedule_digest(rows):
        raise ValueError("frozen arrival schedule digest changed")
    return tuple(arrivals)


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
