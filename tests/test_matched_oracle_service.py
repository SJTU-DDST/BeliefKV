from copy import deepcopy

import pytest

from scripts.audit_matched_oracle_service import inspect, matched_choice, summarize
from scripts.audit_workflow_size_selection import inspect_choice, ORACLE_RANK
from tests.test_workflow_size_selection_audit import choice, key, service


def oracle_row(*, protected=False):
    row = choice()
    row.update(
        rank_kind=ORACLE_RANK, rank_before_category=True,
        ordered=[
            ["short", 0, 0. if protected else 11., 600, 2, None],
            ["small", 0, 101., 300, 1, None],
        ],
    )
    return row


def test_ready_join_protection_is_not_a_remaining_work_ranking_gain():
    row = oracle_row(protected=True)
    assert inspect_choice(row)["selected_set_changed"]
    assert not matched_choice(row)["selected_set_changed"]
    assert matched_choice(row)["matched_control_selected"] == ["short"]


def test_sibling_adjacency_is_not_a_remaining_work_ranking_gain():
    row = oracle_row()
    row.update(
        capacity_tokens=400, selected_tokens=400, selected_in_priority_order=["a", "c"],
        ordered=[
            ["a", 0, 101., 100, 1, [0, 101., "join"]],
            ["c", 0, 101., 100, 3, [0, 101., "join"]],
            ["b", 0, 101., 100, 2, None],
        ],
    )
    assert inspect_choice(row)["selected_set_changed"]
    assert not matched_choice(row)["selected_set_changed"]
    assert matched_choice(row)["matched_control_selected"] == ["a", "c"]


def test_actual_short_work_replaces_larger_remaining_work_across_categories():
    row = oracle_row()
    row["ordered"][0][1] = 1
    result = matched_choice(row)
    assert result["selected_set_changed"]
    assert result["newly_selected_invocations"] == ["short"]
    assert result["matched_control_selected"] == ["small"]


def test_order_only_does_not_count_as_a_set_change():
    row = oracle_row()
    row.update(capacity_tokens=2000, selected_tokens=1100, selected_in_priority_order=["short", "small"])
    assert matched_choice(row)["order_changed"]
    assert not matched_choice(row)["selected_set_changed"]


@pytest.mark.parametrize("capacity", (-20, 0, 100))
def test_empty_budget_keeps_both_choices_empty(capacity):
    row = oracle_row()
    row.update(capacity_tokens=capacity, selected_tokens=0, ordered=[], selected_in_priority_order=[])
    assert matched_choice(row)["matched_control_selected"] == []


@pytest.mark.parametrize("change", ("legacy", "local", "unreproduced"))
def test_unsupported_or_unreproduced_actual_choices_are_rejected(change):
    row = oracle_row()
    if change == "legacy":
        row["rank_kind"] = "learned_work_class"
    elif change == "local":
        row["rank_before_category"] = False
    else:
        row["selected_in_priority_order"] = ["small"]
    with pytest.raises(ValueError):
        matched_choice(row)


def progress_evidence():
    row = oracle_row()
    programs = [{
        "invocation_id": owner, "key": key(owner),
        "active_before": False, "pending_before": True, "acting_before": False,
        "marked_before": False, "application_ready": True,
        "active_after_pressure": owner == "short", "marked_after_pressure": False,
        "complete_budget_tokens": tokens + 100,
    } for owner, tokens in (("short", 600), ("small", 300))]
    control = {
        "event": "agent_periodic_admission_decision", "monotonic_s": 10.,
        "resume_capacity_tokens": 700, "programs": programs,
        "resumed_program_ids": ["short"], "physical_capacity_credit": False,
    }
    final = deepcopy(control)
    final.update(monotonic_s=12., resumed_program_ids=[])
    batch = service("short", 10002.)
    batch.update(launch_monotonic_ms=10001., complete_monotonic_ms=10003., phase="prefill")
    batch["request_samples"][0].update(phase="prefill", token_delta=10, output_token_delta=0)
    return [control, row, final], [batch]


def test_matching_actual_grant_and_new_useful_completion_are_required():
    choices, progress = inspect(*progress_evidence())
    counts = summarize(choices)
    assert counts["matched_control_selected_set_changes"] == 1
    assert counts["newly_selected_with_following_exact_service"] == 1
    assert counts["newly_selected_with_bounded_useful_completion"] == 1
    assert progress["counts"]["ready_grants_completed_useful_batch_within_observation_window"] == 1


@pytest.mark.parametrize("change", ("foreign_attempt", "earlier_service", "revoked", "paused"))
def test_incorrect_service_or_intervening_pause_does_not_validate_the_original_choice(change):
    records, batches = progress_evidence()
    if change == "foreign_attempt":
        batches[0]["request_samples"][0]["attempt_id"] += 1
    elif change == "earlier_service":
        batches[0].update(
            launch_monotonic_ms=9997., service_start_monotonic_ms=9998.,
            complete_monotonic_ms=9999.,
        )
    elif change == "revoked":
        records[0]["programs"][0]["active_after_pressure"] = False
        records[1]["candidates"][0]["active_after_pressure"] = False
    else:
        records.insert(2, {
            "event": "agent_full_offload_paused", "monotonic_s": 10.0005, "key": key("short"),
        })
    choices, _ = inspect(records, batches)
    assert summarize(choices)["newly_selected_with_bounded_useful_completion"] == 0


def test_missing_periodic_episode_is_explicitly_unknown():
    records, batches = progress_evidence()
    records.pop(0)
    choices, _ = inspect(records, batches)
    counts = summarize(choices)
    assert counts["newly_selected_without_matching_periodic_episode"] == 1
    assert counts.get("newly_selected_with_following_exact_service", 0) == 0


def test_duplicate_selection_timestamps_cannot_share_the_same_service_episode():
    records, batches = progress_evidence()
    records.insert(2, deepcopy(records[1]))
    with pytest.raises(ValueError, match="duplicate oracle selection"):
        inspect(records, batches)
