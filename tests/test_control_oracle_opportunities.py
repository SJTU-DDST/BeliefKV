from copy import deepcopy

import pytest

from scripts.audit_control_oracle_opportunities import opportunity, totals
from scripts.audit_workflow_size_selection import MATCHED_CONTROL_RANK, inspect_choice
from tests.test_workflow_size_selection_audit import choice, key


def control(*, capacity=700, protected=False):
    row = choice()
    row.update(
        rank_kind=MATCHED_CONTROL_RANK, rank_before_category=True,
        capacity_tokens=capacity,
        ordered=[
            ["small", 0, 0. if protected else 1., 300, 1, None],
            ["short", 1, 1., 600, 2, None],
        ],
        selected_in_priority_order=["small"], selected_tokens=400,
        candidates=[
            {
                "key": key(owner), "bucket": 0. if protected and owner == "small" else 1.,
                "active_after_pressure": owner == "small", "marked_for_pause": False,
                "prediction": {"evidence": "oracle", "remaining_path_s": work},
            }
            for owner, work in (("small", 100.), ("short", 1.))
        ],
    )
    return row


def test_matched_control_float_ranks_reproduce_the_actual_control():
    assert not inspect_choice(control())["selected_set_changed"]


def test_local_oracle_can_change_the_set_and_never_claims_jct_or_physical_feasibility():
    decision = opportunity(control())
    assert decision["control_selected"] == ["small"]
    assert decision["oracle_selected"] == ["short"]
    assert decision["selected_set_changed"]
    assert decision["cross_workflow_individually_fitting_set_competition"]
    assert decision["fitting_remaining_work_ratio"] == 100.


def test_protected_ready_join_is_not_displaced_by_a_shorter_workflow():
    decision = opportunity(control(protected=True))
    assert decision["oracle_selected"] == ["small"]
    assert not decision["selected_set_changed"]


def test_an_order_change_with_sufficient_budget_is_not_a_set_change():
    row = control(capacity=2000)
    row.update(selected_tokens=1100, selected_in_priority_order=["small", "short"])
    row["candidates"][1]["active_after_pressure"] = True
    decision = opportunity(row)
    assert decision["order_changed"] and not decision["selected_set_changed"]
    assert not decision["cross_workflow_individually_fitting_set_competition"]


def test_single_workflow_siblings_do_not_become_cross_workflow_competition():
    row = control()
    row["candidates"][1]["key"]["root_workflow_id"] = row["candidates"][0]["key"]["root_workflow_id"]
    row["candidates"][1]["prediction"]["remaining_path_s"] = 100.
    assert not opportunity(row)["cross_workflow_individually_fitting_set_competition"]


def test_sibling_adjacency_uses_the_recorded_join_group_in_both_choices():
    row = control(capacity=400)
    row.update(
        ordered=[
            ["a", 0, 1., 100, 1, [0, 1., "join"]],
            ["c", 0, 1., 100, 3, [0, 1., "join"]],
            ["b", 0, 1., 100, 2, None],
        ],
        selected_in_priority_order=["a", "c"], selected_tokens=400,
        candidates=[
            {
                "key": {**key(owner), "root_workflow_id": workflow},
                "bucket": 1., "active_after_pressure": owner != "b", "marked_for_pause": False,
                "prediction": {"evidence": "oracle", "remaining_path_s": work},
            }
            for owner, workflow, work in (("a", "siblings", 100.), ("c", "siblings", 100.),
                                          ("b", "other", 1.))
        ],
    )
    decision = opportunity(row)
    assert decision["control_selected"] == ["a", "c"]
    assert decision["oracle_selected"] == ["b", "a"]


def test_one_workflow_cannot_supply_conflicting_remaining_path_predictions():
    row = control()
    row["candidates"][1]["key"]["root_workflow_id"] = row["candidates"][0]["key"]["root_workflow_id"]
    with pytest.raises(ValueError, match="inconsistent"):
        opportunity(row)


def test_short_work_larger_than_the_budget_is_not_a_feasible_choice():
    row = control()
    row["capacity_tokens"] = 600
    assert opportunity(row)["oracle_selected"] == ["small"]


@pytest.mark.parametrize("capacity", (-100, 0, 100))
def test_empty_budget_fast_path_has_no_counterfactual_candidates(capacity):
    row = control(capacity=capacity)
    row.update(ordered=[], candidates=[], selected_tokens=0, selected_in_priority_order=[])
    row.pop("rank_before_category")
    decision = opportunity(row)
    assert decision["oracle_selected"] == [] and not decision["selected_set_changed"]


@pytest.mark.parametrize("change", (
    "unbound_prediction", "no_prediction", "negative_work", "nan_work",
    "nonconstant_control_rank", "wrong_metadata_rank", "wrong_rank_kind", "duplicate",
))
def test_missing_or_unsupported_control_evidence_is_rejected(change):
    row = control()
    if change == "unbound_prediction":
        row["candidates"][0]["prediction"]["evidence"] = "model"
    elif change == "no_prediction":
        row["candidates"][0].pop("prediction")
    elif change == "negative_work":
        row["candidates"][0]["prediction"]["remaining_path_s"] = -1.
    elif change == "nan_work":
        row["candidates"][0]["prediction"]["remaining_path_s"] = float("nan")
    elif change == "nonconstant_control_rank":
        row["ordered"][0][2] = 2.
    elif change == "wrong_metadata_rank":
        row["candidates"][0]["bucket"] = 0.
    elif change == "wrong_rank_kind":
        row["rank_kind"] = "learned_work_class"
    else:
        row["candidates"].append(deepcopy(row["candidates"][0]))
    with pytest.raises(ValueError):
        opportunity(row)


def test_zero_remaining_work_does_not_emit_infinite_json_ratios():
    row = control()
    row["candidates"][1]["prediction"]["remaining_path_s"] = 0.
    decision = opportunity(row)
    assert decision["zero_remaining_work_candidate"]
    assert decision["fitting_remaining_work_ratio"] is None


def test_repeated_boundaries_are_counted_as_decisions_not_independent_requests():
    decision = opportunity(control())
    counts = totals([decision, decision])
    assert counts["actual_control_decisions_replayed"] == 2
    assert counts["oracle_selected_set_changes"] == 2
    assert "unique_requests_improved" not in counts
