"""Frozen external workflow arrivals, independent of serving and model seeds."""

from copy import deepcopy
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


def extend_poisson_arrival_plan(prefix_plan, extra_workflow_ids):
    """Continue the original random stream without reshuffling its frozen prefix."""
    if not isinstance(prefix_plan, dict) or (
        prefix_plan.get("schema_version"), prefix_plan.get("mode")
    ) not in (
        (1, "poisson"), (2, "warm_start_poisson"), (4, "permuted_warm_start_poisson"),
        (5, "prefix_extended_poisson"), (6, "background_substituted_poisson"),
    ):
        raise ValueError("background extension requires one original Poisson arrival plan")
    rows, extra = prefix_plan.get("arrival_schedule", ()), list(extra_workflow_ids)
    identities = [row.get("workflow_id") for row in rows]
    if (
        not identities or not extra
        or any(type(key) is not str or not key for key in (*identities, *extra))
        or len(set(extra)) != len(extra) or set(identities) & set(extra)
    ):
        raise ValueError("background extension requires new unique workflow identities")
    validate_arrival_plan(prefix_plan, sorted(identities))
    continued = poisson_arrival_plan(
        [*identities, *extra], rate_per_second=prefix_plan["rate_per_second"],
        seed=prefix_plan["seed"], initial_workflows=prefix_plan.get("initial_workflows", 0),
    )
    if continued["arrival_schedule"][:len(rows)] != rows:
        raise ValueError("continued random stream changed an original arrival")
    return {
        **continued, "schema_version": 5, "mode": "prefix_extended_poisson",
        "prefix_plan": deepcopy(prefix_plan), "prefix_workflows": len(identities),
        "additional_workflow_ids": extra,
    }


def substitute_background_arrivals(
    original_plan, replacements, *, preserved_prefix_workflows,
):
    """Replace complete background identities while retaining every arrival time."""
    if not isinstance(original_plan, dict) or (
        original_plan.get("schema_version"), original_plan.get("mode")
    ) not in ((1, "poisson"), (2, "warm_start_poisson"), (4, "permuted_warm_start_poisson")):
        raise ValueError("background substitution requires one original Poisson arrival plan")
    rows = original_plan.get("arrival_schedule", ())
    identities = [row.get("workflow_id") for row in rows]
    validate_arrival_plan(original_plan, identities)
    if (
        type(preserved_prefix_workflows) is not int
        or not max(1, original_plan.get("initial_workflows", 0))
        <= preserved_prefix_workflows < len(identities)
        or not isinstance(replacements, (list, tuple)) or not replacements
    ):
        raise ValueError("declare a fixed foreground prefix and nonempty background replacements")
    protected = set(identities[:preserved_prefix_workflows])
    replaced, added = {}, set()
    for row in replacements:
        if not isinstance(row, dict) or set(row) != {"old_workflow_id", "new_workflow_id"}:
            raise ValueError("each substitution needs exactly one old and one new workflow identity")
        old, new = row["old_workflow_id"], row["new_workflow_id"]
        if (
            type(old) is not str or old not in identities or old in protected or old in replaced
            or type(new) is not str or not new or new in identities or new in added
        ):
            raise ValueError("background substitution must preserve foreground and unique new identities")
        replaced[old] = new
        added.add(new)
    substituted = [replaced.get(key, key) for key in identities]
    continued = poisson_arrival_plan(
        substituted, rate_per_second=original_plan["rate_per_second"],
        seed=original_plan["seed"], initial_workflows=original_plan.get("initial_workflows", 0),
    )
    if any(before["offset_seconds"] != after["offset_seconds"] for before, after in zip(
        rows, continued["arrival_schedule"],
    )) or continued["arrival_schedule"][:preserved_prefix_workflows] != rows[:preserved_prefix_workflows]:
        raise ValueError("background substitution changed a frozen arrival slot")
    return {
        **continued, "schema_version": 6, "mode": "background_substituted_poisson",
        "original_plan": deepcopy(original_plan),
        "preserved_prefix_workflows": preserved_prefix_workflows,
        "replacements": [
            {"old_workflow_id": old, "new_workflow_id": replaced[old]}
            for old in identities if old in replaced
        ],
    }


def validate_arrival_plan(plan, workflow_ids):
    identities = list(workflow_ids)
    if (plan.get("schema_version"), plan.get("mode")) == (6, "background_substituted_poisson"):
        expected = substitute_background_arrivals(
            plan.get("original_plan"), plan.get("replacements"),
            preserved_prefix_workflows=plan.get("preserved_prefix_workflows"),
        )
        if (
            len(identities) != len(set(identities))
            or set(identities) != {row["workflow_id"] for row in expected["arrival_schedule"]}
            or plan != expected
        ):
            raise ValueError("substituted arrivals changed their fixed foreground, times or population")
        return expected
    if (plan.get("schema_version"), plan.get("mode")) == (5, "prefix_extended_poisson"):
        expected = extend_poisson_arrival_plan(
            plan.get("prefix_plan"), plan.get("additional_workflow_ids", ()),
        )
        if (
            len(identities) != len(set(identities))
            or set(identities) != {row["workflow_id"] for row in expected["arrival_schedule"]}
            or plan != expected
        ):
            raise ValueError("extended arrivals changed their original prefix or declared population")
        return expected
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
