from copy import deepcopy

import pytest

from scripts.audit_free_capacity_choices import EVENT, inspect
from tests.test_free_capacity_choices import waiting


def evidence(monkeypatch, tmp_path, **kwargs):
    live, adapter, _requests, records = waiting(monkeypatch, tmp_path, **kwargs)
    try:
        assert adapter._admit_free_continuation()
    finally:
        live.close()
    return records


def test_actual_grants_are_bound_and_dispatch_records_are_deduplicated(monkeypatch, tmp_path):
    report = inspect(evidence(monkeypatch, tmp_path))
    assert report["instrumentation"] == "final_counters_reconciled"
    assert report["observed_conflict_grant_records"] == 2
    assert report["deduplicated_conflict_decisions"] == 1
    assert report["deduplicated_ready_unprotected_decisions"] == 1


def test_observed_zero_is_distinct_from_uninstrumented_history(monkeypatch, tmp_path):
    rows = evidence(monkeypatch, tmp_path, capacity=1500)
    report = inspect(rows)
    assert report["instrumentation"] == "final_counters_reconciled"
    assert report["observed_conflict_grant_records"] == 0
    for row in rows:
        if row["event"] == "admission_runtime_state":
            row["agent_admission"].pop("free_capacity_choice_observation_version")
    old = inspect(rows)
    assert old["instrumentation"] == "unobserved"
    assert old["observed_conflict_grant_records"] is None
    assert old["deduplicated_conflict_decisions"] is None


@pytest.mark.parametrize("change", ("missing_grant", "capacity", "member", "clock", "duplicate"))
def test_unbound_or_changed_grants_are_rejected(monkeypatch, tmp_path, change):
    rows = evidence(monkeypatch, tmp_path)
    choice = next(row for row in rows if row["event"] == EVENT)
    if change == "missing_grant":
        rows.remove(next(row for row in rows if row["event"] == choice["grant_event"]))
    elif change == "capacity":
        choice["remaining_before_tokens"] += 1
    elif change == "member":
        choice["candidates"][0]["key"]["attempt_id"] += 1
    elif change == "clock":
        choice["monotonic_s"] = choice["grant_monotonic_s"] + 1
    else:
        rows.insert(rows.index(choice) + 1, deepcopy(choice))
    with pytest.raises(ValueError):
        inspect(rows)


@pytest.mark.parametrize("change", ("all_fit", "single_workflow", "nonready", "duplicate_agent"))
def test_invalid_alternative_sets_are_rejected(monkeypatch, tmp_path, change):
    rows = evidence(monkeypatch, tmp_path)
    choice = next(row for row in rows if row["event"] == EVENT)
    if change == "all_fit":
        choice["candidates"].pop()
    elif change == "single_workflow":
        for member in choice["candidates"][1:]:
            member["key"]["root_workflow_id"] = choice["candidates"][0]["key"]["root_workflow_id"]
    elif change == "nonready":
        choice["candidates"][-1]["application_ready"] = False
    else:
        choice["candidates"].append(deepcopy(choice["candidates"][-1]))
    with pytest.raises(ValueError):
        inspect(rows)


@pytest.mark.parametrize("field", ("all_selected_application_ready", "protected_join_present",
                                  "ready_unprotected_competition"))
def test_readiness_and_join_labels_are_recomputed(monkeypatch, tmp_path, field):
    rows = evidence(monkeypatch, tmp_path)
    choice = next(row for row in rows if row["event"] == EVENT)
    choice[field] = not choice[field]
    with pytest.raises(ValueError, match="classification"):
        inspect(rows)


def test_missing_records_in_a_completed_journal_are_rejected(monkeypatch, tmp_path):
    rows = evidence(monkeypatch, tmp_path)
    rows.remove(next(row for row in rows if row["event"] == EVENT))
    with pytest.raises(ValueError, match="counters"):
        inspect(rows)


def test_a_live_prefix_cannot_claim_final_counter_reconciliation(monkeypatch, tmp_path):
    rows = evidence(monkeypatch, tmp_path)
    rows = [row for row in rows if row["event"] != "admission_runtime_state"]
    assert inspect(rows)["instrumentation"] == "observed_live_prefix"


def test_protected_and_warming_choices_are_not_rankable(monkeypatch, tmp_path):
    live, adapter, _requests, rows = waiting(monkeypatch, tmp_path)
    try:
        adapter.policy.programs["a0"].pending = False
        assert adapter._admit_from_free_capacity(
            (adapter.policy.programs["a0"],), event="agent_join_free_capacity_admitted", join_id="join",
        )
    finally:
        live.close()
    report = inspect(rows)
    assert report["deduplicated_conflict_decisions"] == 1
    assert report["deduplicated_ready_unprotected_decisions"] == 0
    assert report["counts"]["warming_target_conflict_grant_records"] == 1
    assert report["bounded_index_direct_decisions"] == 1
