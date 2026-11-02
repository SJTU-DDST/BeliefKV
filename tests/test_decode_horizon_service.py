from copy import deepcopy
from dataclasses import asdict, replace
import json

import pytest

from beliefkv.policy.workflow_combination import (
    CombinationCandidate, CombinationCosts, CombinationResources, choose_combination,
)
from scripts.audit_decode_horizon_service import inspect
from scripts.audit_workflow_combination_choices import inspect as selections
from tests.test_workflow_size_selection_audit import key, service


def evidence(*, source="periodic_decay_resume", required=()):
    costs = CombinationCosts(
        .2, .001, (1., 1.1), 1000., decode_steps=64, decode_cost_basis="full_batch",
    )
    rows = [
        CombinationCandidate(
            owner, "workflow-" + owner, 200, 0, index, 1. if owner != "c" else .25, owner,
            input_tokens=tokens, context_tokens=1000,
        )
        for index, (owner, tokens) in enumerate((("a", 10), ("b", 100), ("c", 100)))
    ]
    resources = CombinationResources(
        500, 2000, 2048, 2, device_capacity_tokens=2000,
    )
    decision = choose_combination(
        rows, ("a", "b"), resources, costs, required=required,
        clock=lambda: 0, allow_feasible_resource_alternatives=True,
    )
    selection = {
        "event": "workflow_combination_selection", "source": source, "monotonic_s": 10.,
        "physical_capacity_credit": False, "resource_fallback_selection_enabled": True,
        "device_fit_scope": "planned_pool_geometry", "cost_identity": "horizon64",
        "decision": decision, "candidates": [asdict(row) for row in rows],
        "resources": asdict(resources), "keys": {row.identity: key(row.identity) for row in rows},
        "metadata_and_selection_cpu_ns": 1000, "metadata_and_selection_wall_ns": 1000,
    }
    selected = set(decision["selected"])
    members = [{
        "invocation_id": row.identity, "key": key(row.identity), "active_before": False,
        "pending_before": True, "acting_before": False, "marked_before": False,
        "application_ready": True, "active_after_pressure": row.identity in selected,
        "marked_after_pressure": False, "complete_budget_tokens": row.logical_tokens,
    } for row in rows]
    control = {
        "event": "agent_periodic_admission_decision", "monotonic_s": 10.,
        "resume_capacity_tokens": 500, "resumed_program_ids": list(decision["selected"]),
        "physical_capacity_credit": False, "programs": members,
    }
    grant = control
    if source == "undiscounted_free_dispatch":
        # Free grants charge the complete context and buffer; the fixture keeps
        # input-cost differences while both native contexts occupy 100 tokens.
        for row in selection["candidates"]:
            row["context_tokens"] = 100
        grant = {
            "event": "agent_dispatch_free_capacity_admitted",
            "ts_ms": 10000.5, "monotonic_s": 10.0005,
            "programs": [{
                "key": key(owner), "observed_context_tokens": 100,
                "complete_budget_tokens": 200,
            } for owner in decision["selected"]],
            "remaining_before_tokens": 500,
            "remaining_after_tokens": 500 - 200 * len(selected),
            "required_tokens": 200 * len(selected), "native_capacity_authority_preserved": True,
        }
    later = deepcopy(control)
    later.update(monotonic_s=12., resumed_program_ids=[])
    batches = []
    for owner in decision["selected"]:
        batch = service(owner, 10002.)
        batch.update(launch_monotonic_ms=10001., complete_monotonic_ms=10003., phase="prefill")
        batch["request_samples"][0].update(phase="prefill", token_delta=10, output_token_delta=0)
        batches.append(batch)
    return json.loads(json.dumps([selection, grant, later])), batches, costs


@pytest.mark.parametrize("source", ("periodic_decay_resume", "undiscounted_free_dispatch"))
def test_horizon_preserved_reference_member_needs_actual_grant_and_bounded_useful_service(source):
    records, batches, costs = evidence(source=source)
    original = deepcopy(records)
    result = inspect(records, batches, costs, "horizon64", {"workflow-a", "workflow-b"})
    assert records == original
    assert result["counts"]["horizon_selects_more_members"] == 1
    assert result["counts"]["extra_reference_members"] == 1
    assert result["counts"]["extra_actual_grants"] == 1
    assert result["counts"]["extra_followed_by_exact_native_service"] == 1
    assert result["counts"]["extra_bounded_new_useful_completions"] == 1
    assert result["counts"]["extra_selected_episodes"] == 1
    assert result["original_reference_new_member_audit_counts"]["newly_selected_agent_episodes"] == 0


def test_all_selected_service_does_not_change_existing_reference_outsider_counts():
    records, batches, costs = evidence()
    old = selections(
        records, batches, costs, "horizon64",
        allow_feasible_resource_alternatives=True, plan_device_capacity=True,
    )
    extended = selections(
        records, batches, costs, "horizon64", allow_feasible_resource_alternatives=True,
        plan_device_capacity=True, include_all_selected_service=True,
    )
    assert old["counts"] == extended["counts"]
    assert old["decisions"][0]["periodic_service"] == extended["decisions"][0]["periodic_service"] == []
    assert len(extended["decisions"][0]["all_periodic_service"]) == 2


def test_retained_reference_free_member_excludes_separate_tool_replacement_and_keeps_open_windows_unknown():
    records, batches, costs = evidence(source="undiscounted_free_dispatch")
    opened = inspect(records[:2], batches, costs, "horizon64", set())
    assert opened["counts"]["extra_open_progress_windows"] == 1
    assert opened["counts"]["extra_bounded_new_useful_completions"] == 0
    assert opened["comparisons"][0]["extra_members"][0]["progress_episode"][
        "new_completion_before_next_control_or_intervention"
    ] is None
    records.insert(2, {
        "event": "agent_predicted_tool_replacement", "monotonic_s": 10.0007,
        "consumer": key("b"), "donor": key("donor"),
    })
    result = inspect(records, batches, costs, "horizon64", set())
    assert result["counts"]["extra_actual_grants"] == 1
    assert result["counts"]["extra_direct_grants"] == 0
    assert result["counts"]["extra_separate_tool_replacement_grants"] == 1
    assert result["counts"].get("extra_bounded_new_useful_completions", 0) == 0


@pytest.mark.parametrize("change", ("missing_grant", "foreign_attempt", "real_pause", "zero_progress"))
def test_reference_preservation_cannot_invent_or_misattribute_service(change):
    records, batches, costs = evidence()
    if change == "missing_grant":
        records = records[:1]
    elif change == "foreign_attempt":
        for batch in batches:
            batch["request_samples"][0]["attempt_id"] = 9
    elif change == "real_pause":
        records.insert(2, {
            "event": "agent_full_offload_paused", "monotonic_s": 10.0005, "key": key("b"),
        })
    else:
        for batch in batches:
            batch["request_samples"][0].update(token_delta=0, output_token_delta=0)
    result = inspect(records, batches, costs, "horizon64", set())
    assert result["counts"].get("extra_bounded_new_useful_completions", 0) == 0


def test_join_rights_remain_required_and_unavailable_metadata_is_not_a_horizon_effect():
    records, batches, costs = evidence(required=("b",))
    result = inspect(records, batches, costs, "horizon64", set())
    assert result["counts"].get("extra_selected_episodes", 0) == 0
    records[0]["decision"] = {
        "selected": ["a", "b"], "reason": "metadata_unavailable_reference_fallback",
    }
    records[0].update(keys={}, candidates=[], resources=None)
    result = inspect(records, batches, costs, "horizon64", set())
    assert result["counts"]["metadata_unavailable"] == 1
    assert not result["comparisons"]


def test_another_actual_horizon_and_implicit_service_extension_are_rejected():
    records, batches, costs = evidence()
    with pytest.raises(ValueError, match="64-step"):
        inspect(records, batches, replace(costs, decode_steps=16), "horizon64", set())
    with pytest.raises(ValueError, match="explicitly"):
        selections(records, batches, costs, "horizon64", include_all_selected_service="yes")
