import math

import pytest

from beliefkv.policy.paper_baselines import (
    ConcurConfig, ConcurWindow, ContinuumTTL, Intercept, ThunderWorkingSet,
    continuum_priority, continuum_public_priority, continuum_public_ttl,
    infercept_chunk_discard_waste,
    infercept_decisions,
)


def test_continuum_cold_start_solves_the_exponential_objective():
    model = ContinuumTTL()
    choice = model.choose(
        "test", evicted_queue_seconds=9., prefill_reload_seconds=1.,
        memoryfulness=-.5,
    )
    assert choice.evidence == "exponential_cold_start"
    assert choice.seconds == pytest.approx(math.log(10.))
    assert choice.expected_net_seconds == pytest.approx(9. - math.log(10.))


def test_continuum_short_cold_start_benefit_does_not_pin():
    choice = ContinuumTTL().choose(
        "unknown", evicted_queue_seconds=0., prefill_reload_seconds=.02,
        memoryfulness=1.,
    )
    assert choice.seconds == 0.


def test_continuum_tool_cdf_and_global_cold_start_are_distinct():
    model = ContinuumTTL(cold_start_records=2)
    for tool, value in (("fast", .1), ("fast", .1), ("slow", 5.)):
        model.observe_gap(tool, value)
    global_choice = model.choose(
        "new", evicted_queue_seconds=2., prefill_reload_seconds=0., memoryfulness=1.,
    )
    assert global_choice.evidence == "global_empirical_cdf"
    assert global_choice.seconds == .1
    model.observe_gap("fast", .2)
    local = model.choose(
        "fast", evicted_queue_seconds=2., prefill_reload_seconds=0., memoryfulness=1.,
    )
    assert local.evidence == "tool_empirical_cdf"
    assert local.seconds == .2
    assert local.samples == 3


def test_continuum_queue_cost_can_change_the_ttl_without_changing_tool_duration():
    model = ContinuumTTL(cold_start_records=0)
    model.observe_gap("same", .5)
    no_queue = model.choose(
        "same", evicted_queue_seconds=0., prefill_reload_seconds=.1, memoryfulness=1.,
    )
    queued = model.choose(
        "same", evicted_queue_seconds=3., prefill_reload_seconds=.1, memoryfulness=1.,
    )
    memoryless = model.choose(
        "same", evicted_queue_seconds=3., prefill_reload_seconds=.1, memoryfulness=0.,
    )
    assert no_queue.seconds == memoryless.seconds == 0.
    assert queued.seconds == .5


@pytest.mark.parametrize("gaps,identified,expected", (
    ([], False, 0.), ([], True, 2.), ([1., 3.], True, 2.),
    ([2., 2.1], True, 0.),
))
def test_public_continuum_is_not_the_paper_estimator(gaps, identified, expected):
    assert continuum_public_ttl(gaps, tool_identified=identified) == expected


def test_continuum_priority_keeps_preemption_ttl_and_original_program_age():
    values = {
        "new": (False, False, 10., 0),
        "return": (False, False, 1., 8),
        "pinned": (False, True, 5., 9),
        "preempted": (True, False, 12., 10),
    }
    order = sorted(values, key=lambda name: continuum_priority(
        **dict(zip(("preempted", "ttl_retained", "program_arrival", "native_position"), values[name]))
    ))
    assert order == ["preempted", "pinned", "return", "new"]


def test_public_continuum_queue_uses_program_fcfs_with_ttl_first():
    values = {
        "new": (False, 10., 0),
        "return": (False, 1., 8),
        "pinned_new": (True, 5., 9),
        "pinned_old": (True, 1., 10),
    }
    assert sorted(values, key=lambda name: continuum_public_priority(
        **dict(zip(("ttl_retained", "program_arrival", "native_position"), values[name]))
    )) == ["pinned_old", "pinned_new", "return", "new"]


def test_thunder_sequential_projection_can_block_a_spawned_dependency():
    policy = ThunderWorkingSet(1000)
    policy.arrive("parent", 898, now=0.)
    policy.completed("parent", 900, now=1.)
    policy.arrive("child", 50, now=2.)
    # Public policy has no observed JOIN edge and no negative capacity to fix.
    for now in (5., 10., 600., 1799.):
        assert policy.tick(now=now) == ()
        assert policy.programs["parent"].active
        assert policy.programs["child"].pending
        assert not policy.programs["child"].active
        assert policy.remaining_capacity() == 0


def test_concur_requires_both_high_usage_and_low_hits_for_a_decrease():
    controller = ConcurWindow(8, maximum=24)
    assert controller.observe(sample_id=1, gpu_usage=.1, gpu_hit_rate=.9) == 10
    assert controller.observe(sample_id=2, gpu_usage=.9, gpu_hit_rate=.8) == 10
    assert controller.observe(sample_id=3, gpu_usage=.9, gpu_hit_rate=.1) == 5
    assert controller.observe(sample_id=3, gpu_usage=.1, gpu_hit_rate=.9) == 5
    assert controller.observe(sample_id=4, gpu_usage=.3, gpu_hit_rate=.1) == 5
    with pytest.raises(ValueError):
        controller.observe(sample_id=2, gpu_usage=.9, gpu_hit_rate=.1)


def test_concur_window_stays_within_agent_population():
    controller = ConcurWindow(1, maximum=2)
    assert controller.observe(sample_id=1, gpu_usage=.9, gpu_hit_rate=0.) == 1
    assert controller.observe(sample_id=2, gpu_usage=.1, gpu_hit_rate=1.) == 2


@pytest.mark.parametrize("kwargs", (
    {"additive_increase": 0}, {"multiplicative_decrease": 1.},
    {"high_usage": .1}, {"low_hit_rate": 2.},
))
def test_concur_rejects_invalid_control_laws(kwargs):
    with pytest.raises(ValueError):
        ConcurConfig(**kwargs)


def test_infercept_chunk_discard_includes_other_resident_contexts():
    assert infercept_chunk_discard_waste(
        context_tokens=100, other_context_tokens=500, chunks=4,
        full_forward_seconds=.08, chunk_forward_seconds=.01,
    ) == pytest.approx(24.)


def test_infercept_swap_budget_goes_to_highest_min_waste_not_longest_tool():
    requests = (
        Intercept("long-small", 10, 10., 0),
        Intercept("short-large", 100, 1., 1),
        Intercept("cheap-to-keep", 50, .01, 2),
    )
    decisions = infercept_decisions(
        requests, swap_out_tokens=120, free_host_tokens=120,
        discard_waste=lambda tokens: tokens * .5,
    )
    assert [item.identity for item in decisions] == [
        "short-large", "long-small", "cheap-to-keep",
    ]
    assert [item.swap_tokens for item in decisions] == [100, 10, 10]
    assert decisions[-1].remaining_tokens == 40
    assert decisions[-1].remaining_action == "preserve"


def test_infercept_host_pressure_limits_swapping_and_selects_residual_actions():
    decisions = infercept_decisions(
        (Intercept("large", 100, 10., 0), Intercept("fast", 10, .01, 1)),
        swap_out_tokens=100, free_host_tokens=30, discard_waste=lambda count: count * .2,
    )
    assert decisions[0].swap_tokens == 30
    assert decisions[0].remaining_action == "discard"
    assert decisions[1].swap_tokens == 0
    assert decisions[1].remaining_action == "preserve"


def test_thunder_program_stays_active_across_tool_wait():
    policy = ThunderWorkingSet(1000)
    policy.arrive("a", 400, now=1.)
    policy.completed("a", 420, now=2.)
    assert policy.programs["a"].acting and policy.programs["a"].active
    assert policy.remaining_capacity() == 480
    policy.arrive("a", 430, now=3.)
    assert policy.programs["a"].steps == 2
    assert not policy.programs["a"].acting
    with pytest.raises(ValueError, match="concurrent"):
        policy.arrive("a", 431, now=3.1)


def test_thunder_pauses_acting_smallest_first_without_preempting_active_output():
    policy = ThunderWorkingSet(1200)
    policy.arrive("small", 100, now=0.)
    policy.arrive("large", 400, now=0.)
    policy.arrive("running", 100, now=0.)
    policy.completed("small", 110, now=1.)
    policy.completed("large", 420, now=1.)
    policy.programs["running"].tokens = 800
    policy.tick(now=2.)
    assert not policy.programs["small"].active
    assert not policy.programs["large"].active
    assert policy.programs["running"].active
    assert not policy.programs["running"].marked_for_pause


def test_thunder_marks_running_calls_but_credits_no_capacity_before_completion():
    policy = ThunderWorkingSet(1000)
    policy.arrive("a", 300, now=0.)
    policy.arrive("b", 300, now=0.)
    policy.programs["a"].tokens = policy.programs["b"].tokens = 600
    policy.tick(now=1.)
    assert all(item.active and item.marked_for_pause for item in policy.programs.values())
    assert policy.remaining_capacity() == -400
    policy.completed("a", 610, now=2.)
    assert not policy.programs["a"].active
    assert policy.programs["b"].active


def test_thunder_paused_resumption_keeps_group_priority_and_bfd_placement():
    policy = ThunderWorkingSet(100)
    for identity, tokens in (("new", 300), ("returning", 200), ("acting", 100)):
        policy.arrive(identity, tokens, now=1.)
    policy.programs["returning"].steps = 2
    policy.programs["acting"].steps = 2
    policy.programs["acting"].acting = True
    policy.capacity_tokens = 700
    # returning(200+100) and new(300+100) fit, acting does not.
    assert policy.tick(now=2.) == ("new", "returning")
    assert not policy.programs["acting"].active


@pytest.mark.parametrize("value", (True, -1, math.inf, math.nan))
def test_nonphysical_measurements_do_not_become_policy_evidence(value):
    with pytest.raises(ValueError):
        ContinuumTTL().observe_gap("tool", value)
