from copy import deepcopy

import pytest

from scripts.audit_model_work_competition_supply import summarize


def candidate(workflow, work, *, protected=False, available=True):
    return {
        "selector_metadata_valid": available,
        "protected_JOIN": protected,
        "recorded_remaining_model_work": work,
        "recorded_remaining_path_s": 99999. if work == 10. else 1.,
        "key": {
            "request_id": workflow + "-request", "root_workflow_id": workflow,
            "invocation_id": workflow + "-agent", "context_id": workflow + "-context",
            "context_epoch": 0, "attempt_id": 0, "session_id": workflow + "-session",
            "session_generation": 1,
        },
    }


def test_model_work_ratio_uses_recorded_work_not_opposite_tool_path_order():
    result = summarize([{"candidates": [candidate("short", 10.), candidate("long", 80.)]}], {"short"})
    assert result["boundaries_with_model_work_ratio_ge_5"] == 1
    assert result["measured_participates_with_model_work_ratio_ge_2"] == 1
    assert result["distinct_measured_workflows_participating"] == 1


def test_repeated_candidate_set_is_counted_once_and_protected_join_is_excluded():
    row = {"candidates": [
        candidate("a", 10.), candidate("b", 30.), candidate("join", .01, protected=True),
        candidate("unknown", 0., available=False),
    ]}
    result = summarize([row, deepcopy(row)], {"a", "join"})
    assert result["boundaries"] == result["ordinary_multi_workflow_boundaries"] == 2
    assert result["unique_ordinary_candidate_request_sets"] == 1
    assert result["mean_ordinary_workflows"] == 2.
    assert result["protected_JOIN_candidate_observations"] == 2
    assert result["unavailable_candidate_labels"] == 2
    assert result["measured_workflow_ids_participating"] == ["a"]


@pytest.mark.parametrize("work", (-1., float("nan"), float("inf"), True, "10"))
def test_invalid_recorded_work_is_rejected(work):
    with pytest.raises(ValueError, match="recorded remaining model work"):
        summarize([{"candidates": [candidate("a", work), candidate("b", 10.)]}], set())


def test_zero_work_does_not_create_infinite_heterogeneity_or_a_gain_claim():
    result = summarize([{"candidates": [candidate("a", 0.), candidate("b", 10.)]}], set())
    assert result["boundaries_with_zero_remaining_work"] == 1
    assert result.get("boundaries_with_model_work_ratio_ge_2", 0) == 0
    assert "JCT_gain" not in result
