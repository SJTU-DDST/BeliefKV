from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from beliefkv.policy.workflow_combination import _evaluate
from beliefkv.policy.workflow_maximal_admission import choose_maximal_admission
from beliefkv.runtime.workflow_combination import WorkflowCombinationController
from scripts.audit_workflow_combination_choices import replay_choice, validate_selection_policy
from tests.test_combination_choice_audit import evidence
from tests.test_workflow_shortest_frontier import fixture


def test_benefit_can_select_expensive_short_work_without_leaving_appendable_capacity():
    rows, resources, costs = fixture()
    rows[0] = replace(rows[0], weight=.9, input_tokens=200)
    costs = replace(costs, prefill_ms_per_token=1.)
    reference = ("long-a", "long-b")
    before = deepcopy((rows, resources, costs))
    ratio = choose_maximal_admission(rows, reference, resources, costs, clock=lambda: 0)
    benefit = choose_maximal_admission(
        rows, reference, resources, costs, clock=lambda: 0, selection_objective="benefit",
    )
    assert ratio["selected"] == reference
    assert benefit["selected"] == ("short",)
    assert benefit["selection_objective"] == "maximal_benefit"
    assert benefit["reason"] == "maximal_benefit_set_changed"
    assert benefit["selected_evaluation"]["benefit"] > ratio["selected_evaluation"]["benefit"]
    for peer in rows[1:]:
        assert _evaluate([rows[0], peer], resources, costs) is None
    assert (rows, resources, costs) == before


def test_benefit_keeps_required_join_rights_and_their_resource_cost():
    rows, resources, costs = fixture()
    rows[0] = replace(rows[0], weight=.9, input_tokens=200)
    chosen = choose_maximal_admission(
        rows, ("long-a", "long-b"), resources, costs, required=("long-a",),
        clock=lambda: 0, selection_objective="benefit",
    )
    assert chosen["selected"] == ("long-a", "long-b")
    assert chosen["required_reference_members"] == ("long-a",)
    assert chosen["search_complete"]


def test_benefit_still_takes_every_jointly_fitting_zero_weight_peer():
    rows, resources, costs = fixture()
    rows[0] = replace(rows[0], weight=0.)
    chosen = choose_maximal_admission(
        rows, ("long-a", "long-b"), replace(resources, logical_tokens=400), costs,
        clock=lambda: 0, selection_objective="benefit",
    )
    assert set(chosen["selected"]) == {row.identity for row in rows}
    assert chosen["reason"] == "all_candidates_fit_maximal"


@pytest.mark.parametrize("deadline", (1, 2, 3, 4, 5))
def test_benefit_deadline_preserves_reference_and_replays_exactly(deadline):
    row, _ = evidence()
    rows, resources, costs = fixture()
    ticks = -1

    def clock():
        nonlocal ticks
        ticks += 1
        return 500_000 if ticks >= deadline else 0

    chosen = choose_maximal_admission(
        rows, ("long-a", "long-b"), resources, costs, clock=clock,
        selection_objective="benefit",
    )
    row.update(
        selection_policy="maximal_benefit", candidates=[asdict(item) for item in rows],
        resources=asdict(resources), decision=chosen, resource_fallback_selection_enabled=True,
        device_fit_scope="planned_pool_geometry",
    )
    template = row["keys"]["a"]
    row["keys"] = {
        item.identity: {**template, "invocation_id": item.identity, "root_workflow_id": item.workflow}
        for item in rows
    }
    result = replay_choice(
        json.loads(json.dumps(row)), costs, "frozen",
        allow_feasible_resource_alternatives=True, plan_device_capacity=True,
    )
    assert result["search_replayed"]
    if chosen["reason"].startswith("soft_deadline"):
        assert result["selected"] == ["long-a", "long-b"]


def controller(tmp_path, objective, maximal=True):
    _, _, costs = fixture()
    raw = {
        "shortest_work_first_admission": True,
        "cost_aware_maximal_admission": maximal,
        "maximal_admission_objective": objective,
        "admission_cost_profile": {
            "kind": "r5_frozen_batch_cost_proxy_capacity_v7",
            "identity": "fixture", "costs": asdict(costs),
        },
    }
    path = tmp_path / "model.json"
    path.write_text(json.dumps(raw))
    admission = NS(runtime=NS(
        workflow_work_model=path, workflow_work_model_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    ))
    return WorkflowCombinationController(admission)


@pytest.mark.parametrize("objective", ("benefit", "benefit_per_cost"))
def test_runtime_reads_explicit_objective(tmp_path, objective):
    assert controller(tmp_path, objective).maximal_objective == objective


@pytest.mark.parametrize("objective", ("ratio", "", 1, None, []))
def test_runtime_rejects_unsupported_objective(tmp_path, objective):
    with pytest.raises(ValueError, match="maximal admission objective"):
        controller(tmp_path, objective)


def test_runtime_rejects_benefit_without_the_maximal_selector(tmp_path):
    with pytest.raises(ValueError, match="explicit maximal selector"):
        controller(tmp_path, "benefit", maximal=False)


def test_audit_requires_the_matching_objective_declaration():
    row = {"event": "workflow_combination_selection", "selection_policy": "maximal_benefit"}
    validate_selection_policy(
        [row], shortest_work_first=True, cost_aware_maximal=True, maximal_objective="benefit",
    )
    with pytest.raises(ValueError, match="differs"):
        validate_selection_policy([row], shortest_work_first=True, cost_aware_maximal=True)
    with pytest.raises(ValueError, match="explicitly declared"):
        validate_selection_policy([row], shortest_work_first=True, maximal_objective="benefit")


def test_preparation_preserves_forecasts_work_and_matched_objectives(tmp_path):
    from beliefkv.experiments.source_provenance import sha256_file
    from beliefkv.predictor.workflow_continuous_work import forecast_fingerprint
    from scripts.audit_workflow_combination_choices import verify_model_work_pair
    from scripts.prepare_maximal_cost_admission import configuration, prepare_artifacts
    from scripts.prepare_shortest_work_admission import prepare_artifacts as short_artifacts
    from tests.test_model_work_choice_audit import model_work_pair

    full, oracle_config, learned_config = model_work_pair(tmp_path)
    old_model = json.loads(Path(learned_config["common_environment"]["WORKFLOW_WORK_MODEL"]).read_bytes())
    old_truth = json.loads(Path(oracle_config["common_environment"]["WORKFLOW_WORK_MODEL"]).read_bytes())
    model, truth = prepare_artifacts(*short_artifacts(old_model, old_truth))
    model, truth = prepare_artifacts(model, truth, selection_objective="benefit")
    assert forecast_fingerprint(model) == forecast_fingerprint(old_model)
    assert truth["requests"] == old_truth["requests"]
    assert truth["workflow_graphs"] == old_truth["workflow_graphs"]
    model_path, truth_path = tmp_path / "benefit-model.json", tmp_path / "benefit-truth.json"
    model_path.write_text(json.dumps(model))
    truth["combination_reference_source_model_sha256"] = sha256_file(model_path)
    truth_path.write_text(json.dumps(truth))
    learned_config = configuration(
        learned_config, model_path, sha256_file(model_path), maximal_objective="benefit",
    )
    oracle_config = configuration(
        oracle_config, truth_path, sha256_file(truth_path), maximal_objective="benefit",
    )
    verify_model_work_pair(full, oracle_config, learned_config)
    truth["maximal_admission_objective"] = "benefit_per_cost"
    truth_path.write_text(json.dumps(truth))
    for prefix in ("", "BELIEFKV_"):
        oracle_config["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = sha256_file(truth_path)
    with pytest.raises(ValueError, match="declared work provider"):
        verify_model_work_pair(full, oracle_config, learned_config)


def test_benefit_mode_reaches_eligibility_journal_and_native_prefill(monkeypatch, tmp_path):
    from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
    from tests.test_workflow_combination_runtime import runtime_with_queue

    runtime, admission, _ = runtime_with_queue(monkeypatch, tmp_path)
    records = []
    runtime._opportunity_writer = NS(record=records.append)
    control = admission._combination
    control.shortest_work_first = control.cost_aware_maximal = True
    control.maximal_objective = "benefit"
    control.costs = replace(control.costs, decode_cost_basis="full_batch", selection_objective="benefit")
    try:
        admission.tick()
        choice = next(row for row in records if row["event"] == "workflow_combination_selection")
        assert choice["selection_policy"] == "maximal_benefit"
        assert choice["decision"]["selection_objective"] == "maximal_benefit"
        assert not choice["physical_capacity_credit"]
        selected = set(choice["decision"]["selected"])
        active = {item.identity for item in admission.policy.programs.values() if item.active}
        assert active == selected and active
        requests = [admission._live_requests[name + "-request"] for name in ("a", "b", "c")]
        plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        actual = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert set(req.rid for req in actual.candidates) == {owner + "-request" for owner in selected}
    finally:
        runtime._opportunity_writer = None
        runtime.close()
