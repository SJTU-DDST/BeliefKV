"""Frozen external workflow arrivals, independent of serving and model seeds."""

import json
import math
from pathlib import Path

from beliefkv.experiments.arrival_schedule import (
    arrival_schedule_digest, build_poisson_workflow_arrivals,
)


def poisson_arrival_plan(workflow_ids, *, rate_per_second, seed):
    identities = list(workflow_ids)
    if (
        not identities or len(set(identities)) != len(identities)
        or any(type(identity) is not str or not identity for identity in identities)
        or type(rate_per_second) not in (int, float)
    ):
        raise ValueError("arrival plan requires unique workflow identities and a numeric rate")
    arrivals = build_poisson_workflow_arrivals(
        len(identities), rate_per_second=rate_per_second, seed=seed,
    )
    rows = [
        {"workflow_id": identity, "offset_seconds": arrival.scheduled_offset_seconds}
        for identity, arrival in zip(identities, arrivals)
    ]
    return {
        "schema_version": 1, "mode": "poisson",
        "rate_per_second": rate_per_second, "seed": seed,
        "arrival_schedule": rows, "arrival_schedule_sha256": arrival_schedule_digest(rows),
        "jct_boundary": "scheduled_workflow_arrival_to_workflow_end",
    }


def validate_arrival_plan(plan, workflow_ids):
    identities = list(workflow_ids)
    if (
        plan.get("schema_version") != 1 or plan.get("mode") != "poisson"
        or plan.get("jct_boundary") != "scheduled_workflow_arrival_to_workflow_end"
    ):
        raise ValueError("unsupported replay arrival contract")
    expected = poisson_arrival_plan(
        identities, rate_per_second=plan.get("rate_per_second"), seed=plan.get("seed"),
    )
    if plan != expected:
        raise ValueError("arrival identities, seeded times or digest differ from the frozen plan")
    return expected


def load_arrival_plan(path: Path, workflow_ids):
    return validate_arrival_plan(json.loads(path.read_bytes()), workflow_ids)


def arrival_offsets_ms(plan):
    return {
        row["workflow_id"]: row["offset_seconds"] * 1000.
        for row in plan["arrival_schedule"]
    }


def validate_offsets(plan, offsets):
    """Check the entire release map before delivering any online event."""
    starts = [
        node for node in plan.nodes
        if node.event is not None and node.event.kind.value == "workflow_start"
    ]
    workflows = {node.workflow_id for node in plan.nodes}
    if (
        set(offsets) != workflows or len(starts) != len(workflows)
        or {node.workflow_id for node in starts} != workflows
        or any(node.dependencies for node in starts)
        or any(
            type(value) not in (int, float) or not math.isfinite(value) or value < 0
            for value in offsets.values()
        )
    ):
        raise ValueError("arrivals must cover each independent workflow exactly once")
