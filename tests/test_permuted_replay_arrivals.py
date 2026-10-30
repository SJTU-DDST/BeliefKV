import asyncio
import time

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay import NativeReplayCompletion, execute_causal_replay
from beliefkv.experiments.replay_arrivals import (
    arrival_offsets_ms, permuted_poisson_arrival_plan, validate_arrival_plan,
)
from beliefkv.experiments.workload_mix import burst_arrivals
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from tests.test_causal_replay_arrivals import two_workflows


def test_population_permutation_and_poisson_draws_are_independent():
    identities = [str(i) for i in range(18)]
    first = permuted_poisson_arrival_plan(
        identities, rate_per_second=.01, seed=22, permutation_seed=21, initial_workflows=5,
    )
    second = permuted_poisson_arrival_plan(
        identities, rate_per_second=.01, seed=22, permutation_seed=23, initial_workflows=5,
    )
    assert first["arrival_schedule"] != second["arrival_schedule"]
    assert [row["offset_seconds"] for row in first["arrival_schedule"]] == [
        row["offset_seconds"] for row in second["arrival_schedule"]
    ]
    assert validate_arrival_plan(first, list(reversed(identities))) == first
    first["arrival_schedule"][0]["workflow_id"], first["arrival_schedule"][1]["workflow_id"] = (
        first["arrival_schedule"][1]["workflow_id"], first["arrival_schedule"][0]["workflow_id"],
    )
    with pytest.raises(ValueError):
        validate_arrival_plan(first, identities)


@pytest.mark.parametrize("order", (("w", "b"), ("b", "w")))
def test_simultaneous_actual_workflow_start_delivery_honors_frozen_permutation(order):
    plan = two_workflows()
    arrivals = burst_arrivals(order)
    observed = []

    async def emit(event):
        observed.append(event)

    async def serve(request, _submit):
        return NativeReplayCompletion(
            request, time.monotonic() * 1000., local_monotonic_clock_domain(),
            *plan.request_usage[request],
        )

    result = asyncio.run(execute_causal_replay(
        plan, serve, emit, workflow_arrival_offsets_ms=arrival_offsets_ms(arrivals),
        workflow_release_order=order,
    ))
    starts = [event for event in observed if event.kind is Kind.WORKFLOW_START]
    assert [event.workflow_id for event in starts] == list(order)
    assert starts[0].ts_ms == starts[1].ts_ms
    assert result["completed_requests"] == len(plan.request_usage)
    assert result["workflow_release_order"] == list(order)
    assert result["replay_timing"]["shifts"] == []


@pytest.mark.parametrize("order", ((), ("w",), ("w", "w"), ("w", "other")))
def test_incomplete_or_repeated_release_order_fails_before_delivery(order):
    async def unused(*_args):
        pytest.fail("invalid order must not release anything")

    with pytest.raises(ValueError):
        asyncio.run(execute_causal_replay(
            two_workflows(), unused, unused, workflow_arrival_offsets_ms={"w": 0., "b": 0.},
            workflow_release_order=order,
        ))
