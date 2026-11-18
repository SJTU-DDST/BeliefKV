from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import random
from types import SimpleNamespace as NS

import pytest

from beliefkv.policy.workflow_combination import _evaluate
from beliefkv.policy.workflow_maximal_admission import choose_maximal_admission
from beliefkv.runtime.workflow_combination import WorkflowCombinationController
from scripts.audit_workflow_combination_choices import replay_choice, validate_selection_policy
from tests.test_combination_choice_audit import evidence
from tests.test_workflow_shortest_frontier import fixture


def choose(rows, resources, costs, **kwargs):
    return choose_maximal_admission(
        rows, ("long-a", "long-b"), resources, costs, clock=lambda: 0, **kwargs,
    )


def brute_force(rows, reference, resources, costs, required=()):
    options = {}
    for mask in range(1, 1 << len(rows)):
        members = [row for index, row in enumerate(rows) if mask & (1 << index)]
        if set(required) <= {row.identity for row in members}:
            value = _evaluate(members, resources, costs)
            if value is not None:
                options[mask] = value
    maximal = [
        mask for mask in options if not any(
            mask != other and mask & other == mask for other in options
        )
    ]
    if not maximal:
        return tuple(reference)
    reference_mask = sum(1 << index for index, row in enumerate(rows) if row.identity in reference)
    best = reference_mask if reference_mask in maximal else maximal[0]
    for mask in maximal:
        if options[mask]["cost_normalized_score"] > (
            options[best]["cost_normalized_score"] * (1. + costs.relative_tie)
        ):
            best = mask
    return tuple(row.identity for index, row in enumerate(rows) if best & (1 << index))


def test_all_fitting_candidates_are_selected_even_when_a_peer_has_zero_weight():
    rows, resources, costs = fixture()
    rows[0] = replace(rows[0], weight=0.)
    chosen = choose(rows, replace(resources, logical_tokens=400), costs)
    assert set(chosen["selected"]) == {row.identity for row in rows}
    assert chosen["reason"] == "all_candidates_fit_maximal"


def test_competition_can_avoid_a_short_workflow_cold_prefill_without_leaving_capacity_unfilled():
    rows, resources, costs = fixture()
    rows[0] = replace(rows[0], input_tokens=200)
    assert _evaluate([rows[0]], resources, costs) is not None
    before = deepcopy((rows, resources, costs))
    chosen = choose(rows, resources, costs)
    assert chosen["selected"] == ("long-a", "long-b")
    assert chosen["search_complete"]
    assert (rows, resources, costs) == before


def test_required_join_rights_keep_their_complete_logical_and_physical_cost():
    rows, resources, costs = fixture()
    chosen = choose(rows, resources, costs, required=("long-a",))
    assert chosen["selected"] == ("long-a", "long-b")
    assert chosen["required_reference_members"] == ("long-a",)


@pytest.mark.parametrize("seed", range(24))
def test_packing_matches_independent_exhaustive_feasible_superset_search(seed):
    rng = random.Random(seed)
    base, resources, costs = fixture()
    rows = [
        replace(
            base[0], identity=f"p{i}", workflow=f"w{i}", group=f"g{i}",
            logical_tokens=rng.randint(10, 150), context_tokens=200,
            input_tokens=rng.randint(0, 100), weight=rng.random(),
            missing_extents=((("shared",), 30),) if i % 2 else (((i,), 20),),
        )
        for i in range(2 + seed % 7)
    ]
    resources = replace(
        resources, logical_tokens=200, device_tokens=20, cold_device_tokens=10,
        device_capacity_tokens=150 if seed % 3 else None,
        input_tokens=100, slots=3, growth_tokens=3,
        chunked_input=bool(seed % 2), input_page_tokens=8,
    )
    reference = (rows[-1].identity,)
    required = reference if seed % 4 == 0 else ()
    chosen = choose_maximal_admission(
        rows, reference, resources, costs, required=required, clock=lambda: 0,
    )
    assert chosen["selected"] == brute_force(rows, reference, resources, costs, required)
    assert chosen["search_complete"]


def test_resource_pruning_keeps_required_members_that_arrive_in_later_bits():
    base, resources, costs = fixture()
    rows = [
        replace(
            base[0], identity=f"p{i}", workflow=f"w{i}", group=f"g{i}",
            logical_tokens=20, context_tokens=200, input_tokens=16,
            missing_extents=((("shared",), 20),), weight=1. / (i + 1),
        )
        for i in range(8)
    ]
    resources = replace(
        resources, logical_tokens=200, input_tokens=16, slots=4,
        device_tokens=100, cold_device_tokens=0, device_capacity_tokens=None,
        growth_tokens=0, chunked_input=True, input_page_tokens=8,
    )
    reference, required = ("p7",), ("p7",)
    chosen = choose_maximal_admission(
        rows, reference, resources, costs, required=required, clock=lambda: 0,
    )
    assert chosen["selected"] == brute_force(rows, reference, resources, costs, required)
    assert set(chosen["selected"]) == {"p0", "p7"}
    assert chosen["search_complete"]


def test_pruning_checks_only_the_extended_join_without_losing_shared_extents():
    base, resources, costs = fixture()
    rows = [
        replace(
            base[0], identity=f"p{i}", workflow=f"w{i // 3}", group=f"g{i // 3}",
            logical_tokens=50, context_tokens=200, input_tokens=0,
            weight=.8 if i < 3 else .4,
            unfinished_members=3, active_ready_members=1,
            missing_extents=(((i // 3,), 80),),
        )
        for i in range(6)
    ]
    resources = replace(
        resources, logical_tokens=200, input_tokens=16, slots=4,
        device_tokens=100, cold_device_tokens=0, device_capacity_tokens=None,
        growth_tokens=0,
    )
    reference, required = ("p5",), ("p5",)
    chosen = choose_maximal_admission(
        rows, reference, resources, costs, required=required, clock=lambda: 0,
    )
    selected = set(chosen["selected"])
    assert selected == {"p3", "p5"}
    assert chosen["search_complete"]
    assert chosen["selected_evaluation"]["missing_unique_tokens"] == 80
    assert chosen["selected_evaluation"]["benefit"] == pytest.approx(.4 * (9 - 1) / 9)


@pytest.mark.parametrize("deadline", (1, 2, 3, 4, 5))
def test_deadline_falls_back_to_reference_and_is_exactly_replayable(deadline):
    row, _ = evidence()
    rows, resources, costs = fixture()
    ticks = -1

    def clock():
        nonlocal ticks
        ticks += 1
        return 500_000 if ticks >= deadline else 0

    chosen = choose_maximal_admission(rows, ("long-a", "long-b"), resources, costs, clock=clock)
    row.update(
        selection_policy="maximal_cost_aware", candidates=[asdict(item) for item in rows],
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


@pytest.mark.parametrize("flag", (False, True, 1, "yes", None))
def test_runtime_requires_explicit_maximal_flag_with_short_work_profile(tmp_path, flag):
    _, _, costs = fixture()
    raw = {
        "shortest_work_first_admission": True, "cost_aware_maximal_admission": flag,
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
    if type(flag) is bool:
        assert WorkflowCombinationController(admission).cost_aware_maximal is flag
    else:
        with pytest.raises(ValueError, match="maximal cost"):
            WorkflowCombinationController(admission)


def test_policy_audit_rejects_undeclared_cost_selection():
    row = {"event": "workflow_combination_selection", "selection_policy": "maximal_cost_aware"}
    validate_selection_policy([row], shortest_work_first=True, cost_aware_maximal=True)
    with pytest.raises(ValueError):
        validate_selection_policy([row], shortest_work_first=True)


def test_matched_preparation_changes_neither_forecasts_nor_oracle_work(tmp_path):
    from pathlib import Path
    from beliefkv.experiments.source_provenance import sha256_file
    from beliefkv.predictor.workflow_continuous_work import forecast_fingerprint
    from scripts.audit_workflow_combination_choices import verify_model_work_pair
    from scripts.prepare_maximal_cost_admission import configuration, prepare_artifacts
    from scripts.prepare_shortest_work_admission import (
        configuration as short_configuration, prepare_artifacts as short_artifacts,
    )
    from tests.test_model_work_choice_audit import model_work_pair

    full, oracle_config, learned_config = model_work_pair(tmp_path)
    old_model = json.loads(Path(learned_config["common_environment"]["WORKFLOW_WORK_MODEL"]).read_bytes())
    old_truth = json.loads(Path(oracle_config["common_environment"]["WORKFLOW_WORK_MODEL"]).read_bytes())
    model, truth = short_artifacts(old_model, old_truth)
    initial_model, initial_truth = tmp_path / "short-model.json", tmp_path / "short-truth.json"
    initial_model.write_text(json.dumps(model))
    truth["combination_reference_source_model_sha256"] = sha256_file(initial_model)
    initial_truth.write_text(json.dumps(truth))
    learned_config = short_configuration(learned_config, initial_model, sha256_file(initial_model))
    oracle_config = short_configuration(oracle_config, initial_truth, sha256_file(initial_truth))
    model, truth = prepare_artifacts(model, truth)
    assert forecast_fingerprint(model) == forecast_fingerprint(old_model)
    assert truth["requests"] == old_truth["requests"]
    assert truth["workflow_graphs"] == old_truth["workflow_graphs"]
    model_path, truth_path = tmp_path / "maximal-model.json", tmp_path / "maximal-truth.json"
    model_path.write_text(json.dumps(model))
    truth["combination_reference_source_model_sha256"] = sha256_file(model_path)
    truth_path.write_text(json.dumps(truth))
    learned_config = configuration(learned_config, model_path, sha256_file(model_path))
    oracle_config = configuration(oracle_config, truth_path, sha256_file(truth_path))
    verify_model_work_pair(full, oracle_config, learned_config)
    truth["cost_aware_maximal_admission"] = False
    truth_path.write_text(json.dumps(truth))
    for prefix in ("", "BELIEFKV_"):
        oracle_config["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = sha256_file(truth_path)
    with pytest.raises(ValueError, match="declared work provider"):
        verify_model_work_pair(full, oracle_config, learned_config)


def test_packed_selection_changes_actual_eligibility_and_prefill_candidates(monkeypatch, tmp_path):
    from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
    from tests.test_workflow_combination_runtime import runtime_with_queue

    runtime, admission, _ = runtime_with_queue(monkeypatch, tmp_path)
    records = []
    runtime._opportunity_writer = NS(record=records.append)
    controller = admission._combination
    controller.shortest_work_first = controller.cost_aware_maximal = True
    controller.costs = replace(controller.costs, decode_cost_basis="full_batch", selection_objective="benefit")
    try:
        admission.tick()
        choice = next(row for row in records if row["event"] == "workflow_combination_selection")
        assert choice["selection_policy"] == "maximal_cost_aware"
        assert not choice["physical_capacity_credit"]
        active = {item.identity for item in admission.policy.programs.values() if item.active}
        assert active == set(choice["decision"]["selected"]) == {"b", "c"}
        requests = [admission._live_requests[name + "-request"] for name in ("a", "b", "c")]
        plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        actual = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert set(req.rid for req in actual.candidates) == {"b-request", "c-request"}
    finally:
        runtime._opportunity_writer = None
        runtime.close()
