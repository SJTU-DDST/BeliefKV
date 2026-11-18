from collections import Counter
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from beliefkv.policy.workflow_combination import _evaluate
from beliefkv.policy.workflow_shortest_frontier import choose_shortest_work_first
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey, select_native_prefill_candidates
from beliefkv.runtime.workflow_combination import WorkflowCombinationController
from beliefkv.runtime.workflow_size_admission import WorkflowSizeAdmission
from scripts.audit_workflow_combination_choices import replay_choice, validate_selection_policy
from scripts.prepare_shortest_work_admission import configuration, prepare_artifacts
from tests.test_combination_choice_audit import evidence
from tests.test_workflow_combination_runtime import runtime_with_queue
from tests.test_workflow_shortest_frontier import fixture
from tests.test_workflow_size_selection_audit import key


def select(rows, resources, costs, reference=("long-a", "long-b"), **kwargs):
    return choose_shortest_work_first(
        rows, reference, resources, costs, clock=lambda: 0, **kwargs,
    )


def test_short_large_context_is_not_outvoted_by_two_long_workflows():
    rows, resources, costs = fixture()
    before = deepcopy((rows, resources, costs))
    chosen = select(rows, resources, costs)
    assert chosen["selected"] == ("short",)
    assert chosen["search_complete"] and chosen["evaluated_candidates"] == 3
    assert _evaluate([rows[0]], resources, costs) is not None
    assert (rows, resources, costs) == before


def test_unused_capacity_is_filled_with_multiple_workflows():
    rows, resources, costs = fixture()
    chosen = select(rows, replace(resources, logical_tokens=400), costs)
    assert chosen["selected"] == ("short", "long-b", "long-a")


def test_required_join_precedes_ordinary_work_and_keeps_its_full_budget():
    rows, resources, costs = fixture()
    chosen = select(rows, resources, costs, required=("long-a",))
    assert chosen["selected"] == ("long-a", "long-b")
    assert chosen["required_reference_members"] == ("long-a",)


def test_ready_siblings_stay_adjacent_without_waiting_for_an_unready_member():
    rows, resources, costs = fixture()
    first = replace(
        rows[0], identity="s1", workflow="short", group="join", logical_tokens=80,
        unfinished_members=3,
    )
    second = replace(first, identity="s2")
    chosen = select([first, second, *rows[1:]], resources, costs)
    assert chosen["selected"][:2] == ("s1", "s2")
    assert chosen["selected"] == ("s1", "s2", "long-a")


def test_a_partially_fitting_sibling_group_can_progress():
    rows, resources, costs = fixture()
    first = replace(rows[0], identity="s1", group="join", unfinished_members=2, logical_tokens=120)
    second = replace(first, identity="s2")
    chosen = select([first, second, *rows[1:]], resources, costs)
    assert chosen["selected"] == ("s1", "long-a")


@pytest.mark.parametrize("change", (
    {"slots": 0}, {"input_tokens": 0, "chunked_input": True},
    {"device_capacity_tokens": 1, "growth_tokens": 2},
    {"cold_device_tokens": -1},
))
def test_prediction_never_invents_slots_input_growth_or_cold_capacity(change):
    rows, resources, costs = fixture()
    rows = [replace(row, input_tokens=1) for row in rows]
    chosen = select(rows, replace(resources, **change), costs)
    assert chosen["selected"] == ("long-a", "long-b")
    assert not chosen["search_complete"]


@pytest.mark.parametrize("deadline", (None, 1, 2))
def test_actual_choice_and_deadline_are_exactly_replayable(deadline):
    row, costs = evidence()
    costs = replace(costs, decode_cost_basis="full_batch", selection_objective="benefit")
    from scripts.audit_learned_combination_prediction import combination_candidates
    from beliefkv.policy.workflow_combination import CombinationResources

    candidates = combination_candidates(row)
    candidates = [
        replace(candidate, weight=.9 if candidate.identity == "a" else .35)
        for candidate in candidates
    ]
    calls = -1

    def clock():
        nonlocal calls
        calls += 1
        return 500_000 if deadline is not None and calls >= deadline else 0

    row["selection_policy"] = "shortest_work_first"
    row["candidates"] = [asdict(candidate) for candidate in candidates]
    row["decision"] = choose_shortest_work_first(
        candidates, ("b", "c"), CombinationResources(**row["resources"]), costs, clock=clock,
    )
    row = json.loads(json.dumps(row))
    actual = replay_choice(row, costs, "frozen")
    assert actual["search_replayed"]
    assert actual["selected"] == (["a"] if deadline is None else ["b", "c"])
    assert actual["search_complete"] is (deadline is None)
    row["decision"]["evaluated_candidates"] = len(candidates) + 1
    with pytest.raises(ValueError):
        replay_choice(row, costs, "frozen")


def tool_controller(*, supported=True):
    keys = [PrefillCandidateKey(**key(owner)) for owner in ("long", "short", "join")]
    predictions = {
        "workflow-" + owner: NS(
            effective_weight=weight, probabilities=(0., 1., 0.), evidence="model",
        ) for owner, weight in (("long", .2), ("short", .9), ("join", .1))
    }
    admission = NS(
        counts=Counter(), runtime=NS(_opportunity_writer=None),
        _supported=lambda value: supported and value is not None,
        _protected_join=lambda value: value.invocation_id == "join",
    )
    controller = WorkflowCombinationController.__new__(WorkflowCombinationController)
    controller.admission = admission
    controller.shortest_work_first = True
    controller.prediction = predictions.get
    return controller, admission, keys


def test_immediate_pair_order_preserves_join_and_does_not_need_free_budget_or_a_timer():
    controller, admission, keys = tool_controller()
    ordered = controller.order_tool_replacement(keys)
    assert ordered == [keys[2], keys[1], keys[0]]
    assert set(ordered) == set(keys)
    assert admission.counts["shortest_work_tool_replacement_order_changes"] == 1
    bridge = NS(_conditional_distribution=True, _combination=controller)
    assert WorkflowSizeAdmission.order_tool_replacement(bridge, keys) == ordered


def test_disabled_or_unsupported_immediate_order_keeps_the_reference():
    controller, _admission, keys = tool_controller(supported=False)
    assert controller.order_tool_replacement(keys) is keys
    controller.shortest_work_first = False
    bridge = NS(_conditional_distribution=True, _combination=controller)
    assert WorkflowSizeAdmission.order_tool_replacement(bridge, keys) is keys


def test_scaled_utility_unknown_is_neutral_in_both_candidate_and_tool_pair_order():
    controller, admission, keys = tool_controller()
    controller.neutral_work_weight = 1. / 1201.
    admission._supported = lambda value: value is not None
    controller.prediction = {
        "workflow-long": NS(effective_weight=1. / 230001.),
        "workflow-short": NS(effective_weight=1. / 201.),
    }.get
    admission._protected_join = lambda value: False
    assert controller._work_weight(keys[2], {}) == 1. / 1201.
    assert controller.order_tool_replacement(keys) == [keys[1], keys[2], keys[0]]
    controller.prediction = lambda workflow: None
    assert controller.order_tool_replacement(keys) is keys


def test_invalid_prediction_keeps_the_live_donor_pair_path_available():
    controller, admission, keys = tool_controller()
    controller.prediction = lambda workflow: NS(effective_weight=float("nan"))
    assert controller.order_tool_replacement(keys) is keys
    assert admission.counts["shortest_work_tool_replacement_metadata_fallbacks"] == 1


@pytest.mark.parametrize("flag", (False, True, 1, "yes", None))
def test_constructor_requires_an_explicit_boolean_flag_and_preserves_default(tmp_path, flag):
    _rows, _resources, costs = fixture()
    raw = {
        "shortest_work_first_admission": flag, "free_dispatch_coalescing_window_ms": 2000,
        "admission_cost_profile": {
            "kind": "r5_frozen_batch_cost_proxy_capacity_v7",
            "identity": "cpu_fixture", "costs": asdict(costs),
        },
    }
    path = tmp_path / "model.json"
    path.write_text(json.dumps(raw))
    admission = NS(runtime=NS(
        workflow_work_model=path, workflow_work_model_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    ))
    if type(flag) is not bool:
        with pytest.raises(ValueError, match="explicit enabled"):
            WorkflowCombinationController(admission)
    else:
        controller = WorkflowCombinationController(admission)
        assert controller.shortest_work_first is flag
        assert controller.window_ms == 2000


def test_final_audit_does_not_accept_a_policy_undeclared_by_the_artifact():
    row, _costs = evidence()
    validate_selection_policy([row])
    with pytest.raises(ValueError, match="registered artifact"):
        validate_selection_policy([row], shortest_work_first=True)
    row["selection_policy"] = "shortest_work_first"
    validate_selection_policy([row], shortest_work_first=True)
    with pytest.raises(ValueError, match="registered artifact"):
        validate_selection_policy([row])


@pytest.mark.parametrize("failure", ("gain", "throughput", "service", "complete", None))
def test_campaign_only_continues_after_complete_mean_gain_and_actual_service(failure):
    from scripts.run_continuous_matched_campaign import stage_promising

    comparison = {"checks": {
        "fixed_measured_mean_JCT_gain": failure != "gain",
        "common_window_output_throughput_noninferior": failure != "throughput",
        "background_covers_both_measured_tails": False,
    }}
    choices = {
        "complete_measured_execution_verified": failure != "complete",
        "counts": {
            "selected_set_changes": 2,
            "newly_selected_periodic_followed_by_exact_service": 0 if failure == "service" else 1,
            "newly_selected_free_followed_by_exact_service": 0,
        },
    }
    assert stage_promising(comparison, choices, allow_tail_diagnostic=True) is (failure is None)
    assert not stage_promising(comparison, choices, allow_tail_diagnostic=False)


@pytest.mark.parametrize("change", (
    {"mean_interval_seconds": 70.}, {"initial_workflows": 12}, {"fixed_measured_workflows": []},
))
def test_campaign_never_switches_back_to_a_different_arrival_workload(change):
    from scripts.run_shortest_work_campaign import validate

    previous = {
        "mean_interval_seconds": 60., "initial_workflows": 20,
        "workflows": 100, "fixed_measured_workflows": list(range(18)),
    }
    previous.update(change)
    with pytest.raises(ValueError, match="mean60"):
        validate(previous, {})


def test_preparation_preserves_the_forecast_and_binds_the_same_oracle_rule(tmp_path):
    from pathlib import Path

    from beliefkv.experiments.source_provenance import sha256_file
    from beliefkv.predictor.workflow_continuous_work import forecast_fingerprint
    from scripts.audit_workflow_combination_choices import verify_model_work_pair
    from tests.test_model_work_choice_audit import model_work_pair

    full, oracle_config, learned_config = model_work_pair(tmp_path)
    old_learned = json.loads(Path(learned_config["common_environment"]["WORKFLOW_WORK_MODEL"]).read_bytes())
    old_oracle = json.loads(Path(oracle_config["common_environment"]["WORKFLOW_WORK_MODEL"]).read_bytes())
    before = deepcopy((old_learned, old_oracle))
    learned, oracle = prepare_artifacts(old_learned, old_oracle)
    assert (old_learned, old_oracle) == before
    assert forecast_fingerprint(learned) == forecast_fingerprint(old_learned)
    assert oracle["requests"] == old_oracle["requests"]
    assert oracle["workflow_graphs"] == old_oracle["workflow_graphs"]
    model_path, truth_path = tmp_path / "short-model.json", tmp_path / "short-oracle.json"
    model_path.write_text(json.dumps(learned))
    oracle["combination_reference_source_model_sha256"] = sha256_file(model_path)
    truth_path.write_text(json.dumps(oracle))
    learned_config = configuration(learned_config, model_path, sha256_file(model_path))
    oracle_config = configuration(oracle_config, truth_path, sha256_file(truth_path))
    verify_model_work_pair(full, oracle_config, learned_config)
    oracle["shortest_work_first_admission"] = False
    truth_path.write_text(json.dumps(oracle))
    for prefix in ("", "BELIEFKV_"):
        oracle_config["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = sha256_file(truth_path)
    with pytest.raises(ValueError, match="declared work provider"):
        verify_model_work_pair(full, oracle_config, learned_config)


def test_short_first_changes_actual_eligibility_and_native_prefill_candidates(monkeypatch, tmp_path):
    runtime, admission, _env = runtime_with_queue(monkeypatch, tmp_path)
    records = []
    runtime._opportunity_writer = NS(record=records.append)
    controller = admission._combination
    controller.shortest_work_first = True
    controller.costs = replace(controller.costs, decode_cost_basis="full_batch", selection_objective="benefit")
    try:
        admission.tick()
        assert {item.identity for item in admission.policy.programs.values() if item.active} == {"a"}
        requests = [admission._live_requests[name + "-request"] for name in ("a", "b", "c")]
        plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        actual = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert actual.candidates == (requests[0],)
        choice = next(row for row in records if row["event"] == "workflow_combination_selection")
        assert choice["selection_policy"] == "shortest_work_first"
        assert not choice["physical_capacity_credit"]
    finally:
        runtime._opportunity_writer = None
        runtime.close()
