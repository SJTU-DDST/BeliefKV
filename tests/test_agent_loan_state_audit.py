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


def tool_state(when, *, admitted=0):
    row = state(when)
    row["agent_admission"].update(
        tool_loan_return=True,
        tool_loan_return_boundary="matched_all_tools_returned;submitted_successor;nonrunning_borrower",
        tool_loan_return_priority="below_join;recorded_lenders_only",
    )
    row["agent_admission"]["policy_counts"].update({
        "recorded_lender_tool_returned": 1,
        "tool_loan_recall_admitted": admitted,
        "capacity_switch:tool_loan_return": admitted,
    })
    return row


def test_tool_return_counters_remain_distinct_from_join_returns():
    result = inspect([tool_state(1000.), tool_state(2000., admitted=1)])
    assert result["tool_loan_return_enabled"] is True
    assert result["maximum_policy_counters"]["tool_loan_recall_admitted"] == 1
    assert result["maximum_policy_counters"]["join_loan_recall_admitted"] == 0


@pytest.mark.parametrize("field,value", [
    ("tool_loan_return", 1),
    ("tool_loan_return_boundary", "forecast_before_return"),
    ("tool_loan_return_priority", "above_join"),
])
def test_tool_recall_requires_its_observed_boundary_and_priority(field, value):
    row = tool_state(1000.)
    row["agent_admission"][field] = value
    with pytest.raises(ValueError, match="tool loan"):
        inspect([row])


def test_tool_policy_cannot_change_mid_run_or_report_execution_while_disabled():
    with pytest.raises(ValueError, match="tool loan"):
        inspect([state(1000.), tool_state(2000.)])
    row = state(1000.)
    row["agent_admission"]["policy_counts"]["tool_loan_recall_admitted"] = 1
    with pytest.raises(ValueError, match="disabled tool"):
        inspect([row])


def test_tool_recall_counter_regression_is_rejected():
    with pytest.raises(ValueError, match="regressed"):
        inspect([tool_state(1000., admitted=2), tool_state(2000., admitted=1)])
