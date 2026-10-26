from collections import Counter

import pytest

from beliefkv.policy.tokencake import CapacityPlan
from beliefkv.policy.tokencake_capacity import TokenCakeCapacityWindow, take_runs


def window():
    result = TokenCakeCapacityWindow(100)
    result.set_plan(CapacityPlan({"a", "b"}, {"a": 2., "b": 1.}, {"a": 10, "b": 10}, 80))
    return result


def test_generation_commitments_reduce_shared_then_lowest_scoring_reserved_capacity():
    state = window()
    state.set_growth("r", 90)
    state.refresh(100)
    assert state.uncommitted == 10
    assert state.shared_available == 0
    assert state.reserved_available == {"a": 10, "b": 0}
    state.refresh(80)
    assert state.uncommitted == -10
    assert state.reserved_available == {"a": 0, "b": 0}
    assert not state.can_admit("a", 1, score=1000)


def test_critical_waiting_type_is_protected_except_for_explicit_score_margin_or_fallback():
    state = window()
    state.set_waiting({"a": 1000, "b": 1000})
    assert state.consumption("ordinary", 90, score=1000) is None
    assert state.consumption("ordinary", 90, score=1500) == ((None, 80), ("a", 10))
    assert state.consumption(
        "ordinary", 90, score=0, borrow_reserved=True,
    ) == ((None, 80), ("a", 10))


def test_critical_agent_consumes_its_own_reservation_before_borrowing():
    state = window()
    state.set_waiting({"a": 1000, "b": 1000})
    assert state.consumption("a", 90, score=0) == ((None, 80), ("a", 10))
    assert state.consumption("a", 91, score=0) is None


def test_native_growth_uses_existing_commitment_and_preserves_revised_partition_debt():
    state = window()
    state.set_growth("r", 100)
    state.refresh(100)
    assert state.shared_available == 0 and not any(state.reserved_available.values())
    assert state.charge("a", 1, score=1000, already_admitted=True) == ((None, 1),)
    state.set_growth("r", 99)
    state.refresh(99)
    assert state.uncommitted == 0
    assert state.used == Counter({None: 1})
    assert not state.can_admit("a", 1, score=1000)


def test_repeated_charge_and_release_conserve_actual_occupied_units():
    state = window()
    first = state.charge("a", 90, score=0)
    assert first == ((None, 80), ("a", 10))
    assert sum(state.used.values()) == 90
    state.refresh(10)
    second = state.charge("b", 10, score=0)
    assert second == (("b", 10),)
    state.refresh(0)
    assert not state.can_admit("a", 1, score=1000)
    state.release(first)
    state.refresh(90)
    assert state.used == Counter({"b": 10})
    assert state.shared_available == 80 and state.reserved_available["a"] == 10
    state.release(second)
    state.refresh(100)
    assert not state.used
    assert state.shared_available + sum(state.reserved_available.values()) == 100


def test_old_reservation_owner_stays_charged_after_type_leaves_current_partition():
    state = window()
    charges = state.charge("a", 90, score=0)
    state.set_plan(CapacityPlan({"new"}, {"new": 1.}, {"new": 20}, 80))
    state.refresh(10)
    assert state.used == Counter({None: 80, "a": 10})
    assert state.shared_available + sum(state.reserved_available.values()) == 10
    state.release(charges)
    state.refresh(100)
    assert state.shared_available == 80 and state.reserved_available["new"] == 20


def test_extent_split_keeps_order_and_coalesces_adjacent_runs_without_per_token_entries():
    assert take_runs(((None, 2), (None, 3), ("a", 7)), 6) == (
        ((None, 5), ("a", 1)), (("a", 6),),
    )
    assert take_runs(((None, 1_000_000),), 500_000) == (
        ((None, 500_000),), ((None, 500_000),),
    )


@pytest.mark.parametrize("count", (-1, 13, 1.5))
def test_invalid_extent_split_is_rejected(count):
    with pytest.raises(ValueError):
        take_runs(((None, 12),), count)


def test_invalid_partition_cannot_silently_change_capacity_or_allow_negative_ownership():
    state = window()
    with pytest.raises(ValueError, match="conserve"):
        state.set_plan(CapacityPlan({"a"}, {"a": 1.}, {"a": 10}, 100))
    with pytest.raises(ValueError, match="more physical"):
        state.release(((None, 1),))
    assert not state.used
