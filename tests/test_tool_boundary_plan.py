from dataclasses import replace

import pytest

from beliefkv.policy.tool_boundary_preemption import BoundaryDonor, choose_boundary_plan


def donor(key="long", *, work=100., logical=100, physical=100, backup=0., restore=10.):
    return BoundaryDonor(key, key, work, logical, physical, backup, restore, physical * 2)


def choose(donors, **changes):
    settings = dict(
        consumer_workflow="short", consumer_work=10., logical_deficit=80,
        physical_deficit=80, consumer_restore_ms=10., consumer_migration_tokens=80,
        model_seconds_per_work_token=1., margin_ms=400.,
    )
    settings.update(changes)
    return choose_boundary_plan(donors, **settings)


def test_best_long_owner_is_selected_independently_of_registration_order():
    medium, long = donor("medium", work=60.), donor()
    assert choose([medium, long]).donors == choose([long, medium]).donors == ("long",)


def test_two_donors_are_used_only_when_no_single_owner_can_cover_full_demand():
    first, second = donor("first", physical=50, logical=50), donor("second", physical=50, logical=50)
    assert choose([first]) is None
    plan = choose([first, second])
    assert plan.donors == ("first", "second") and plan.physical_tokens == 100
    assert plan.gain_ms == 79_560.
    assert choose([first, second, donor(work=40.)]).donors == ("long",)


@pytest.mark.parametrize("change", (
    {"logical_deficit": 201}, {"physical_deficit": 201},
    {"consumer_work": 80.}, {"consumer_restore_ms": 100_000.},
    {"migration_allowance": 10},
))
def test_work_gap_whole_capacity_cost_and_migration_limits_are_required(change):
    assert choose([donor(), donor("other")], **change) is None


def test_same_workflow_and_duplicate_owner_are_not_free_capacity():
    assert choose([donor("short")]) is None
    with pytest.raises(ValueError, match="counted twice"):
        choose([donor(), donor()])


def test_physical_capacity_is_not_inferred_from_logical_credit():
    assert choose([donor(physical=0)]) is None
    assert choose([donor()], physical_deficit=0) is not None
    assert choose([donor()], logical_deficit=0, physical_deficit=0) is None


def test_transfer_cost_can_outweigh_selecting_the_longest_workflow():
    longest = donor("largest", work=110., restore=100_000.)
    assert choose([longest, donor()]).donors == ("long",)


def test_group_prices_each_displaced_owner_and_does_not_add_same_workflow_gain_twice():
    siblings = [donor("child1", physical=50), donor("child2", physical=50)]
    siblings = [replace(item, workflow="same-parent") for item in siblings]
    assert choose(siblings).gain_ms == 79_560.


def test_partial_handover_cannot_accumulate_a_third_distinct_victim():
    small = [donor("second", physical=50, logical=50), donor("third", physical=50, logical=50)]
    assert choose(small, committed_donors=("first",)) is None
    assert choose(small, committed_donors=("second",)).donors == ("second", "third")


@pytest.mark.parametrize("change", (
    {"max_donors": 3}, {"min_work_ratio": .99}, {"consumer_work": float("nan")},
    {"logical_deficit": True}, {"migration_allowance": -1},
))
def test_invalid_inputs_fail_closed(change):
    with pytest.raises(ValueError):
        choose([donor()], **change)


def test_unit_ratio_keeps_strict_short_priority_and_still_prices_migration():
    assert choose([donor(work=10.)], min_work_ratio=1.) is None
    assert choose([donor(work=10.1)], min_work_ratio=1.) is None
    assert choose([donor(work=11.)], min_work_ratio=1.).donors == ("long",)
