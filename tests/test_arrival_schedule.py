from __future__ import annotations

import json
import random

import pytest

from beliefkv.experiments.arrival_schedule import (
    arrival_schedule_digest,
    build_poisson_workflow_arrivals,
    build_workflow_arrivals,
    group_arrivals,
    load_workflow_arrivals,
)


def test_batched_arrivals_form_reproducible_waves() -> None:
    arrivals = build_workflow_arrivals(
        7,
        mode="batched",
        batch_size=3,
        batch_interval_seconds=12.5,
    )

    assert [item.scheduled_offset_seconds for item in arrivals] == [
        0.0,
        0.0,
        0.0,
        12.5,
        12.5,
        12.5,
        25.0,
    ]
    assert [len(batch) for batch in group_arrivals(arrivals)] == [3, 3, 1]


def test_batched_arrivals_support_monotonic_intra_wave_stagger() -> None:
    arrivals = build_workflow_arrivals(
        6,
        mode="batched",
        batch_size=3,
        batch_interval_seconds=20.0,
        intra_batch_interval_seconds=0.5,
    )

    assert [item.scheduled_offset_seconds for item in arrivals] == [
        0.0,
        0.5,
        1.0,
        20.0,
        20.5,
        21.0,
    ]


def test_batched_arrivals_reject_overlapping_wave_starts() -> None:
    with pytest.raises(ValueError, match="final intra-batch"):
        build_workflow_arrivals(
            6,
            mode="batched",
            batch_size=3,
            batch_interval_seconds=0.5,
            intra_batch_interval_seconds=0.5,
        )


def test_simultaneous_arrivals_ignore_batch_interval() -> None:
    arrivals = build_workflow_arrivals(
        4,
        mode="simultaneous",
        batch_size=1,
        batch_interval_seconds=30,
    )

    assert {item.batch_index for item in arrivals} == {0}
    assert {item.scheduled_offset_seconds for item in arrivals} == {0.0}


@pytest.mark.parametrize(
    ("workflow_count", "mode", "batch_size", "interval"),
    (
        (0, "batched", 1, 1.0),
        (1, "invalid", 1, 1.0),
        (1, "batched", 0, 1.0),
        (1, "batched", 1, -1.0),
    ),
)
def test_invalid_arrival_policy_is_rejected(
    workflow_count: int,
    mode: str,
    batch_size: int,
    interval: float,
) -> None:
    with pytest.raises(ValueError):
        build_workflow_arrivals(
            workflow_count,
            mode=mode,
            batch_size=batch_size,
            batch_interval_seconds=interval,
        )


def test_poisson_arrivals_are_frozen_independently_of_model_randomness():
    expected = build_poisson_workflow_arrivals(156, rate_per_second=1 / 15, seed=21)
    generator_state = random.getstate()
    try:
        random.seed(100)
        for _ in range(100):
            random.random()
        actual = build_poisson_workflow_arrivals(156, rate_per_second=1 / 15, seed=21)
    finally:
        random.setstate(generator_state)
    assert actual == expected
    offsets = [arrival.scheduled_offset_seconds for arrival in actual]
    assert 0 < offsets[0]
    assert all(left < right for left, right in zip(offsets, offsets[1:]))
    assert [arrival.workflow_index for arrival in actual] == list(range(156))
    assert build_poisson_workflow_arrivals(156, rate_per_second=1 / 15, seed=22) != actual


@pytest.mark.parametrize("count,rate,seed", (
    (0, .1, 21), (1, 0., 21), (1, -.1, 21),
    (1, float("inf"), 21), (1, float("nan"), 21), (1, .1, -1),
))
def test_invalid_poisson_configuration_is_rejected(count, rate, seed):
    with pytest.raises(ValueError):
        build_poisson_workflow_arrivals(count, rate_per_second=rate, seed=seed)


def test_frozen_schedule_binds_exact_task_order_and_detects_changed_times(tmp_path):
    rows = [
        {"instance_id": "first", "offset_seconds": 2.},
        {"instance_id": "second", "offset_seconds": 5.},
    ]
    path = tmp_path / "plan.json"
    plan = {"arrival_schedule": rows, "arrival_schedule_sha256": arrival_schedule_digest(rows)}
    path.write_text(json.dumps(plan))
    arrivals = load_workflow_arrivals(path, instance_ids=("first", "second"))
    assert [row.scheduled_offset_seconds for row in arrivals] == [2., 5.]
    with pytest.raises(ValueError, match="task order"):
        load_workflow_arrivals(path, instance_ids=("second", "first"))
    with pytest.raises(ValueError, match="exactly"):
        load_workflow_arrivals(path, instance_ids=("first",))
    rows[1]["offset_seconds"] = 6.
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="digest changed"):
        load_workflow_arrivals(path, instance_ids=("first", "second"))


@pytest.mark.parametrize("offsets", ((-1., 2.), (2., 1.), (0., float("nan")), (False, 1.)))
def test_frozen_schedule_rejects_invalid_clock_values(tmp_path, offsets):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"arrival_schedule": [
        {"instance_id": instance, "offset_seconds": offset}
        for instance, offset in zip(("first", "second"), offsets)
    ]}))
    with pytest.raises(ValueError, match="finite monotonic"):
        load_workflow_arrivals(path, instance_ids=("first", "second"))
