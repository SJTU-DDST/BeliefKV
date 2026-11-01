from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.policy.workflow_combination import (
    CombinationResources as Resources, _evaluate, choose_combination,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.audit_workflow_combination_choices import replay_choice
from scripts.prepare_calibrated_combination_oracle import calibrated_artifact
from tests.test_calibrated_combination_preparation import calibration, original
from tests.test_combination_full_batch_costs import costs
from tests.test_workflow_combination import candidate
from tests.test_workflow_combination_runtime import configured, runtime_with_queue
from tests.test_workflow_size_selection_audit import key


def rows():
    return [
        replace(
            candidate(name, 200, weight), context_tokens=200,
            missing_extents=(((name, 1.), 100),),
        )
        for name, weight in (("long", .2), ("short", 1.))
    ]


def resource():
    return Resources(300, 0, 2048, 2, device_capacity_tokens=1000)


def select(candidates=None, resources=None, **kwargs):
    return choose_combination(
        candidates or rows(), ("long",), resources or resource(), costs(),
        allow_feasible_resource_alternatives=True, clock=lambda: 0, **kwargs,
    )


def test_logical_plan_can_compare_alternatives_before_physical_space_is_released():
    strict = select(resources=replace(resource(), device_capacity_tokens=None))
    assert strict["selected"] == ("long",)
    assert strict["reason"] == "resource_fallback_no_feasible_subset"
    planned = select()
    assert planned["selected"] == ("short",)
    assert planned["selected_evaluation"]["device_fit_scope"] == "planned_pool_geometry"
    assert planned["selected_evaluation"]["current_device_summary_fits"] is False
    assert planned["selected_evaluation"]["required_device_tokens"] == 101


def test_current_fit_is_still_reported_without_inventing_more_free_capacity():
    free = replace(resource(), device_tokens=101)
    result = select(resources=free)
    assert result["selected_evaluation"]["current_device_summary_fits"] is True
    assert free.device_tokens == 101 and free.cold_device_tokens == 0


@pytest.mark.parametrize("change", (
    {"logical_tokens": 199}, {"slots": 0}, {"input_tokens": 0},
    {"device_capacity_tokens": 100}, {"growth_tokens": 900},
))
def test_logical_input_slot_pool_and_growth_limits_still_apply(change):
    assert _evaluate(rows()[:1], replace(resource(), **change), costs()) is None


@pytest.mark.parametrize("value", (0, -1, True, 1.5, "1000"))
def test_unknown_or_invalid_pool_geometry_cannot_enable_a_new_choice(value):
    result = select(resources=replace(resource(), device_capacity_tokens=value))
    assert result["selected"] == ("long",)
    assert result["reason"] == "unsupported_summary_reference_fallback"


def test_free_and_safe_cold_observations_cannot_exceed_the_observed_pool():
    result = select(resources=replace(
        resource(), device_tokens=900, cold_device_tokens=101,
    ))
    assert result["selected"] == ("long",)
    assert result["reason"] == "unsupported_summary_reference_fallback"


def test_shared_copy_extents_are_still_deduplicated():
    shared = [replace(row, missing_extents=((("shared", 1.), 100),)) for row in rows()]
    value = _evaluate(shared, replace(resource(), logical_tokens=400), costs())
    assert value["missing_unique_tokens"] == 100
    assert value["required_device_tokens"] == 102


def test_required_ready_join_cannot_be_replaced_by_short_work():
    parent = replace(
        candidate("parent", 100, .2), context_tokens=100,
    )
    result = choose_combination(
        [parent, *rows()], ("parent", "long"), resource(), costs(),
        required=("parent",), clock=lambda: 0,
        allow_feasible_resource_alternatives=True,
    )
    assert set(result["selected"]) == {"parent", "short"}
    assert result["required_reference_members"] == ("parent",)


def artifact(**overrides):
    return calibrated_artifact(
        original(), calibration(), calibration_path="/costs", calibration_sha256="a" * 64,
        include_unreferenced_capacity=True, allow_deep_prefix_aggregate=True,
        allow_feasible_resource_alternatives=True, full_batch_cost=True, **overrides,
    )


def test_preparation_fingerprints_the_new_planning_scope_without_changing_future_work():
    strict, planned = artifact(), artifact(plan_device_capacity=True)
    before, after = strict["admission_cost_profile"], planned["admission_cost_profile"]
    assert before["kind"] == "r5_frozen_batch_cost_proxy_capacity_v5"
    assert after["kind"] == "r5_frozen_batch_cost_proxy_capacity_v6"
    assert before["identity"] != after["identity"]
    assert before["costs"] == after["costs"]
    for name in ("requests", "workflow_graphs", "model_seconds_per_work_token"):
        assert strict[name] == planned[name]
    proof = planned["calibrated_model_time"]["capacity_summary_revision"]["device_capacity_planning"]
    assert proof["logical_selection_does_not_prove_immediate_physical_fit"]
    assert proof["native_allocation_locks_Host_and_DMA_authority_unchanged"]


@pytest.mark.parametrize("flag", (None, 1, "yes"))
def test_preparation_requires_an_explicit_boolean(flag):
    with pytest.raises(ValueError, match="device pool planning"):
        artifact(plan_device_capacity=flag)


def test_preparation_cannot_enable_planning_without_full_batch_capacity_contract():
    with pytest.raises(ValueError, match="device pool planning"):
        calibrated_artifact(
            original(), calibration(), calibration_path="/costs",
            calibration_sha256="a" * 64, plan_device_capacity=True,
        )


@pytest.mark.parametrize("basis,valid", (("incremental", False), ("full_batch", True)))
def test_actual_constructor_requires_the_declared_planning_and_cost_profile(
    monkeypatch, tmp_path, basis, valid,
):
    config = configured(tmp_path)
    path = Path(config["WORKFLOW_WORK_MODEL"])
    raw = json.loads(path.read_bytes())
    raw["admission_cost_profile"]["kind"] = "r5_frozen_batch_cost_proxy_capacity_v6"
    raw["admission_cost_profile"]["costs"]["decode_cost_basis"] = basis
    path.write_text(json.dumps(raw))
    config["WORKFLOW_WORK_MODEL_SHA256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name, value in arm_environment(config, "full").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    if not valid:
        with pytest.raises(ValueError, match="full-batch cost"):
            NativeAdmissionRuntime(event_socket_path=str(tmp_path / "planning.sock"))
        return
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "planning.sock"))
    try:
        controller = runtime._agent_admission._combination
        assert controller.plan_device_capacity
        assert controller.allow_feasible_resource_alternatives
        assert controller.allow_deep_prefix_aggregate
        assert controller.include_unreferenced_capacity
    finally:
        runtime.close()


@pytest.mark.parametrize("capacity", (500, None, 0, True))
def test_runtime_plans_from_actual_pool_geometry_and_never_credits_physical_pages(
    monkeypatch, tmp_path, capacity,
):
    from beliefkv.runtime import workflow_combination as combination

    runtime, admission, _env = runtime_with_queue(monkeypatch, tmp_path)
    controller = admission._combination
    controller.plan_device_capacity = True
    controller.allow_feasible_resource_alternatives = True
    controller.costs = replace(controller.costs, decode_cost_basis="full_batch")
    runtime._native_cache.token_to_kv_pool_allocator.size_full = capacity
    monkeypatch.setattr(combination, "observe_static_full_mamba_headroom", lambda _cache: NS(
        observable=True, device_full_free_tokens=0, device_mamba_free_slots=0,
    ))
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        admission.tick()
        row = next(row for row in records if row["event"] == "workflow_combination_selection")
        assert row["physical_capacity_credit"] is False
        if type(capacity) is int and capacity > 0:
            assert row["resources"]["device_tokens"] == 0
            assert row["resources"]["device_capacity_tokens"] == 500
            assert row["device_fit_scope"] == "planned_pool_geometry"
            assert row["decision"]["selected_evaluation"]["current_device_summary_fits"] is False
            assert {p.identity for p in admission.policy.programs.values() if p.active} == {"b", "c"}
        else:
            assert row["resources"] is None
            assert row["decision"]["reason"] == "native_resources_unavailable_reference_fallback"
            assert admission.policy.programs["a"].active
        assert runtime._native_cache.token_to_kv_pool_allocator.size_full is capacity
    finally:
        runtime.close()


def audit_row():
    candidates = rows()
    resources = resource()
    return {
        "event": "workflow_combination_selection", "source": "periodic_decay_resume",
        "monotonic_s": 10., "physical_capacity_credit": False, "cost_identity": "planned",
        "decision": select(), "candidates": [asdict(row) for row in candidates],
        "resources": asdict(resources), "device_fit_scope": "planned_pool_geometry",
        "keys": {
            row.identity: {**key(row.identity), "root_workflow_id": row.workflow}
            for row in candidates
        },
        "metadata_and_selection_cpu_ns": 0, "metadata_and_selection_wall_ns": 0,
        "resource_fallback_selection_enabled": True,
    }


def test_read_only_audit_requires_the_exact_planning_scope_from_its_profile():
    row = json.loads(json.dumps(audit_row()))
    result = replay_choice(
        row, costs(), "planned", allow_feasible_resource_alternatives=True,
        plan_device_capacity=True,
    )
    assert result["search_replayed"] and result["selected"] == ["short"]
    with pytest.raises(ValueError, match="device fit scope"):
        replay_choice(row, costs(), "planned", allow_feasible_resource_alternatives=True)
    changed = deepcopy(row)
    changed["resources"]["device_capacity_tokens"] = None
    with pytest.raises(ValueError, match="device fit scope"):
        replay_choice(
            changed, costs(), "planned", allow_feasible_resource_alternatives=True,
            plan_device_capacity=True,
        )
