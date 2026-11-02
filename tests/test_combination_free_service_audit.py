from copy import deepcopy

import pytest

from scripts.audit_workflow_combination_choices import inspect
from tests.test_combination_choice_audit import service_evidence
from tests.test_workflow_size_selection_audit import key


def evidence():
    records, batches, costs = service_evidence()
    selection, periodic, later = records
    selection["source"] = "undiscounted_free_dispatch"
    chosen = set(selection["decision"]["selected"])
    members = [{
        "key": selection["keys"][raw["identity"]],
        "observed_context_tokens": raw["context_tokens"],
        "complete_budget_tokens": raw["logical_tokens"],
    } for raw in selection["candidates"] if raw["identity"] in chosen]
    required = sum(row["complete_budget_tokens"] for row in members)
    grant = {
        "event": "agent_dispatch_free_capacity_admitted", "ts_ms": 10000.5,
        "monotonic_s": 10.0005, "programs": members,
        "remaining_before_tokens": required, "remaining_after_tokens": 0,
        "required_tokens": required, "native_capacity_authority_preserved": True,
    }
    close = {"event": "workflow_oracle_coalesced_dispatch", "monotonic_s": 9.999}
    return [selection, grant, close, later], batches, costs


def audit(records, batches, costs):
    return inspect(records, batches, costs, "frozen")


def test_free_selection_requires_its_exact_committed_grant_and_new_bounded_native_progress():
    records, batches, costs = evidence()
    result = audit(records, batches, costs)
    expected = len(records[0]["decision"]["selected"])
    counts = result["counts"]
    assert counts["newly_selected_free_episodes"] == expected
    assert counts["newly_selected_free_actual_grant_verified"] == expected
    assert counts["newly_selected_free_followed_by_exact_service"] == expected
    assert counts["newly_selected_free_with_bounded_useful_completion"] == expected
    assert not result["scope"]["free_dispatch_selection_replayed_but_service_not_associated"]
    assert result["counts"].get("newly_selected_periodic_episodes", 0) == 0


@pytest.mark.parametrize("change", ("attempt", "duplicate_grant", "budget", "authority", "selected_budget"))
def test_changed_attempt_duplicate_or_invalid_logical_authority_is_rejected(change):
    records, batches, costs = evidence()
    if change == "attempt":
        records[1]["programs"][0]["key"] = {
            **records[1]["programs"][0]["key"], "attempt_id": 9,
        }
    elif change == "duplicate_grant":
        records.insert(2, deepcopy(records[1]))
    elif change == "budget":
        records[1]["required_tokens"] += 1
    elif change == "selected_budget":
        records[1]["programs"][0]["complete_budget_tokens"] += 1
        records[1]["programs"][0]["observed_context_tokens"] += 1
        records[1]["required_tokens"] += 1
        records[1]["remaining_before_tokens"] += 1
    else:
        records[1]["native_capacity_authority_preserved"] = False
    with pytest.raises(ValueError):
        audit(records, batches, costs)


@pytest.mark.parametrize("change", ("after_close", "after_control", "after_next_selection"))
def test_a_later_dispatch_cannot_consume_an_earlier_ungranted_selection(change):
    records, batches, costs = evidence()
    grant = records.pop(1)
    if change == "after_close":
        records.insert(2, grant)
    elif change == "after_control":
        records = [records[0], records[-1], grant]
    else:
        next_choice = deepcopy(records[0])
        next_choice["source"] = "periodic_decay_resume"
        next_choice["decision"] = {
            "selected": [], "reason": "metadata_unavailable_reference_fallback",
        }
        records.insert(1, next_choice)
        records.insert(2, grant)
    result = audit(records, batches, costs)
    assert result["counts"]["newly_selected_free_actual_grant_verified"] == 0
    assert result["counts"]["newly_selected_free_followed_by_exact_service"] == 0


@pytest.mark.parametrize("change", ("earlier", "foreign_attempt", "zero_progress", "actual_pause", "planned_pause"))
def test_free_progress_preserves_exact_attempt_positive_work_and_real_intervention_bounds(change):
    records, batches, costs = evidence()
    expected = len(records[0]["decision"]["selected"])
    if change == "earlier":
        for batch in batches:
            batch.update(launch_monotonic_ms=9997., service_start_monotonic_ms=9998.,
                         complete_monotonic_ms=9999.)
    elif change == "foreign_attempt":
        for batch in batches:
            batch["request_samples"][0]["attempt_id"] = 1
    elif change == "zero_progress":
        for batch in batches:
            batch["request_samples"][0].update(token_delta=0, output_token_delta=0)
    else:
        for owner in records[0]["decision"]["selected"]:
            records.insert(2, {
                "event": "workflow_remaining_pressure_pause", "monotonic_s": 10.0007,
                "selected": owner, "marked_for_pause": change == "planned_pause",
            })
    result = audit(records, batches, costs)
    assert result["counts"]["newly_selected_free_actual_grant_verified"] == expected
    assert result["counts"]["newly_selected_free_with_bounded_useful_completion"] == (
        expected if change == "planned_pause" else 0
    )


def test_missing_grant_or_open_prefix_stays_unknown():
    records, batches, costs = evidence()
    missing = audit([records[0], records[2], records[3]], batches, costs)
    assert missing["counts"]["newly_selected_free_without_grant_evidence"] > 0
    opened = audit(records[:2], batches, costs)
    assert opened["counts"]["newly_selected_free_actual_grant_verified"] > 0
    assert all(row["episode"]["new_completion_before_next_control_or_intervention"] is None
               for row in opened["decisions"][0]["free_service"])


def test_metadata_fallback_with_missing_reference_keys_cannot_claim_free_selection_consumption():
    records, batches, costs = evidence()
    records[0]["decision"] = {
        "selected": records[0]["decision"]["selected"],
        "reason": "metadata_unavailable_reference_fallback",
    }
    records[0].update(keys={}, candidates=[], resources=None)
    result = audit(records, batches, costs)
    assert result["counts"]["newly_selected_free_episodes"] == 0
    assert result["decisions"][0]["selected_set_changed"] is None
    assert result["decisions"][0]["free_service"] == []


def test_separate_tool_replacement_cannot_be_credited_to_an_open_free_selection():
    records, batches, costs = evidence()
    count = len(records[0]["decision"]["selected"])
    records.insert(2, {
        "event": "agent_predicted_tool_replacement", "monotonic_s": 10.0007,
        "consumer": records[1]["programs"][0]["key"], "donor": key("donor"),
    })
    result = audit(records, batches, costs)
    assert result["counts"]["newly_selected_free_actual_grant_verified"] == count
    assert result["counts"]["newly_selected_free_granted_by_separate_tool_replacement"] == 1
    assert result["counts"]["newly_selected_free_direct_dispatch_grant_verified"] == count - 1
    assert result["counts"]["newly_selected_free_with_bounded_useful_completion"] == count - 1
