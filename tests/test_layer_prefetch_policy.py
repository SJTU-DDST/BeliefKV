import random

import pytest

from beliefkv.runtime.layer_prefetch_policy import (
    LayerCost, LayerCostProfile, LayerPrefetchBudget, LeadTimeScenario,
    choose_prefetch_depth,
)


def simulate(profile, depth, *, lead, backlog):
    clock = backlog
    ready = []
    for index, layer in enumerate(profile.layers):
        if index == depth:
            clock = max(clock, lead)
        clock += layer.transfer_ms
        ready.append(clock)
    clock = lead
    if profile.before_forward_layer is not None:
        clock = max(clock, ready[profile.before_forward_layer])
    clock += profile.before_forward_copy_ms
    for layer, arrival in zip(profile.layers, ready):
        clock = max(clock, arrival) + layer.compute_ms
    return clock


def profile(*, barrier=None, transfer_ms=1., compute_ms=4.):
    return LayerCostProfile.build(
        tuple(LayerCost(1024, 0, transfer_ms, compute_ms) for _ in range(40)),
        allocation_bytes=40960, before_forward_layer=barrier,
    )


def budget(**overrides):
    return LayerPrefetchBudget(**{
        "allocation_bytes": 40960, "transfer_bytes": 40960,
        "transfer_ms": 1000., "transfer_shadow_price": .1, **overrides,
    })


def test_cached_envelopes_match_an_independent_layer_by_layer_simulator():
    rng = random.Random(21)
    for _ in range(160):
        layers = tuple(LayerCost(
            rng.randrange(100), rng.randrange(100),
            rng.random() * 5, rng.random() * 4,
        ) for _ in range(rng.randrange(1, 45)))
        costs = LayerCostProfile.build(
            layers, allocation_bytes=10000,
            before_forward_layer=rng.choice([None, rng.randrange(len(layers))]),
            before_forward_copy_ms=rng.random(),
        )
        lead, backlog = rng.random() * 50, rng.random() * 20
        for depth in range(len(layers) + 1):
            assert costs.completion_ms(
                depth, lead_ms=lead, backlog_ms=backlog,
            ) == pytest.approx(simulate(costs, depth, lead=lead, backlog=backlog))


def test_native_overlap_and_the_state_barrier_require_different_depths():
    inputs = (LeadTimeScenario(50., 1.),)
    full = choose_prefetch_depth(profile(), inputs, budget=budget())
    state = choose_prefetch_depth(profile(barrier=38), inputs, budget=budget())
    assert full.depth == 1
    assert state.depth == 39
    assert state.conditional_latency_saved_ms == pytest.approx(39.)
    # Later layers cannot be assumed hidden when transfers outpace compute.
    slow = choose_prefetch_depth(
        profile(transfer_ms=4., compute_ms=1.), inputs, budget=budget(),
    )
    assert slow.depth == 13
    assert slow.conditional_latency_saved_ms == pytest.approx(50.)


def test_partial_layers_reserve_the_entire_native_page_allocation():
    costs = profile()
    inputs = (LeadTimeScenario(50., 1.),)
    rejected = choose_prefetch_depth(costs, inputs, budget=budget(allocation_bytes=2000))
    assert rejected.depth == 0
    assert rejected.reason == "allocation_budget"
    decision = choose_prefetch_depth(costs, inputs, budget=budget(transfer_bytes=1024))
    assert decision.depth == 1
    assert decision.transfer_bytes == 1024
    assert decision.allocation_bytes == 40960


@pytest.mark.parametrize("overrides", [
    {"backlog_ms": 100.}, {"continuation_probability": 0.},
    {"control_ms": 100.}, {"transfer_shadow_price": 100.},
    {"residency_price_ms_per_gib_second": 1e9},
])
def test_costs_and_no_available_advance_can_choose_no_prefetch(overrides):
    decision = choose_prefetch_depth(
        profile(), (LeadTimeScenario(10., 1.),), budget=budget(**overrides),
    )
    assert decision.depth == 0
    assert decision.expected_net_saved_ms == 0.


def test_decision_matches_exhaustive_resource_constrained_local_oracle():
    rng = random.Random(33)
    for _ in range(80):
        costs = profile(
            barrier=rng.choice([None, 38]),
            transfer_ms=.1 + rng.random() * 3, compute_ms=.1 + rng.random(),
        )
        scenarios = (
            LeadTimeScenario(rng.random() * 50, .2),
            LeadTimeScenario(rng.random() * 50, .5),
            LeadTimeScenario(rng.random() * 50, .3),
        )
        resources = budget(
            transfer_bytes=1024 * rng.randrange(1, 41),
            transfer_ms=rng.random() * 100, backlog_ms=rng.random() * 20,
            continuation_probability=rng.random(), control_ms=.01,
            residency_price_ms_per_gib_second=.1,
        )
        baseline = [
            simulate(costs, 0, lead=item.lead_ms, backlog=resources.backlog_ms)
            for item in scenarios
        ]
        expected = [(0., 0)]
        for depth in range(1, 41):
            size = sum(item.full_bytes + item.state_bytes for item in costs.layers[:depth])
            service = sum(item.transfer_ms for item in costs.layers[:depth])
            if size > resources.transfer_bytes or service > resources.transfer_ms:
                continue
            gain = sum(
                item.probability * (base - simulate(
                    costs, depth, lead=item.lead_ms, backlog=resources.backlog_ms,
                )) for item, base in zip(scenarios, baseline)
            )
            score = (
                resources.continuation_probability * gain
                - (1 - resources.continuation_probability + resources.transfer_shadow_price)
                * service - resources.control_ms
                - costs.allocation_bytes / 1024 ** 3
                * sum(item.probability * item.lead_ms for item in scenarios) / 1000.
                * resources.residency_price_ms_per_gib_second
            )
            expected.append((score, depth))
        best = max(expected, key=lambda item: (item[0], -item[1]))
        decision = choose_prefetch_depth(costs, scenarios, budget=resources)
        assert decision.expected_net_saved_ms == pytest.approx(best[0])
        assert decision.depth == best[1]


@pytest.mark.parametrize("scenarios", [
    (), (LeadTimeScenario(1., .5),), (LeadTimeScenario(1., 0.),),
    (LeadTimeScenario(1., .1),) * 10,
])
def test_scenario_count_and_normalization_are_bounded(scenarios):
    with pytest.raises(ValueError):
        choose_prefetch_depth(profile(), scenarios, budget=budget())


@pytest.mark.parametrize("overrides", [
    {"transfer_ms": float("inf")}, {"transfer_bytes": -1},
    {"backlog_ms": float("nan")}, {"control_ms": -1.},
    {"continuation_probability": 1.1},
])
def test_invalid_resource_estimates_are_rejected(overrides):
    with pytest.raises(ValueError):
        budget(**overrides)
