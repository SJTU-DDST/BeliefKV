"""Frozen external workflow arrivals, independent of serving and model seeds."""

import json
import math
from pathlib import Path
import random

from beliefkv.experiments.arrival_schedule import (
    arrival_schedule_digest, build_poisson_workflow_arrivals,
)


def poisson_arrival_plan(workflow_ids, *, rate_per_second, seed, initial_workflows=0):
    identities = list(workflow_ids)
    if (
        not identities or len(set(identities)) != len(identities)
        or any(type(identity) is not str or not identity for identity in identities)
        or type(rate_per_second) not in (int, float)
    ):
        raise ValueError("arrival plan requires unique workflow identities and a numeric rate")
    arrivals = build_poisson_workflow_arrivals(
        len(identities), rate_per_second=rate_per_second, seed=seed,
        initial_workflows=initial_workflows,
    )
    rows = [
        {"workflow_id": identity, "offset_seconds": arrival.scheduled_offset_seconds}
        for identity, arrival in zip(identities, arrivals)
    ]
    return {
        "schema_version": 2 if initial_workflows else 1,
        "mode": "warm_start_poisson" if initial_workflows else "poisson",
        **({"initial_workflows": initial_workflows} if initial_workflows else {}),
        "rate_per_second": rate_per_second, "seed": seed,
        "arrival_schedule": rows, "arrival_schedule_sha256": arrival_schedule_digest(rows),
        "jct_boundary": "scheduled_workflow_arrival_to_workflow_end",
    }


def permuted_poisson_arrival_plan(
    workflow_ids, *, rate_per_second, seed, permutation_seed, initial_workflows,
):
    if type(permutation_seed) is not int or permutation_seed < 0:
        raise ValueError("arrival permutation requires a nonnegative independent seed")
    identities = sorted(workflow_ids)
    random.Random(permutation_seed).shuffle(identities)
    plan = poisson_arrival_plan(
        identities, rate_per_second=rate_per_second, seed=seed,
        initial_workflows=initial_workflows,
    )
    if initial_workflows <= 0:
        raise ValueError("permuted warm arrivals require an initial population")
    return {
        **plan, "schema_version": 4, "mode": "permuted_warm_start_poisson",
        "permutation_seed": permutation_seed,
    }


def validate_arrival_plan(plan, workflow_ids):
    identities = list(workflow_ids)
    if (plan.get("schema_version"), plan.get("mode")) == (3, "simultaneous"):
        from beliefkv.experiments.workload_mix import burst_arrivals

        rows = plan.get("arrival_schedule", ())
        ordered_ids = [row.get("workflow_id") for row in rows]
        if (
            len(identities) != len(ordered_ids)
            or set(identities) != set(ordered_ids)
            or plan != burst_arrivals(ordered_ids)
        ):
            raise ValueError("burst arrivals differ from the declared workflow population")
        return plan
    if (plan.get("schema_version"), plan.get("mode")) == (4, "permuted_warm_start_poisson"):
        expected = permuted_poisson_arrival_plan(
            identities, rate_per_second=plan.get("rate_per_second"), seed=plan.get("seed"),
            permutation_seed=plan.get("permutation_seed"),
            initial_workflows=plan.get("initial_workflows"),
        )
        if plan != expected:
            raise ValueError("permuted arrivals differ from the seeded population and slots")
        return expected
    if (
        (plan.get("schema_version"), plan.get("mode")) not in (
            (1, "poisson"), (2, "warm_start_poisson"),
        )
        or plan.get("jct_boundary") != "scheduled_workflow_arrival_to_workflow_end"
    ):
        raise ValueError("unsupported replay arrival contract")
    initial = plan.get("initial_workflows", 0)
    if plan["mode"] == "warm_start_poisson" and (
        type(initial) is not int or initial <= 0
    ):
        raise ValueError("warm-start arrivals require a positive initial population")
    expected = poisson_arrival_plan(
        identities, rate_per_second=plan.get("rate_per_second"), seed=plan.get("seed"),
        initial_workflows=initial,
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
