from copy import deepcopy

import pytest

from scripts.audit_agent_loan_state import inspect


def state(when, *, active=("borrower",), loans=None, returned=0):
    return {
        "event": "admission_runtime_state", "ts_ms": when, "physical_disabled": False,
        "agent_admission": {
            "strategy": "thunderagent_retained", "blocked_lending": "backed_demand_recall",
            "join_loan_recall": True, "native_capacity_authority_preserved": True,
            "join_loan_scope": "logical_admissions_during_open_blocked_lending_period",
            "join_loan_recall_boundary": "submitted_join_successor;nonrunning_borrower",
            "active_agents": len(active), "active_invocation_ids": list(active),
            "join_loan_borrowers": {"parent": ["borrower"]} if loans is None else loans,
            "policy_counts": {"join_loan_recall_admitted": returned},
        },
    }


def test_open_lending_and_return_are_counted_without_claiming_exact_transition_times():
    result = inspect([
        state(1000.), state(2000., active=("parent",), loans={}, returned=1),
    ])
    assert result["counts"]["snapshots"] == 2
    assert result["counts"]["snapshots_with_open_loans"] == 1
    assert result["maximum_policy_counters"]["join_loan_recall_admitted"] == 1
    assert result["last_observation"]["open_loans"] == {}
    assert result["sampled_relationship_invariants_passed"]


def test_multiple_lenders_sharing_one_borrower_do_not_duplicate_active_agent_count():
    result = inspect([state(1000., loans={"a": ["borrower"], "b": ["borrower"]})])
    assert result["counts"]["maximum_relationships"] == 2
    assert result["counts"]["maximum_distinct_borrowers"] == 1


def test_open_period_can_temporarily_have_no_borrower_and_stale_gap_is_reported():
    result = inspect([
        state(1000., active=(), loans={"parent": []}),
        state(3000.),
    ])
    assert result["counts"]["sampled_open_periods_without_current_borrower"] == 1
    assert result["counts"]["snapshot_gaps_over_1500ms"] == 1


@pytest.mark.parametrize("active,loans", [
    (("parent", "borrower"), {"parent": ["borrower"]}),
    ((), {"parent": ["borrower"]}),
    (("borrower",), {"parent": ["borrower", "borrower"]}),
    (("borrower",), {"parent": [None]}),
    (("borrower",), {"": ["borrower"]}),
])
def test_inconsistent_or_duplicate_relationships_are_rejected(active, loans):
    with pytest.raises(ValueError):
        inspect([state(1000., active=active, loans=loans)])


@pytest.mark.parametrize("when", [1000., 999., float("nan"), float("inf"), True])
def test_duplicate_backward_and_invalid_timestamps_are_rejected(when):
    with pytest.raises(ValueError, match="chronology"):
        inspect([state(1000.), state(when)])


@pytest.mark.parametrize("value", [-1, True, 0.5])
def test_invalid_counter_values_are_rejected(value):
    row = state(1000.)
    row["agent_admission"]["policy_counts"]["join_loan_recall_admitted"] = value
    with pytest.raises(ValueError, match="counters"):
        inspect([row])


def test_counter_regression_is_rejected():
    with pytest.raises(ValueError, match="regressed"):
        inspect([state(1000., returned=2), state(2000., returned=1)])


@pytest.mark.parametrize("field,value", [
    ("strategy", "working_set"), ("blocked_lending", "backed_demand"),
    ("join_loan_recall", False), ("native_capacity_authority_preserved", False),
    ("join_loan_scope", "physical_page_ownership"),
    ("join_loan_recall_boundary", "speculative_eos"),
])
def test_undeclared_runtime_policy_is_not_qualified(field, value):
    row = state(1000.)
    row["agent_admission"][field] = value
    with pytest.raises(ValueError, match="declared"):
        inspect([row])


def test_disabled_physical_runtime_and_empty_capture_are_rejected():
    row = deepcopy(state(1000.))
    row["physical_disabled"] = True
    with pytest.raises(ValueError, match="disabled"):
        inspect([row])
    with pytest.raises(ValueError, match="no observed"):
        inspect([])
