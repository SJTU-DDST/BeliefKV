from copy import deepcopy

import pytest

from experiments.reports.predicted_acting_pressure_20261029.complete_review import (
    pressure_decisions,
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
