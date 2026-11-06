from copy import deepcopy

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts import audit_oracle_rank_objectives as audit
from scripts.audit_oracle_rank_objectives import bound_original_inputs, compare, totals
from tests.test_ranked_oracle_service_audit import ranked


def row(**kwargs):
    result = ranked(**kwargs)
    for item in result["candidates"]:
        owner = item["key"]["invocation_id"]
        item["prediction"].update(
            model_only_path_s=1. if owner == "small" else 10.,
            remaining_work=200. if owner == "small" else 20.,
        )
    return result


def test_tool_path_model_path_and_total_work_have_distinct_same_state_choices():
    original = row()
    before = deepcopy(original)
    result = compare(original, {"workflow-small"})
    choices = result["objectives"]
    assert choices["model_tool_path"]["selected"] == ["short"]
    assert choices["model_only_path"]["selected"] == ["small"]
    assert choices["total_model_work"]["selected"] == ["short"]
    assert result["logical_cross_workflow_competition"]
    assert choices["model_only_path"]["affected_measured_workflows"] == ["workflow-small"]
    assert choices["model_only_path"]["selected_tokens"] == 400
    assert original == before


def test_protected_join_cannot_be_dropped_to_follow_another_work_definition():
    result = compare(row(protected=True), set())
    assert all(choice["selected"] == ["small"] for choice in result["objectives"].values())
    assert result["protected_join_present"]


def test_order_changes_without_set_changes_do_not_count_as_new_admissions():
    original = row(capacity=2000)
    original.update(selected_in_priority_order=["short", "small"], selected_tokens=1100)
    result = compare(original, {"workflow-small", "workflow-short"})
    assert not result["logical_cross_workflow_competition"]
    counts = totals([result])["objectives"]["model_only_path"]
    assert counts["order_changes"] == 1
    assert counts["set_changes"] == 0
    assert counts["measurement_affecting_changes"] == 0
    assert counts["added_agent_episodes"] == 0


@pytest.mark.parametrize("capacity", (-100, 0, 100))
def test_empty_budget_preserves_no_candidate_fast_path(capacity):
    original = row(capacity=capacity)
    original.update(ordered=[], candidates=[], selected_in_priority_order=[], selected_tokens=0)
    original.pop("rank_before_category")
    result = compare(original, set())
    assert all(choice["selected"] == [] for choice in result["objectives"].values())


@pytest.mark.parametrize("field", ("remaining_work", "model_only_path_s"))
@pytest.mark.parametrize("value", (None, -1., float("nan"), float("inf"), True))
def test_missing_or_invalid_true_work_does_not_invent_a_counterfactual(field, value):
    original = row()
    original["candidates"][0]["prediction"][field] = value
    with pytest.raises(ValueError, match="work definitions"):
        compare(original, set())


def test_one_workflow_cannot_have_inconsistent_model_work_at_one_boundary():
    original = row()
    original["candidates"][1]["key"]["root_workflow_id"] = "workflow-small"
    original["candidates"][1]["prediction"]["remaining_path_s"] = 100.
    original["candidates"][1]["bucket"] = 101.
    original["ordered"] = [["small", 0, 101., 300, 1, None], ["short", 1, 101., 600, 2, None]]
    original.update(selected_in_priority_order=["small"], selected_tokens=400)
    with pytest.raises(ValueError, match="inconsistent workflow work"):
        compare(original, set())


def test_summary_counts_affected_members_without_filtering_the_scheduler_candidates():
    unfiltered = compare(row(), {"workflow-small"})
    background_only = compare(row(), set())
    assert unfiltered["objectives"]["model_only_path"]["selected"] == (
        background_only["objectives"]["model_only_path"]["selected"]
    )
    summary = totals([unfiltered, background_only])
    assert summary["counts"]["controls"] == 2
    assert summary["objectives"]["model_only_path"]["set_changes"] == 2
    assert summary["objectives"]["model_only_path"]["measurement_affecting_changes"] == 1
    assert summary["objectives"]["model_only_path"]["distinct_affected_measured_workflows"] == 1


def test_candidate_that_cannot_fit_alone_is_not_an_ordinary_competitive_choice():
    original = row(capacity=400)
    original["ordered"] = [
        ["short", 1, 2., 600, 2, None], ["small", 0, 101., 300, 1, None],
    ]
    original.update(selected_in_priority_order=["small"], selected_tokens=400)
    result = compare(original, set())
    assert not result["logical_cross_workflow_competition"]
    assert result["objectives"]["model_only_path"]["selected"] == ["small"]


def test_counterfactual_cannot_hide_an_unreproducible_original_selection():
    original = row()
    original["selected_in_priority_order"] = ["small"]
    original["selected_tokens"] = 400
    with pytest.raises(ValueError, match="reproduce"):
        compare(original, set())


def test_work_definition_change_preserves_observed_sibling_adjacency():
    original = row(capacity=400)
    original.update(
        ordered=[
            ["b", 0, 2., 100, 2, None],
            ["a", 0, 101., 100, 1, [0, 101., "join"]],
            ["c", 0, 101., 100, 3, [0, 101., "join"]],
        ],
        selected_in_priority_order=["b", "a"], selected_tokens=400,
        candidates=[
            {
                "key": {**original["candidates"][0]["key"], "request_id": "rid-" + owner,
                        "invocation_id": owner, "root_workflow_id": workflow,
                        "context_id": "context-" + owner, "session_id": "session-" + owner},
                "bucket": remaining + 1.,
                "prediction": {
                    "evidence": "oracle", "remaining_path_s": remaining,
                    "model_only_path_s": 1. if owner != "b" else 10.,
                    "remaining_work": 200. if owner != "b" else 20.,
                },
            } for owner, workflow, remaining in (
                ("a", "siblings", 100.), ("b", "other", 1.), ("c", "siblings", 100.),
            )
        ],
    )
    result = compare(original, set())
    assert result["objectives"]["model_only_path"]["selected"] == ["a", "c"]
    assert result["objectives"]["total_model_work"]["selected"] == ["b", "a"]


def test_candidate_classes_partition_controls_instead_of_mixing_observer_populations():
    competing = row()
    all_fit = row(capacity=2000)
    all_fit.update(selected_in_priority_order=["short", "small"], selected_tokens=1100)
    empty = row(capacity=100)
    empty.update(ordered=[], candidates=[], selected_in_priority_order=[], selected_tokens=0)
    none_fit = row(capacity=200)
    none_fit.update(selected_in_priority_order=[], selected_tokens=0)
    one = row(capacity=400)
    one.update(selected_in_priority_order=["small"], selected_tokens=400)
    summary = totals([compare(item, set()) for item in (competing, all_fit, empty, none_fit, one)])
    assert summary["counts"]["controls"] == 5
    assert summary["candidate_classes"] == {
        "multiple_fitting_workflows_compete": 1, "multiple_fitting_workflows_all_fit": 1,
        "no_recorded_candidates": 1, "none_individually_fit": 1, "one_fitting_workflow": 1,
    }


def test_original_code_can_be_verified_in_the_frozen_checkout_after_main_changes(monkeypatch, tmp_path):
    current, frozen = tmp_path / "current", tmp_path / "frozen"
    for root in (current, frozen):
        (root / "beliefkv").mkdir(parents=True)
    original = current / "beliefkv/runtime.py"
    saved = frozen / "beliefkv/runtime.py"
    saved.write_text("original serving code")
    original.write_text("new serving code")
    monkeypatch.setattr(audit, "ROOT", current)
    report = {"inputs_sha256": {str(original): sha256_file(saved)}}
    assert bound_original_inputs(report, frozen)[str(original)]["verified_path"] == str(saved)
    saved.write_text("changed frozen code")
    with pytest.raises(ValueError, match="unavailable or changed"):
        bound_original_inputs(report, frozen)
