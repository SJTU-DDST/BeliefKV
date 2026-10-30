from copy import deepcopy

import pytest

from experiments.reports.predicted_acting_pressure_20261029.complete_review import (
    pressure_decisions,
)
from experiments.reports.predicted_acting_pressure_20261029.pressure_outcomes import (
    evaluate,
)


def decision():
    return {
        "event": "agent_predicted_acting_pressure",
        "selected": "long", "reference_selected": "short",
        "context_tokens": 400, "acting_candidates": 2,
        "remaining_before_tokens": -400, "remaining_after_tokens": 100,
        "physical_capacity_credit": False,
        "evidence": {
            "key": {"invocation_id": "long"}, "threshold_ms": 2000.,
            "completion_probability_within_threshold": .2,
            "minimum_survival": .75,
        },
    }


def test_review_separates_logical_choices_and_preserves_zero_actions():
    assert pressure_decisions([])["counts"] == {}
    row = decision()
    report = pressure_decisions([{"event": "unrelated"}, row])
    assert report["counts"]["changed_local_victims"] == 1
    assert report["counts"]["logical_tokens_paused"] == 500
    assert "saved migration" in report["scope"]
    fallback = {
        **deepcopy(row), "selected": "short", "evidence": None,
    }
    result = pressure_decisions([fallback])
    assert result["counts"]["changed_local_victims"] == 0


@pytest.mark.parametrize("field,value", (
    ("remaining_before_tokens", 0), ("remaining_after_tokens", 101),
    ("physical_capacity_credit", True), ("context_tokens", True),
    ("acting_candidates", 0), ("selected", ""), ("evidence", None),
))
def test_pressure_review_refuses_wrong_identity_or_physical_capacity_claim(field, value):
    row = decision()
    row[field] = value
    with pytest.raises(ValueError):
        pressure_decisions([row])


@pytest.mark.parametrize("field,value", (
    ("minimum_survival", .9), ("threshold_ms", 0.),
    ("completion_probability_within_threshold", float("nan")),
    ("key", {"invocation_id": "other"}),
))
def test_pressure_review_rejects_unproven_probability_or_owner(field, value):
    row = decision()
    row["evidence"][field] = value
    with pytest.raises(ValueError):
        pressure_decisions([row])


def outcomes():
    row = decision()
    row["monotonic_s"] = 2.
    row["evidence"]["key"].update({
        "request_id": "r", "root_workflow_id": "w", "context_id": "c",
        "context_epoch": 1, "session_id": "s", "session_generation": 2,
    })
    base = {"workflow_id": "w", "invocation_id": "long", "context_id": "c"}
    result = {
        **base, "kind": "llm_result", "context_epoch": 1,
        "ts_ms": 1100., "attributes": {"request_id": "r"},
    }
    following = {
        **base, "kind": "llm_submit", "context_epoch": 2,
        "ts_ms": 5100., "attributes": {"request_id": "r2"},
    }
    tools = [
        {
            **base, "kind": kind, "ts_ms": when,
            "attributes": {"tool_run_id": "t"},
        } for kind, when in (("tool_start", 1200.), ("tool_end", 5000.))
    ]
    receipts = [{
        "request_id": "r", "exact_tokens_verified": True,
        "native_receipt": {
            "identity": ["w", "long", "c", 1], "session_id": "s",
            "session_generation": 2, "complete_monotonic_ms": 1000., "clock_domain": "d",
        },
    }, {
        "request_id": "r2", "exact_tokens_verified": True,
        "native_receipt": {
            "identity": ["w", "long", "c", 2], "clock_domain": "d",
        },
        "response_metrics": {"first_output_monotonic_ms": 5300.},
    }]
    return [row], [result, following, *tools], receipts


def test_pressure_outcomes_bind_real_tool_and_deduplicate_repeated_decisions():
    decisions, events, receipts = outcomes()
    again = deepcopy(decisions[0])
    again["monotonic_s"] = 4.
    report = evaluate([*decisions, again], events, receipts)
    assert report["counts"]["fully_bound_decisions"] == 2
    assert report["counts"]["observed_survived_threshold_decisions"] == 1
    assert report["counts"]["independent_tool_executions"] == 1
    assert report["counts"]["independent_first_decisions_survived_threshold"] == 1
    assert report["rows"][0]["tool_return_to_next_first_output_ms"] == 300.
    assert "causal JCT" in report["scope"]


def test_pressure_outcomes_keep_returned_tools_and_unknown_prefixes():
    decisions, events, receipts = outcomes()
    decisions[0]["monotonic_s"] = 5.05
    report = evaluate(decisions, events, receipts)
    assert report["counts"]["pause_outside_observed_tool"] == 1
    assert report["rows"][0]["observed_remaining_ms"] == -50.
    report = evaluate(decisions, events[:1] + events[2:], receipts)
    assert report["counts"]["unbound_decisions"] == 1
    assert report["rows"][0]["binding"] == "following_request_outside_observed_prefix"


@pytest.mark.parametrize("change", (
    "wrong_session", "wrong_epoch", "result_before_native",
    "mixed_clock", "duplicate_tool", "first_output_before_submit",
))
def test_pressure_outcomes_refuse_wrong_identity_or_clock(change):
    decisions, events, receipts = outcomes()
    if change == "wrong_session":
        receipts[0]["native_receipt"]["session_id"] = "other"
    elif change == "wrong_epoch":
        receipts[0]["native_receipt"]["identity"][-1] = 9
    elif change == "result_before_native":
        events[0]["ts_ms"] = 999.
    elif change == "mixed_clock":
        receipts[1]["native_receipt"]["clock_domain"] = "other"
    elif change == "duplicate_tool":
        events.append(deepcopy(events[-1]))
    else:
        receipts[1]["response_metrics"]["first_output_monotonic_ms"] = 5099.
    with pytest.raises(ValueError):
        evaluate(decisions, events, receipts)
