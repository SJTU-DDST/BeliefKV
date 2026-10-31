from copy import deepcopy
from dataclasses import asdict
import json
from types import SimpleNamespace as NS

import pytest

from beliefkv.policy.workflow_combination import (
    CombinationCandidate as Candidate, CombinationCosts as Costs,
    CombinationResources as Resources, choose_combination,
)
from scripts.audit_workflow_combination_choices import inspect, replay_choice
from tests.test_workflow_size_selection_audit import key, service


def evidence(*, deadline=None, required=()):
    costs = Costs(.01, .001, tuple(1. + index * .1 for index in range(24)), 10000.)
    rows = [
        Candidate(owner, "workflow-" + owner, tokens + 100, 0, index, weight, owner,
                  missing_extents=(((1, 2.), 10),), input_tokens=1, context_tokens=tokens)
        for index, (owner, tokens, weight) in enumerate((
            ("a", 300, .2), ("b", 150, .9), ("c", 150, .9), ("d", 150, .9),
        ))
    ]
    resources = Resources(500, 10000, 2048, 4)
    calls = -1

    def clock():
        nonlocal calls
        calls += 1
        return 500_000 if deadline is not None and calls >= deadline else 0

    decision = choose_combination(
        rows, ["a"], resources, costs, required=required, clock=clock,
    )
    row = {
        "event": "workflow_combination_selection", "source": "periodic_decay_resume",
        "monotonic_s": 10., "physical_capacity_credit": False,
        "cost_identity": "frozen", "decision": decision,
        "candidates": [asdict(value) for value in rows],
        "resources": asdict(resources), "keys": {owner.identity: key(owner.identity) for owner in rows},
        "metadata_and_selection_cpu_ns": 600000, "metadata_and_selection_wall_ns": 700000,
    }
    return json.loads(json.dumps(row)), costs


@pytest.mark.parametrize("deadline", (None, 1, 2))
@pytest.mark.parametrize("required", ((), ("a",)))
def test_replay_preserves_complete_and_cutoff_search_including_required_join(deadline, required):
    row, costs = evidence(deadline=deadline, required=required)
    result = replay_choice(row, costs, "frozen")
    assert result["search_replayed"]
    assert result["selected"] == row["decision"]["selected"]
    assert result["search_complete"] == row["decision"]["search_complete"]


@pytest.mark.parametrize("change", ("identity", "cost", "score", "count", "selection", "right"))
def test_changed_owner_cost_score_or_search_is_rejected(change):
    row, costs = evidence()
    if change == "identity":
        row["keys"]["b"]["attempt_id"] = -1
    elif change == "cost":
        row["cost_identity"] = "other"
    elif change == "score":
        row["decision"]["selected_evaluation"]["score"] += 1.
    elif change == "count":
        row["decision"]["evaluated_subsets"] -= 1
    elif change == "selection":
        row["decision"]["selected"] = ["a"]
    else:
        row["decision"]["required_reference_members"] = ["b"]
    with pytest.raises(ValueError):
        replay_choice(row, costs, "frozen")


def test_unavailable_metadata_is_not_a_reproduced_or_changed_choice():
    row, costs = evidence()
    row.update(candidates=[], resources=None, keys={})
    row["decision"] = {"selected": ["a"], "reason": "metadata_unavailable_reference_fallback"}
    result = replay_choice(row, costs, "frozen")
    assert not result["search_replayed"]
    assert result["selected_set_changed"] is None


def service_evidence():
    selection, costs = evidence()
    selected = selection["decision"]["selected"]
    control = {
        "event": "agent_periodic_admission_decision", "monotonic_s": 10.,
        "resume_capacity_tokens": 500, "resumed_program_ids": selected,
        "physical_capacity_credit": False,
        "programs": [{
            "invocation_id": raw["identity"], "key": key(raw["identity"]),
            "active_before": False, "pending_before": True, "acting_before": False,
            "marked_before": False, "application_ready": True,
            "active_after_pressure": raw["identity"] in selected,
            "marked_after_pressure": False, "complete_budget_tokens": raw["logical_tokens"],
        } for raw in selection["candidates"]],
    }
    later = deepcopy(control)
    later.update(monotonic_s=12., resumed_program_ids=[])
    batches = []
    for owner in selected:
        batch = service(owner, 10002.)
        batch.update(launch_monotonic_ms=10001., complete_monotonic_ms=10003., phase="prefill")
        batch["request_samples"][0].update(phase="prefill", token_delta=10, output_token_delta=0)
        batches.append(batch)
    return [selection, control, later], batches, costs


def test_actual_periodic_grant_and_bounded_progress_are_both_required():
    records, batches, costs = service_evidence()
    result = inspect(records, batches, costs, "frozen")
    selected = len(records[0]["decision"]["selected"])
    assert result["counts"]["selected_set_changes"] == 1
    assert result["counts"]["newly_selected_periodic_retained_after_pressure"] == selected
    assert result["counts"]["newly_selected_periodic_with_bounded_useful_completion"] == selected
    assert result["metadata_and_selection_cpu_us"]["p50"] == 600.


@pytest.mark.parametrize("change", ("attempt", "earlier", "pause", "revoked", "zero"))
def test_foreign_earlier_revoked_or_zero_progress_cannot_consume_a_choice(change):
    records, batches, costs = service_evidence()
    if change == "attempt":
        for batch in batches:
            batch["request_samples"][0]["attempt_id"] = 1
    elif change == "earlier":
        for batch in batches:
            batch.update(launch_monotonic_ms=9997., service_start_monotonic_ms=9998.,
                         complete_monotonic_ms=9999.)
    elif change == "pause":
        for owner in records[0]["decision"]["selected"]:
            records.insert(2, {
                "event": "agent_full_offload_paused", "monotonic_s": 10.0005, "key": key(owner),
            })
    elif change == "revoked":
        for member in records[1]["programs"]:
            member["active_after_pressure"] = False
    else:
        for batch in batches:
            batch["request_samples"][0].update(token_delta=0, output_token_delta=0)
    result = inspect(records, batches, costs, "frozen")
    assert result["counts"]["newly_selected_periodic_with_bounded_useful_completion"] == 0


def test_missing_actual_grant_or_open_window_is_unknown():
    records, batches, costs = service_evidence()
    result = inspect(records[:2], batches, costs, "frozen")
    assert result["counts"]["newly_selected_periodic_with_bounded_useful_completion"] == 0
    assert all(
        row["episode"]["new_completion_before_next_control_or_intervention"] is None
        for row in result["decisions"][0]["periodic_service"]
    )
    result = inspect(records[:1], batches, costs, "frozen")
    assert result["decisions"][0]["periodic_service"] == []


def test_following_actual_grant_cannot_change_selection_or_attempt():
    records, batches, costs = service_evidence()
    records[1]["resumed_program_ids"] = ["a"]
    with pytest.raises(ValueError, match="grant disagree"):
        inspect(records, batches, costs, "frozen")
    records, batches, costs = service_evidence()
    records[1]["programs"][1]["key"]["attempt_id"] = 2
    with pytest.raises(ValueError, match="selected request attempt"):
        inspect(records, batches, costs, "frozen")


def test_free_dispatch_is_replayed_without_inventing_a_periodic_grant():
    records, batches, costs = service_evidence()
    records[0]["source"] = "undiscounted_free_dispatch"
    result = inspect(records, batches, costs, "frozen")
    assert result["counts"]["searches_replayed"] == 1
    assert result["decisions"][0]["periodic_service"] == []
    assert result["scope"]["free_dispatch_selection_replayed_but_service_not_associated"]


@pytest.mark.parametrize("periodic", (False, True))
def test_adapter_accepts_real_runtime_journal(monkeypatch, tmp_path, periodic):
    from tests import test_global_workflow_oracle as global_oracle
    from tests.test_global_combination_oracle import queued
    original_request = global_oracle.request

    def request(*args, **kwargs):
        req = original_request(*args, **kwargs)
        req.session_id = "session-" + req.rid
        req.session_generation = 0
        return req

    monkeypatch.setattr(global_oracle, "request", request)
    runtime, admission = queued(monkeypatch, tmp_path, window_ms=0)
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        if periodic:
            admission.next_tick = 0.
        admission.tick()
        result = inspect(
            json.loads(json.dumps(records)), [], admission._combination.costs,
            admission._combination.cost_identity,
        )
        assert result["counts"]["searches_replayed"] >= 1
        if periodic:
            assert result["counts"]["periodic_selections_with_actual_grant_boundary"] >= 1
    finally:
        runtime.close()
