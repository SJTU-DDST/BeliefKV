from collections import defaultdict
from copy import deepcopy

import pytest

from scripts.audit_free_capacity_choices import service_progress
from scripts.audit_periodic_admission_decisions import PublishedGrantInterventions
from tests.test_free_choice_progress import progress_evidence


def append_replacement(rows, *, donor=None):
    position, grant = next(
        (index, row) for index, row in reversed(list(enumerate(rows)))
        if row.get("event") == "agent_dispatch_free_capacity_admitted"
    )
    consumer = grant["programs"][0]["key"]
    donor = donor or {
        **consumer, "request_id": "donor-request", "invocation_id": "donor",
        "context_id": "donor-context", "session_id": "donor-session",
    }
    marker = {
        "event": "agent_predicted_tool_replacement",
        "monotonic_s": grant["monotonic_s"] + .005,
        "consumer": deepcopy(consumer), "donor": deepcopy(donor),
        "remaining_before_tokens": grant["remaining_before_tokens"],
        "remaining_after_tokens": grant["remaining_after_tokens"],
        "coalescing_window_bypassed": True, "ordinary_restore_lease": False,
        "physical_capacity_authority": "settled_private_release_then_fresh_native_budget",
    }
    position += 1
    if rows[position].get("event") == "agent_free_capacity_choice":
        position += 1
    rows.insert(position, marker)
    return position, marker


def track(rows):
    tracker, changes = PublishedGrantInterventions(), defaultdict(list)
    for sequence, row in enumerate(rows):
        tracker.observe(row, sequence, changes)
    return tracker, changes


def test_same_replacement_metadata_does_not_erase_its_consumers_new_service(monkeypatch, tmp_path):
    choices, rows, batches = progress_evidence(monkeypatch, tmp_path)
    append_replacement(rows)
    result = service_progress(choices, rows, batches)
    assert result["counts"]["ready_grants_completed_useful_batch_within_observation_window"] == 2
    assert result["published_grant_interventions"]["linked_tool_replacement_markers"] == 1


def test_linked_marker_still_closes_the_donors_window(monkeypatch, tmp_path):
    choices, rows, batches = progress_evidence(monkeypatch, tmp_path)
    first = next(row for row in rows if row.get("event") == "agent_dispatch_free_capacity_admitted")
    donor = first["programs"][0]["key"]
    append_replacement(rows, donor=donor)
    result = service_progress(choices, rows, batches)
    episodes = {row["key"][2]: row for row in result["ready_grant_episodes"]}
    assert not episodes[donor["invocation_id"]]["new_launch_before_next_control_or_intervention"]
    assert result["counts"]["ready_grants_completed_useful_batch_within_observation_window"] == 1


@pytest.mark.parametrize("mutation", (
    "attempt", "session", "request", "before_budget", "after_budget",
    "early_marker", "ordinary_lease", "missing_authority", "unrelated_record",
))
def test_unproven_link_preserves_conservative_intervention(
    monkeypatch, tmp_path, mutation,
):
    choices, rows, batches = progress_evidence(monkeypatch, tmp_path)
    index, marker = append_replacement(rows)
    if mutation == "attempt":
        marker["consumer"]["attempt_id"] += 1
    elif mutation == "session":
        marker["consumer"]["session_generation"] += 1
    elif mutation == "request":
        marker["consumer"]["request_id"] = "different-request"
    elif mutation == "before_budget":
        marker["remaining_before_tokens"] += 1
    elif mutation == "after_budget":
        marker["remaining_after_tokens"] += 1
    elif mutation == "early_marker":
        marker["monotonic_s"] -= .01
    elif mutation == "ordinary_lease":
        marker["ordinary_restore_lease"] = True
    elif mutation == "missing_authority":
        marker.pop("physical_capacity_authority")
    else:
        rows.insert(index, {"event": "different_boundary"})
    result = service_progress(choices, rows, batches)
    assert result["published_grant_interventions"]["linked_tool_replacement_markers"] == 0
    assert result["published_grant_interventions"]["unlinked_tool_replacement_markers"] == 1
    assert result["counts"]["ready_grants_completed_useful_batch_within_observation_window"] == (
        2 if mutation == "early_marker" else 1
    )


def test_link_is_optional_observation_independent_and_never_reused(monkeypatch, tmp_path):
    _choices, rows, _batches = progress_evidence(monkeypatch, tmp_path)
    position, marker = append_replacement(rows)
    # A grant with no competitive alternatives does not emit a choice record.
    rows.pop(position - 1)
    position -= 1
    tracker, changes = track(rows)
    owner = marker["consumer"]["invocation_id"]
    assert len(changes[owner]) == 1
    assert len(tracker.links) == 1
    duplicate = {**deepcopy(marker), "monotonic_s": marker["monotonic_s"] + .001}
    rows.insert(position + 1, duplicate)
    tracker, changes = track(rows)
    assert len(tracker.links) == 1 and tracker.unlinked_replacements == 1
    assert len(changes[owner]) == 2


def test_choice_for_another_grant_cannot_bridge_the_records(monkeypatch, tmp_path):
    _choices, rows, _batches = progress_evidence(monkeypatch, tmp_path)
    position, _marker = append_replacement(rows)
    rows[position - 1]["grant_monotonic_s"] -= 1.
    tracker, _changes = track(rows)
    assert not tracker.links and tracker.unlinked_replacements == 1
