from copy import deepcopy

import pytest

from scripts.audit_ranked_oracle_service import inspect, matched_reference
from scripts.audit_workflow_size_selection import ORACLE_RANK
from tests.test_workflow_size_selection_audit import choice, key, service


def ranked(*, protected=False, capacity=700):
    row = choice()
    row.update(
        rank_kind=ORACLE_RANK, rank_before_category=True,
        capacity_tokens=capacity,
        ordered=(
            [["small", 0, 0., 300, 1, None], ["short", 1, 2., 600, 2, None]]
            if protected else
            [["short", 1, 2., 600, 2, None], ["small", 0, 101., 300, 1, None]]
        ),
        selected_in_priority_order=["small"] if protected else ["short"],
        selected_tokens=400 if protected else 700,
        candidates=[
            {
                "key": key(owner), "bucket": 0. if protected and owner == "small" else work + 1.,
                "active_after_pressure": owner == ("small" if protected else "short"),
                "marked_for_pause": False,
                "prediction": {"evidence": "oracle", "remaining_path_s": work},
            } for owner, work in (("small", 100.), ("short", 1.))
        ],
    )
    return row


def evidence():
    selected = ranked()
    grant = {
        "event": "agent_periodic_admission_decision", "monotonic_s": 10.,
        "resume_capacity_tokens": 700, "resumed_program_ids": ["short"],
        "physical_capacity_credit": False,
        "programs": [
            {
                "invocation_id": owner, "key": key(owner), "complete_budget_tokens": tokens,
                "active_before": False, "pending_before": True, "acting_before": False,
                "marked_before": False, "application_ready": True,
                "active_after_pressure": owner == "short", "marked_after_pressure": False,
            } for owner, tokens in (("short", 700), ("small", 400))
        ],
    }
    ending = deepcopy(grant)
    ending.update(monotonic_s=12., resumed_program_ids=[])
    batch = service("short", 10100.)
    batch.update(launch_monotonic_ms=10050., complete_monotonic_ms=10150.)
    batch["request_samples"][0].update(phase="prefill", token_delta=10, output_token_delta=0)
    return [grant, selected, ending], [batch]


def test_actual_changed_set_requires_an_exact_grant_and_bounded_useful_service():
    result = inspect(*evidence())
    counts = result["counts"]
    assert counts["selected_set_changes"] == 1
    assert counts["newly_selected_with_exact_ready_grant"] == 1
    assert counts["newly_selected_retained_after_pressure"] == 1
    assert counts["newly_selected_with_bounded_useful_completion"] == 1
    assert counts["unique_newly_selected_request_attempts"] == 1
    assert result["decisions"][0]["matched_control_selected"] == ["small"]
    assert result["scope"]["JCT_throughput_or_whole_run_counterfactual"] is False


def test_protected_JOIN_is_retained_in_the_matched_reference():
    result = matched_reference(ranked(protected=True))
    assert result["matched_control_selected"] == ["small"]
    assert not result["selected_set_changed"]


def test_order_change_without_set_change_does_not_create_a_new_admission():
    row = ranked(capacity=2000)
    row.update(selected_in_priority_order=["short", "small"], selected_tokens=1100)
    result = matched_reference(row)
    assert result["candidate_order_changed"]
    assert not result["selected_set_changed"]
    assert result["newly_selected_invocations"] == []


def test_matched_reference_keeps_observed_sibling_adjacency():
    row = ranked(capacity=400)
    row.update(
        ordered=[
            ["b", 0, 2., 100, 2, None],
            ["a", 0, 101., 100, 1, [0, 101., "join"]],
            ["c", 0, 101., 100, 3, [0, 101., "join"]],
        ],
        selected_in_priority_order=["b", "a"], selected_tokens=400,
        candidates=[
            {
                "key": {**key(owner), "root_workflow_id": workflow},
                "bucket": remaining + 1.,
                "prediction": {"evidence": "oracle", "remaining_path_s": remaining},
            } for owner, workflow, remaining in (
                ("a", "siblings", 100.), ("b", "other", 1.), ("c", "siblings", 100.),
            )
        ],
    )
    result = matched_reference(row)
    assert result["matched_control_selected"] == ["a", "c"]
    assert result["newly_selected_invocations"] == ["b"]


@pytest.mark.parametrize("capacity", (-100, 0, 100))
def test_empty_budget_fast_path_has_no_invented_candidates(capacity):
    row = ranked(capacity=capacity)
    row.update(ordered=[], candidates=[], selected_in_priority_order=[], selected_tokens=0)
    row.pop("rank_before_category")
    assert matched_reference(row)["matched_control_selected"] == []


@pytest.mark.parametrize("field", ("attempt_id", "session_generation", "context_epoch"))
def test_foreign_attempt_or_context_cannot_prove_service(field):
    records, batches = evidence()
    batches[0]["request_samples"][0][field] += 1
    result = inspect(records, batches)
    assert result["counts"]["newly_selected_followed_by_exact_service"] == 0


def test_pregrant_launch_or_later_control_cannot_prove_bounded_new_service():
    for change in ("old_launch", "later_service"):
        records, batches = evidence()
        if change == "old_launch":
            batches[0]["launch_monotonic_ms"] = 9999.
        else:
            batches[0].update(
                launch_monotonic_ms=12010., service_start_monotonic_ms=12020.,
                complete_monotonic_ms=12100.,
            )
        result = inspect(records, batches)
        assert result["counts"]["newly_selected_followed_by_exact_service"] == 1
        assert result["counts"]["newly_selected_with_bounded_useful_service"] == 0


def test_actual_revocation_is_not_counted_as_consumed_oracle_eligibility():
    records, batches = evidence()
    records[0]["programs"][0]["active_after_pressure"] = False
    result = inspect(records, batches)
    assert result["counts"]["newly_selected_retained_after_pressure"] == 0
    assert result["counts"]["newly_selected_followed_by_exact_service"] == 0


def test_same_agent_intervention_closes_the_oracle_grant_window():
    records, batches = evidence()
    records.insert(2, {
        "event": "agent_full_offload_paused", "monotonic_s": 10.025, "key": key("short"),
    })
    result = inspect(records, batches)
    assert result["counts"]["newly_selected_followed_by_exact_service"] == 1
    assert result["counts"]["newly_selected_with_bounded_useful_service"] == 0


def test_open_prefix_window_is_unknown_even_if_later_useful_progress_is_present():
    records, batches = evidence()
    records.pop()
    result = inspect(records, batches)
    assert result["counts"]["newly_selected_open_service_window"] == 1
    assert result["counts"]["newly_selected_with_bounded_useful_service"] == 0
    assert result["decisions"][0]["periodic_service"][0]["episode"][
        "new_service_before_next_control_or_intervention"
    ] is None


@pytest.mark.parametrize("change", ("grant_members", "grant_attempt", "missing_grant"))
def test_a_selection_cannot_borrow_another_grant(change):
    records, batches = evidence()
    if change == "grant_members":
        records[0]["resumed_program_ids"] = ["small"]
    elif change == "grant_attempt":
        records[0]["programs"][0]["key"]["attempt_id"] += 1
    else:
        records.pop(0)
    with pytest.raises(ValueError, match="grant"):
        inspect(records, batches)


@pytest.mark.parametrize("change", ("prediction", "rank", "kind", "metadata"))
def test_unbound_or_nonoracle_ranking_is_rejected(change):
    row = ranked()
    if change == "prediction":
        row["candidates"][0]["prediction"]["evidence"] = "model"
    elif change == "rank":
        row["candidates"][0]["prediction"]["remaining_path_s"] = 10.
    elif change == "kind":
        row["rank_kind"] = "learned_work_class"
    else:
        row["candidates"][0]["bucket"] = 0.
    with pytest.raises(ValueError):
        matched_reference(row)
