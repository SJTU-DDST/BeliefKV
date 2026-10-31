from copy import deepcopy

import pytest

from scripts.audit_free_capacity_choices import (
    EVENT, choice_identity, choice_phases, inspect, service_progress,
)
from scripts.audit_periodic_admission_decisions import observation_phases
from tests.test_free_capacity_choice_audit import evidence
from tests.test_free_capacity_choices import waiting
from tests.test_periodic_admission_decisions import phase_evidence


def progress_evidence(monkeypatch, tmp_path):
    rows = evidence(monkeypatch, tmp_path)
    choices = inspect(rows)
    batches = []
    for row in choices["observations"]:
        selected = next(member for member in row["candidates"] if member["selected"])
        key = selected["key"]
        native_key = {**key, "workflow_id": key["root_workflow_id"]}
        native_key.pop("root_workflow_id")
        when = row["grant_monotonic_s"] * 1000.
        batches.append({
            "event": "gpu_service_sample", "monotonic_clock_domain": "fixture-host",
            "launch_monotonic_ms": when + 10.,
            "service_start_monotonic_ms": when + 15.,
            "complete_monotonic_ms": when + 100.,
            "phase": "prefill", "request_samples": [{
                **native_key, "phase": "prefill", "token_delta": 5,
                "output_token_delta": 0,
            }],
        })
    rows.append({
        "event": "agent_periodic_admission_decision",
        "monotonic_s": choices["observations"][-1]["grant_monotonic_s"] + 1.,
    })
    return choices, rows, batches


def test_free_conflicts_require_new_useful_native_batches_and_deduplicate_dispatch(monkeypatch, tmp_path):
    report = service_progress(*progress_evidence(monkeypatch, tmp_path))
    assert report["counts"]["selected_ready_grant_episodes"] == 2
    assert report["counts"]["ready_grants_completed_useful_batch_within_observation_window"] == 2
    assert report["deduplicated_decisions_with_bounded_useful_completion"] == 1
    assert report["deduplicated_unprotected_decisions_with_bounded_useful_completion"] == 1
    assert report["within_window_new_completion_ms"]["p50"] == pytest.approx(100., abs=.01)


@pytest.mark.parametrize("change", ("earlier_launch", "foreign_attempt", "empty_decode"))
def test_old_foreign_or_empty_service_does_not_validate_a_free_grant(monkeypatch, tmp_path, change):
    choices, rows, batches = progress_evidence(monkeypatch, tmp_path)
    for choice, batch in zip(choices["observations"], batches):
        if change == "earlier_launch":
            batch["launch_monotonic_ms"] = choice["grant_monotonic_s"] * 1000. - 1.
        elif change == "foreign_attempt":
            batch["request_samples"][0]["attempt_id"] += 1
        else:
            batch["request_samples"][0].update(phase="decode", token_delta=0)
    report = service_progress(choices, rows, batches)
    assert report["deduplicated_decisions_with_bounded_useful_completion"] == 0
    assert report["counts"]["ready_grants_with_observed_new_useful_launch"] == 0


def test_later_control_cannot_validate_a_free_grant_from_earlier_in_the_journal(monkeypatch, tmp_path):
    choices, rows, batches = progress_evidence(monkeypatch, tmp_path)
    rows[-1]["monotonic_s"] = choices["observations"][-1]["grant_monotonic_s"] + .05
    report = service_progress(choices, rows, batches)
    assert report["counts"]["ready_grants_served_within_observation_window"] == 2
    assert report["counts"]["ready_grants_completed_useful_batch_within_observation_window"] == 0
    assert report["deduplicated_decisions_with_bounded_useful_completion"] == 0


def test_a_recorded_donor_pause_closes_the_same_agent_window(monkeypatch, tmp_path):
    choices, rows, batches = progress_evidence(monkeypatch, tmp_path)
    first = choices["observations"][0]
    key = next(row["key"] for row in first["candidates"] if row["selected"])
    parent = {**key, "invocation_id": "parent", "request_id": "parent-request"}
    rows.insert(-1, {
        "event": "agent_join_blocked_capacity_admitted",
        "monotonic_s": first["grant_monotonic_s"] + .005,
        "programs": [{"key": parent}], "donors": [{"key": key}],
    })
    report = service_progress(choices, rows, batches)
    episode = next(row for row in report["ready_grant_episodes"] if row["key"][0] == key["request_id"])
    assert episode["observation_window_end_reason"] == "same_agent_intervention"
    assert episode["following_native_service_ms"] is not None
    assert not episode["new_launch_before_next_control_or_intervention"]


def test_the_last_live_window_is_unknown_instead_of_a_failed_consumption(monkeypatch, tmp_path):
    choices, rows, batches = progress_evidence(monkeypatch, tmp_path)
    rows.pop()
    report = service_progress(choices, rows, batches)
    assert report["counts"]["ready_grants_with_open_observation_window"] == 2
    assert all(
        row["new_completion_before_next_control_or_intervention"] is None
        for row in report["ready_grant_episodes"]
    )


def test_unknown_instrumentation_does_not_consume_or_count_native_samples():
    def unavailable():
        pytest.fail("uninstrumented choices read native samples")
        yield
    report = service_progress(inspect([]), [], unavailable())
    assert report["instrumentation"] == "unobserved"
    assert report["counts"] is None


def test_service_prefix_requires_the_original_audited_grant(monkeypatch, tmp_path):
    choices, rows, batches = progress_evidence(monkeypatch, tmp_path)
    rows.remove(next(row for row in rows if row["event"] == choices["observations"][0]["grant_event"]))
    with pytest.raises(ValueError, match="every audited"):
        service_progress(choices, rows, batches)


def test_a_warming_parent_does_not_become_a_ready_execution_episode(monkeypatch, tmp_path):
    live, adapter, _requests, rows = waiting(monkeypatch, tmp_path)
    try:
        adapter.policy.programs["a0"].pending = False
        adapter._admit_from_free_capacity(
            (adapter.policy.programs["a0"],), event="agent_join_free_capacity_admitted", join_id="join",
        )
    finally:
        live.close()
    report = service_progress(inspect(rows), rows, [])
    assert report["counts"]["warming_selected_members_excluded"] == 1
    assert report["counts"]["selected_ready_grant_episodes"] == 0
    assert report["deduplicated_decisions_with_bounded_useful_completion"] == 0


def test_protected_ready_join_progress_is_reported_separately(monkeypatch, tmp_path):
    choices, rows, batches = progress_evidence(monkeypatch, tmp_path)
    for row in choices["observations"]:
        row["ready_unprotected_competition"] = False
    report = service_progress(choices, rows, batches)
    assert report["deduplicated_decisions_with_bounded_useful_completion"] == 1
    assert report["deduplicated_unprotected_decisions_with_bounded_useful_completion"] == 0


@pytest.mark.parametrize("change", ("missing_launch", "missing_complete", "missing_progress"))
def test_missing_native_evidence_does_not_establish_useful_completion(monkeypatch, tmp_path, change):
    choices, rows, batches = progress_evidence(monkeypatch, tmp_path)
    for batch in batches:
        if change == "missing_launch":
            batch.pop("launch_monotonic_ms")
        elif change == "missing_complete":
            batch.pop("complete_monotonic_ms")
        else:
            batch["request_samples"][0].pop("output_token_delta")
    report = service_progress(choices, rows, batches)
    assert report["deduplicated_decisions_with_bounded_useful_completion"] == 0


def test_choice_phases_keep_the_common_window_half_open_and_separate_later_JCT_work(monkeypatch, tmp_path):
    original = inspect(evidence(monkeypatch, tmp_path))["observations"][0]
    phases = observation_phases(*phase_evidence())
    origin = phases["origin_monotonic_ms"] / 1000.
    fixed = phases["phases"]["fixed_common_throughput_window"]
    rows = []
    for sequence, when in enumerate((fixed["start_s"] - 1., fixed["start_s"],
                                     fixed["end_s"], fixed["end_s"] + 1.)):
        row = deepcopy(original)
        row.update(grant_monotonic_s=origin + when, dispatch_sequence=sequence + 1)
        rows.append(row)
    result = {
        "instrumentation": "observed_live_prefix", "observations": rows,
        "native_progress": {"counts": {}, "ready_grant_episodes": [{
            "selection_ms": row["grant_monotonic_s"] * 1000.,
            "choice_identity": list(choice_identity(row)),
            "ready_unprotected_competition": True,
            "new_completion_before_next_control_or_intervention": True,
        } for row in rows]},
    }
    report = choice_phases(result, phases)
    common = report["phases"]["fixed_common_throughput_window"]
    later = report["phases"]["measured_execution_after_common_window"]
    whole = report["phases"]["measured_execution_observation_span"]
    assert common["deduplicated_conflict_decisions"] == 1
    assert later["deduplicated_conflict_decisions"] == 2
    assert whole["deduplicated_decisions_with_bounded_useful_completion"] == 3
    assert not report["scope"]["workload_acceptance_gate_changed"]


def test_unobserved_phase_counts_remain_null():
    report = choice_phases(inspect([]), observation_phases(*phase_evidence()))
    assert all(row["deduplicated_conflict_decisions"] is None for row in report["phases"].values())
