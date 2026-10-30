from copy import deepcopy

import pytest

from scripts.audit_workflow_size_selection import inspect, inspect_choice


def key(owner):
    return {
        "request_id": "request-" + owner, "root_workflow_id": "workflow-" + owner,
        "invocation_id": owner, "context_id": "context-" + owner, "context_epoch": 0,
        "attempt_id": 0, "session_id": "session-" + owner, "session_generation": 0,
    }


def choice():
    return {
        "event": "workflow_size_resume_selection", "monotonic_s": 10.,
        "capacity_tokens": 700, "selected_tokens": 700,
        "ordered": [["short", 0, 0, 600, 2, None], ["small", 0, 2, 300, 1, None]],
        "selected_in_priority_order": ["short"],
        "thread_cpu_us": 4., "wall_us": 5.,
        "control_thread_cpu_us": 7., "control_wall_us": 8.,
        "candidates": [
            {"key": key(owner), "active_after_pressure": owner == "short", "marked_for_pause": False}
            for owner in ("short", "small")
        ],
    }


def service(owner, timestamp, *, attempt=0):
    member = key(owner)
    member["workflow_id"] = member.pop("root_workflow_id")
    member["attempt_id"] = attempt
    return {
        "event": "gpu_service_sample", "service_start_monotonic_ms": timestamp,
        "monotonic_clock_domain": "same-host", "request_samples": [member],
    }


def test_audit_distinguishes_local_set_change_and_later_exact_attempt_service():
    result = inspect([choice()], [
        service("short", 9000.), service("short", 10100., attempt=1),
        service("short", 10200.),
    ])
    assert result["counts"]["changed_selected_set_passes"] == 1
    assert result["counts"]["same_category_cross_workflow_capacity_competition_passes"] == 1
    assert result["counts"]["capacity_competition_with_different_effective_ranks_passes"] == 1
    assert result["counts"]["unique_local_choice_changes_with_following_service"] == 1
    assert result["episodes"][0]["following_native_service_ms"] == 200.
    assert result["scope"]["following_service_proves_exclusive_causal_attribution"] is False


def test_order_change_with_enough_capacity_does_not_claim_a_set_change():
    row = choice()
    row.update(capacity_tokens=2000, selected_tokens=1100, selected_in_priority_order=["short", "small"])
    row["candidates"][1]["active_after_pressure"] = True
    result = inspect_choice(row)
    assert result["order_changed"]
    assert not result["selected_set_changed"]
    assert inspect([row], [])["counts"]["same_category_cross_workflow_capacity_competition_passes"] == 0

def test_explicit_global_oracle_replays_cross_category_selection_and_service():
    row = choice()
    row.update(
        rank_kind="oracle_remaining_model_tool_critical_path_seconds",
        rank_before_category=True,
        ordered=[["short", 1, 1., 600, 2, None], ["small", 0, 100., 300, 1, None]],
    )
    result = inspect([row], [service("short", 10200.)])
    assert result["counts"]["global_remaining_rank_passes"] == 1
    assert result["counts"]["global_cross_workflow_capacity_competition_passes"] == 1
    assert result["counts"]["unique_local_choice_changes_with_following_service"] == 1


def test_capacity_competition_respects_capacity_used_by_an_earlier_category():
    row = choice()
    row.update(
        capacity_tokens=800,
        ordered=[
            ["continuation", 0, 2, 600, 1, None],
            ["short", 1, 0, 1, 2, None], ["small", 1, 1, 1, 3, None],
        ],
        selected_in_priority_order=["continuation"],
        candidates=[
            {"key": key(owner), "active_after_pressure": owner == "continuation", "marked_for_pause": False}
            for owner in ("continuation", "short", "small")
        ],
    )
    result = inspect([row], [])
    assert result["counts"]["candidate_budgets_exceed_remaining_capacity_passes"] == 1
    assert result["counts"]["same_category_cross_workflow_capacity_competition_passes"] == 0


def test_siblings_from_one_workflow_do_not_create_cross_workflow_competition():
    row = choice()
    row["candidates"][1]["key"]["root_workflow_id"] = "workflow-short"
    assert inspect([row], [])["counts"]["same_category_cross_workflow_capacity_competition_passes"] == 0


def test_post_pressure_revocation_cannot_be_counted_as_consumed_eligibility():
    row = choice()
    row["candidates"][0]["active_after_pressure"] = False
    result = inspect([row], [service("short", 10200.)])
    assert result["counts"]["unique_selected_attempts_with_following_service"] == 0
    assert result["episodes"][0]["following_native_service_ms"] is None


def test_prior_service_and_a_different_attempt_cannot_complete_a_live_selection():
    result = inspect([choice()], [
        service("short", 9000.), service("short", 10100., attempt=1),
    ])
    assert result["counts"]["unique_selected_attempts_with_following_service"] == 0


def test_repeated_selections_keep_episode_and_unique_attempt_counts_separate():
    first, second = choice(), choice()
    second["monotonic_s"] = 10.1
    result = inspect([first, second], [service("short", 10200.)])
    assert result["counts"]["selected_agent_episodes"] == 2
    assert result["counts"]["active_selection_episodes_followed_by_service"] == 2
    assert result["counts"]["unique_selected_attempts_with_following_service"] == 1


@pytest.mark.parametrize("capacity", (-20, 0, 100))
def test_empty_budget_preserves_the_original_fast_return(capacity):
    row = choice()
    row.update(capacity_tokens=capacity, selected_tokens=0, ordered=[], selected_in_priority_order=[])
    assert not inspect_choice(row)["selected_set_changed"]


@pytest.mark.parametrize("field,value", (
    ("selected_tokens", 699), ("selected_in_priority_order", ["small"]),
    ("ordered", [["short", 0, 0, 600, 2, None], ["short", 0, 2, 300, 1, None]]),
))
def test_malformed_or_nonreproducing_selection_is_rejected(field, value):
    row = deepcopy(choice())
    row[field] = value
    with pytest.raises(ValueError):
        inspect_choice(row)


def test_audit_rejects_mixed_native_clock_domains():
    first, second = service("short", 10100.), service("short", 10200.)
    second["monotonic_clock_domain"] = "different-host"
    with pytest.raises(ValueError, match="clock domains"):
        inspect([choice()], [first, second])
