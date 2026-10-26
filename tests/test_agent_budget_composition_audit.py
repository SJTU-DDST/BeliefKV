from copy import deepcopy

import pytest

from scripts.audit_agent_budget_composition import composition, event_changes, inspect


def state(when, active, blocked, budget):
    return {
        "ts_ms": when,
        "agent_admission": {
            "active_invocation_ids": list(active), "blocked_active_invocation_ids": list(blocked),
            "logical_active_tokens": budget,
            "capacity_semantics": "conservative_sum_of_observed_agent_contexts_plus_growth_buffer",
        },
    }


def event(kind, when, invocation, request, tokens):
    return {
        "kind": kind, "ts_ms": when, "invocation_id": invocation,
        "attributes": {
            "request_id": request,
            "prompt_tokens" if kind == "llm_submit" else "output_tokens": tokens,
        },
    }


def test_pending_blocked_and_between_requests_partition_known_budget_once():
    result = composition(
        state(1., ("a", "b", "c"), ("b",), 360),
        {"a": 10, "b": 20, "c": 30, "waiting": 40}, {"a": "request", "waiting": "other"},
    )
    assert result["model_pending_tokens"] == 110
    assert result["known_blocked_tokens"] == 120
    assert result["between_native_requests_tokens"] == 130
    assert result["inactive_pending_agents"] == 1
    assert result["inactive_pending_tokens"] == 140


def test_missing_or_inconsistent_history_remains_unreconciled():
    row = state(1., ("agent",), (), 200)
    assert composition(row, {}, {}) is None
    assert composition(row, {"agent": 101}, {}) is None


def test_no_future_completion_output_is_charged_before_its_native_timestamp():
    events = [
        event("llm_submit", 0., "a", "r", 100),
        event("llm_result", 100., "a", "r", 50),
    ]
    states = [
        state(0., ("a",), (), 200),
        state(50., ("a",), (), 200),
        state(99., ("a",), (), 250),
    ]
    result = inspect(states, events)
    assert result["counts"]["reconciled_ms"] == 99.
    assert result["counts"]["unreconciled_ms"] == 1.
    assert result["active_budget_token_time_fraction"]["model_pending"] == 1.


def test_unknown_snapshot_gap_is_not_carried_forward_indefinitely():
    events = [
        event("llm_submit", 0., "a", "r", 100),
        event("llm_result", 4000., "a", "r", 10),
    ]
    result = inspect([state(0., ("a",), (), 200)], events)
    assert result["counts"]["covered_ms"] == 1500.
    assert result["uncovered_ms"] == 2500.


def test_completion_updates_retained_context_budget_for_between_request_state():
    events = [
        event("llm_submit", 0., "a", "one", 100),
        event("llm_result", 100., "a", "one", 20),
        event("llm_submit", 200., "a", "two", 130),
        event("llm_result", 300., "a", "two", 10),
    ]
    states = [
        state(0., ("a",), (), 200),
        state(100., ("a",), (), 220),
        state(200., ("a",), (), 230),
    ]
    result = inspect(states, events)
    assert result["counts"]["reconciled_ms"] == 300.
    assert result["time_weighted_agents"]["between_native_requests"] == pytest.approx(1. / 3.)
    assert result["active_budget_token_time_fraction"]["between_native_requests"] == pytest.approx(220. / 650.)


def test_completion_requires_matching_request_identity():
    with pytest.raises(ValueError, match="matching arrival"):
        event_changes([event("llm_result", 0., "a", "missing", 1)])


@pytest.mark.parametrize("change", ("duplicate", "blocked_unknown", "semantics"))
def test_invalid_snapshot_identity_and_budget_semantics_are_rejected(change):
    row = state(0., ("a",), (), 200)
    if change == "duplicate":
        row["agent_admission"]["active_invocation_ids"] = ["a", "a"]
    elif change == "blocked_unknown":
        row["agent_admission"]["blocked_active_invocation_ids"] = ["b"]
    else:
        row["agent_admission"]["capacity_semantics"] = "physical_pages"
    with pytest.raises(ValueError, match="unsupported"):
        composition(row, {"a": 100}, {})


@pytest.mark.parametrize("when", [0., -1., float("nan")])
def test_unordered_or_invalid_snapshot_time_is_rejected(when):
    events = [
        event("llm_submit", 0., "a", "r", 100),
        event("llm_result", 100., "a", "r", 10),
    ]
    with pytest.raises(ValueError, match="increase"):
        inspect([state(0., ("a",), (), 200), state(when, ("a",), (), 200)], events)
