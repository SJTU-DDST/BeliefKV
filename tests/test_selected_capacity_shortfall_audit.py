from copy import deepcopy

import pytest

from scripts.audit_selected_capacity_shortfalls import summarize


def shortfall():
    return {
        "event": "selected_consumer_capacity_shortfall",
        "request_id": "r", "workflow_id": "w", "invocation_id": "a", "context_id": "c",
        "context_epoch": 1, "attempt_id": 0, "dispatch_stage": "before_admission",
        "device_free_tokens": 10, "missing_full_tokens": 30, "input_tokens": 2,
        "growth_reserve_tokens": 3, "required_device_tokens": 35, "deficit_tokens": 25,
    }


def test_repeated_shortfall_is_an_observation_not_an_independent_exchange():
    result = summarize([shortfall(), shortfall()])
    assert result["capacity_shortfall_observations"] == 2
    assert result["distinct_request_attempts"] == 1
    assert result["repeated_observations_of_same_attempt"] == 1
    assert result["submitted_handoffs"] == 0


@pytest.mark.parametrize("field,value", (
    ("input_tokens", 4), ("required_device_tokens", 36), ("deficit_tokens", 0),
    ("device_free_tokens", 11), ("attempt_id", None), ("context_epoch", True),
))
def test_inconsistent_capacity_or_identity_cannot_create_a_shortfall_count(field, value):
    row = shortfall()
    row[field] = value
    with pytest.raises(ValueError):
        summarize([row])


def test_private_geometry_credits_only_free_tokens_outside_the_full_reserve():
    row = {
        "event": "layer_kv_handoff_geometry_shortfall", "beneficiary_request_id": "r",
        "private_unbacked_donor_tokens": 20, "minimum_donor_tokens": 25,
        "device_free_tokens": 10, "restore_tokens": 30,
        "input_tokens": 2, "growth_reserve_tokens": 3, "required_reserve_tokens": 5,
    }
    result = summarize([row, deepcopy(row)])
    assert result["handoff_private_geometry_refusals"] == 2
    assert result["handoff_private_geometry_distinct_consumers"] == 1
    assert result["private_donor_coverage_of_required_recycled_tokens"]["p50"] == .8
    row["minimum_donor_tokens"] = 21
    with pytest.raises(ValueError, match="free extent or reserve"):
        summarize([row])
