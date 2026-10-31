from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.policy.workflow_combination import (
    CombinationResources as Resources, choose_combination,
)
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.audit_workflow_combination_choices import replay_choice
from scripts.prepare_calibrated_combination_oracle import calibrated_artifact
from tests.test_calibrated_combination_preparation import calibration, original
from tests.test_workflow_combination import candidate, cost
from tests.test_workflow_combination_runtime import configured, runtime_with_queue


def choices():
    return [
        replace(candidate("a", 300, .2), missing_extents=((("old", 1.), 1000),)),
        candidate("b", 150, .9), candidate("c", 150, .9),
    ]


def select(*, rows=None, required=(), clock=lambda: 0, **kwargs):
    return choose_combination(
        choices() if rows is None else rows, ("a",), Resources(500, 250, 2048, 4),
        cost(), required=required, clock=clock, **kwargs,
    )


def test_explicit_resource_alternative_uses_feasible_agents_without_inventing_reference_score():
    old = select()
    assert old["selected"] == ("a",)
    assert old["reason"] == "reference_resource_summary_unsupported"
    result = select(allow_feasible_resource_alternatives=True)
    assert result["selected"] == ("b", "c")
    assert result["reference_evaluation"] is None
    assert result["reason"] == "feasible_resource_alternative"
    assert result["search_complete"]
    assert result["selected_evaluation"]["missing_unique_tokens"] == 0


@pytest.mark.parametrize("required", ((), ("a",)))
def test_unfittable_required_join_or_all_candidates_preserves_original_authority(required):
    rows = choices()
    if not required:
        rows = [replace(row, missing_extents=(((row.identity, 1.), 1000),)) for row in rows]
    result = select(rows=rows, required=required, allow_feasible_resource_alternatives=True)
    assert result["selected"] == ("a",)
    assert result["reason"] == "resource_fallback_no_feasible_subset"
    assert result["selected_evaluation"] is None


def test_feasible_join_parent_is_retained_while_an_unfittable_peer_is_replaced():
    rows = [candidate("parent", 100, .2), *choices()]
    result = choose_combination(
        rows, ("parent", "a"), Resources(500, 250, 2048, 4), cost(),
        required=("parent",), clock=lambda: 0, allow_feasible_resource_alternatives=True,
    )
    assert set(result["selected"]) == {"parent", "b", "c"}
    assert result["required_reference_members"] == ("parent",)
    assert result["reference_evaluation"] is None


@pytest.mark.parametrize("stop_check,expected", ((1, ("a",)), (2, ("b", "c"))))
def test_deadline_keeps_reference_until_a_feasible_alternative_has_actually_been_evaluated(
    stop_check, expected,
):
    rows = choices() + [candidate(str(index), 500, 0.) for index in range(5)]
    calls = -1

    def clock():
        nonlocal calls
        calls += 1
        return 500_000 if calls >= stop_check else 0

    result = select(rows=rows, clock=clock, allow_feasible_resource_alternatives=True)
    assert result["selected"] == expected
    assert not result["search_complete"]
    assert result["reason"] == (
        "soft_deadline_resource_fallback" if stop_check == 1
        else "soft_deadline_feasible_resource_alternative"
    )


def test_feasible_reference_and_legacy_default_keep_their_exact_decisions():
    rows = [replace(choices()[0], missing_extents=()), *choices()[1:]]
    before = select(rows=rows)
    after = select(rows=rows, allow_feasible_resource_alternatives=True)
    assert before == after


def test_new_profile_is_fingerprinted_without_changing_work_costs_or_paths():
    kwargs = dict(
        calibration_path="/costs", calibration_sha256="a" * 64,
        include_unreferenced_capacity=True, allow_deep_prefix_aggregate=True,
    )
    before = calibrated_artifact(original(), calibration(), **kwargs)
    after = calibrated_artifact(
        original(), calibration(), allow_feasible_resource_alternatives=True, **kwargs,
    )
    assert after["admission_cost_profile"]["kind"] == "r5_frozen_batch_cost_proxy_capacity_v4"
    assert after["admission_cost_profile"]["identity"] != before["admission_cost_profile"]["identity"]
    normalized = deepcopy(after)
    normalized["admission_cost_profile"] = before["admission_cost_profile"]
    normalized["calibrated_model_time"] = before["calibrated_model_time"]
    assert normalized == before
    assert after["admission_cost_profile"]["costs"] == before["admission_cost_profile"]["costs"]


@pytest.mark.parametrize("kind,enabled", (
    ("r5_frozen_batch_cost_proxy", False),
    ("r5_frozen_batch_cost_proxy_capacity_v2", False),
    ("r5_frozen_batch_cost_proxy_capacity_v3", False),
    ("r5_frozen_batch_cost_proxy_capacity_v4", True),
))
def test_actual_constructor_gates_resource_alternatives_by_explicit_profile(
    monkeypatch, tmp_path, kind, enabled,
):
    config = configured(tmp_path)
    path = Path(config["WORKFLOW_WORK_MODEL"])
    raw = json.loads(path.read_bytes())
    raw["admission_cost_profile"]["kind"] = kind
    path.write_text(json.dumps(raw))
    config["WORKFLOW_WORK_MODEL_SHA256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name, value in arm_environment(config, "full").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "resources.sock"))
    try:
        controller = runtime._agent_admission._combination
        assert controller.allow_feasible_resource_alternatives is enabled
        assert controller.allow_deep_prefix_aggregate is kind.endswith(("_v3", "_v4"))
    finally:
        runtime.close()


def test_resource_replacement_reaches_actual_eligibility_and_native_service_candidates(
    monkeypatch, tmp_path,
):
    from beliefkv.runtime import workflow_combination as combination
    from tests import test_workflow_combination_runtime as fixtures

    original_request = fixtures.request

    def request(*args, **kwargs):
        req = original_request(*args, **kwargs)
        req.session_id, req.session_generation = "session-" + req.rid, 0
        return req

    monkeypatch.setattr(fixtures, "request", request)

    runtime, admission, _env = runtime_with_queue(monkeypatch, tmp_path)
    controller = admission._combination
    controller.allow_feasible_resource_alternatives = True
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    monkeypatch.setattr(combination, "observe_static_full_mamba_headroom", lambda _cache: NS(
        observable=True, device_full_free_tokens=250, device_mamba_free_slots=0,
    ))
    monkeypatch.setattr(controller, "_prefix", lambda key, _item: (
        (((("old", 1.), 1000),) if key.invocation_id == "a" else ()), 1,
    ))
    try:
        admission.tick()
        assert {item.identity for item in admission.policy.programs.values() if item.active} == {"b", "c"}
        row = next(row for row in records if row["event"] == "workflow_combination_selection")
        assert row["decision"]["reason"] == "feasible_resource_alternative"
        assert row["resource_fallback_selection_enabled"] is True
        assert row["physical_capacity_credit"] is False
        requests = [admission._live_requests[name + "-request"] for name in ("a", "b", "c")]
        plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        actual = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert actual.candidates == tuple(requests[1:])
        replayed = replay_choice(
            json.loads(json.dumps(row)), controller.costs, controller.cost_identity,
            allow_feasible_resource_alternatives=True,
        )
        assert replayed["search_replayed"] and replayed["selected_set_changed"]
        with pytest.raises(ValueError, match="resource fallback"):
            replay_choice(json.loads(json.dumps(row)), controller.costs, controller.cost_identity)
    finally:
        runtime.close()


@pytest.mark.parametrize("stop_check", (None, 1, 2))
def test_read_only_audit_replays_new_deadlines_with_the_explicit_selection_contract(stop_check):
    rows = choices() + [candidate(str(index), 500, 0.) for index in range(5)]
    calls = -1

    def clock():
        nonlocal calls
        calls += 1
        return 500_000 if stop_check is not None and calls >= stop_check else 0

    decision = select(rows=rows, clock=clock, allow_feasible_resource_alternatives=True)
    from tests.test_workflow_size_selection_audit import key

    row = {
        "event": "workflow_combination_selection", "source": "periodic_decay_resume",
        "monotonic_s": 10., "physical_capacity_credit": False,
        "cost_identity": "frozen", "decision": decision,
        "candidates": [asdict(value) for value in rows],
        "resources": asdict(Resources(500, 250, 2048, 4)),
        "keys": {
            value.identity: {**key(value.identity), "root_workflow_id": value.workflow}
            for value in rows
        },
        "metadata_and_selection_cpu_ns": 0, "metadata_and_selection_wall_ns": 0,
        "resource_fallback_selection_enabled": True,
    }
    replayed = replay_choice(
        json.loads(json.dumps(row)), cost(), "frozen",
        allow_feasible_resource_alternatives=True,
    )
    assert replayed["selected"] == list(decision["selected"])
