from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.policy.workflow_combination import (
    CombinationResources as Resources, _evaluate, choose_combination,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.audit_workflow_combination_choices import replay_choice
from scripts.prepare_calibrated_combination_oracle import calibrated_artifact
from tests.test_calibrated_combination_preparation import calibration, original
from tests.test_workflow_combination import candidate, cost
from tests.test_workflow_combination_runtime import configured
from tests.test_workflow_size_selection_audit import key


def costs():
    return replace(cost(), decode_cost_basis="full_batch")


def test_existing_decode_base_prevents_an_artificially_tiny_incremental_denominator():
    rows = [candidate("a", 100, .5), candidate("b", 100, .4), candidate("c", 1000, 1.)]
    resources = Resources(200, 10000, 2048, 4, active_width=4, active_context_tokens=40000)
    old = choose_combination(rows, ("a", "b"), resources, cost(), clock=lambda: 0)
    new = choose_combination(rows, ("a", "b"), resources, costs(), clock=lambda: 0)
    assert old["selected"] == ("a",)
    assert new["selected"] == ("a", "b")
    evaluation = new["selected_evaluation"]
    assert evaluation["objective_cost_ms"] == pytest.approx(
        evaluation["incremental_cost_ms"] + costs().decode_cost(4, 40000),
    )


def test_zero_existing_compute_preserves_selection_and_does_not_force_batch_cardinality():
    rows = [candidate("a", 100, 1.), candidate("b", 100, 0.), candidate("c", 1000, .5)]
    resources = Resources(200, 10000, 2048, 4)
    old = choose_combination(rows, ("a", "b"), resources, cost(), clock=lambda: 0)
    new = choose_combination(rows, ("a", "b"), resources, costs(), clock=lambda: 0)
    assert old["selected"] == new["selected"] == ("a",)
    assert old["selected_evaluation"]["score"] == new["selected_evaluation"]["score"]


def test_active_work_is_not_charged_as_extra_migration_or_capacity():
    rows = [
        replace(candidate(name, 100, .5), missing_extents=((("shared", 1.), 500),))
        for name in ("a", "b")
    ]
    resources = Resources(200, 520, 2048, 4, active_width=4, active_context_tokens=40000)
    value = _evaluate(rows, resources, costs())
    assert value["missing_unique_tokens"] == 500
    assert value["objective_cost_ms"] > value["incremental_cost_ms"]
    assert _evaluate(rows, replace(resources, device_tokens=500), costs()) is None


def test_all_join_rights_and_unsupported_resource_fallback_still_hold():
    rows = [
        candidate("parent", 100, .2),
        replace(candidate("large", 300, .9), missing_extents=((("host", 1.), 1000),)),
        candidate("b", 150, .5), candidate("c", 150, .5),
    ]
    result = choose_combination(
        rows, ("parent", "large"), Resources(500, 250, 2048, 4, active_width=2),
        costs(), required=("parent",), clock=lambda: 0,
        allow_feasible_resource_alternatives=True,
    )
    assert "parent" in result["selected"] and "large" not in result["selected"]
    assert result["reference_evaluation"] is None
    assert result["reason"] == "feasible_resource_alternative"


def test_new_profile_changes_objective_only_and_binds_it_to_a_distinct_identity():
    kwargs = dict(
        calibration_path="/costs", calibration_sha256="a" * 64,
        include_unreferenced_capacity=True, allow_deep_prefix_aggregate=True,
        allow_feasible_resource_alternatives=True,
    )
    before = calibrated_artifact(original(), calibration(), **kwargs)
    after = calibrated_artifact(original(), calibration(), full_batch_cost=True, **kwargs)
    assert after["admission_cost_profile"]["kind"] == "r5_frozen_batch_cost_proxy_capacity_v5"
    assert after["admission_cost_profile"]["identity"] != before["admission_cost_profile"]["identity"]
    expected_costs = {**before["admission_cost_profile"]["costs"], "decode_cost_basis": "full_batch"}
    assert after["admission_cost_profile"]["costs"] == expected_costs
    normalized = deepcopy(after)
    normalized["admission_cost_profile"] = before["admission_cost_profile"]
    normalized["calibrated_model_time"] = before["calibrated_model_time"]
    assert normalized == before


@pytest.mark.parametrize("invalid", (None, True, "other"))
def test_unknown_cost_basis_is_rejected(invalid):
    with pytest.raises(ValueError, match="cost profile"):
        replace(cost(), decode_cost_basis=invalid)


def test_new_objective_requires_the_complete_capacity_contract():
    with pytest.raises(ValueError, match="v4 capacity"):
        calibrated_artifact(
            original(), calibration(), calibration_path="/costs",
            calibration_sha256="a" * 64, full_batch_cost=True,
        )


@pytest.mark.parametrize("kind,basis,valid", (
    ("r5_frozen_batch_cost_proxy_capacity_v4", "incremental", True),
    ("r5_frozen_batch_cost_proxy_capacity_v4", "full_batch", False),
    ("r5_frozen_batch_cost_proxy_capacity_v5", "incremental", False),
    ("r5_frozen_batch_cost_proxy_capacity_v5", "full_batch", True),
))
def test_actual_constructor_requires_matching_explicit_kind_and_cost_basis(
    monkeypatch, tmp_path, kind, basis, valid,
):
    config = configured(tmp_path)
    path = Path(config["WORKFLOW_WORK_MODEL"])
    raw = json.loads(path.read_bytes())
    raw["admission_cost_profile"]["kind"] = kind
    raw["admission_cost_profile"]["costs"]["decode_cost_basis"] = basis
    path.write_text(json.dumps(raw))
    config["WORKFLOW_WORK_MODEL_SHA256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name, value in arm_environment(config, "full").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    if not valid:
        with pytest.raises(ValueError, match="full-batch cost"):
            NativeAdmissionRuntime(event_socket_path=str(tmp_path / "full-batch.sock"))
        return
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "full-batch.sock"))
    try:
        controller = runtime._agent_admission._combination
        assert controller.costs.decode_cost_basis == basis
        assert controller.allow_feasible_resource_alternatives
        assert controller.allow_deep_prefix_aggregate
        assert controller.include_unreferenced_capacity
    finally:
        runtime.close()


@pytest.mark.parametrize("stop_check", (None, 1, 2))
def test_read_only_audit_reconstructs_full_batch_decisions_and_actual_cutoffs(stop_check):
    rows = [candidate(str(index), 100, .2 + index / 20.) for index in range(8)]
    resources = Resources(400, 10000, 2048, 4, active_width=4, active_context_tokens=40000)
    calls = -1

    def clock():
        nonlocal calls
        calls += 1
        return 500_000 if stop_check is not None and calls >= stop_check else 0

    decision = choose_combination(
        rows, ("0", "1"), resources, costs(), required=("0",), clock=clock,
        allow_feasible_resource_alternatives=True,
    )
    row = {
        "event": "workflow_combination_selection", "source": "periodic_decay_resume",
        "monotonic_s": 10., "physical_capacity_credit": False, "cost_identity": "frozen-full",
        "decision": decision, "candidates": [asdict(value) for value in rows],
        "resources": asdict(resources),
        "keys": {
            value.identity: {**key(value.identity), "root_workflow_id": value.workflow}
            for value in rows
        },
        "metadata_and_selection_cpu_ns": 0, "metadata_and_selection_wall_ns": 0,
        "resource_fallback_selection_enabled": True,
    }
    replayed = replay_choice(
        json.loads(json.dumps(row)), costs(), "frozen-full",
        allow_feasible_resource_alternatives=True,
    )
    assert replayed["selected"] == list(decision["selected"])
    assert "0" in replayed["selected"]
