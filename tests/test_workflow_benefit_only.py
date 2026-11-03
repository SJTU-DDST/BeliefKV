from copy import deepcopy
from dataclasses import replace
import hashlib
from itertools import chain, repeat
import json
from pathlib import Path

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.policy.workflow_combination import CombinationResources, _evaluate, choose_combination
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.prepare_workflow_benefit_only import KIND, benefit_only_artifact
from tests.test_combination_device_planning import artifact
from tests.test_workflow_combination import candidate, cost
from tests.test_workflow_combination_runtime import configured


def costs():
    return replace(cost(), decode_cost_basis="full_batch", selection_objective="benefit")


def test_expensive_request_of_short_workflow_is_not_divided_by_request_cost():
    rows = [
        replace(candidate("short", 100, .8), input_tokens=9000),
        candidate("long", 100, .4),
    ]
    resources = CombinationResources(100, 100000, 10000, 1)
    expensive = replace(costs(), prefill_ms_per_token=.1)
    ratio = choose_combination(
        rows, ("long",), resources, replace(expensive, selection_objective="benefit_per_cost"), clock=lambda: 0,
    )
    benefit = choose_combination(rows, ("long",), resources, expensive, clock=lambda: 0)
    assert ratio["selected"] == ("long",)
    assert benefit["selected"] == ("short",)
    assert benefit["selected_evaluation"]["score"] == .8
    assert benefit["selected_evaluation"]["selection_objective"] == "benefit"
    assert benefit["selected_evaluation"]["cost_normalized_score"] < ratio["selected_evaluation"]["score"]


def test_cost_does_not_reject_an_additional_ready_agent_when_the_union_fits():
    rows = [
        candidate("resident", 100, .8),
        replace(candidate("expensive", 100, .2), input_tokens=9000),
    ]
    resources = CombinationResources(200, 100000, 10000, 2)
    expensive = replace(costs(), prefill_ms_per_token=.1)
    ratio = choose_combination(
        rows, ("resident",), resources, replace(expensive, selection_objective="benefit_per_cost"), clock=lambda: 0,
    )
    result = choose_combination(rows, ("resident",), resources, expensive, clock=lambda: 0)
    assert ratio["selected"] == ("resident",)
    assert result["selected"] == ("resident", "expensive")
    assert result["selected_evaluation"]["benefit"] == 1.


@pytest.mark.parametrize("resources", (
    CombinationResources(200, 100000, 10000, 1),
    CombinationResources(200, 100000, 1, 2),
    CombinationResources(200, 1, 10000, 2),
    CombinationResources(199, 100000, 10000, 2),
))
def test_benefit_cannot_override_slots_input_device_or_logical_capacity(resources):
    rows = [candidate("a", 100, .8), candidate("b", 100, .2)]
    result = choose_combination(rows, ("a",), resources, costs(), clock=lambda: 0)
    assert result["selected"] == ("a",)


def test_shared_extents_growth_and_join_coverage_keep_the_same_evaluation():
    rows = [
        replace(candidate(name, 100, .8, workflow="wf", group="join", unfinished_members=2),
                missing_extents=((("shared", 1.), 100),))
        for name in ("a", "b")
    ]
    resources = CombinationResources(200, 104, 10000, 2, growth_tokens=2)
    value = _evaluate(rows, resources, costs())
    ratio = _evaluate(rows, resources, replace(costs(), selection_objective="benefit_per_cost"))
    assert value["score"] == value["benefit"] == .8
    assert value["missing_unique_tokens"] == 100
    assert value["cost_normalized_score"] == ratio["score"]
    assert {key: v for key, v in value.items() if key not in (
        "score", "selection_objective", "cost_normalized_score",
    )} == {key: v for key, v in ratio.items() if key != "score"}
    assert _evaluate(rows, replace(resources, growth_tokens=3), costs()) is None


def test_required_join_and_relative_tie_preserve_reference_rights():
    rows = [
        candidate("parent", 100, .1), candidate("old", 100, .5),
        candidate("new", 100, .505),
    ]
    resources = CombinationResources(200, 100000, 10000, 2)
    result = choose_combination(
        rows, ("parent", "old"), resources, costs(), required=("parent",), clock=lambda: 0,
    )
    assert result["selected"] == ("parent", "old")
    rows[2] = replace(rows[2], weight=.8)
    assert choose_combination(
        rows, ("parent", "old"), resources, costs(), required=("parent",), clock=lambda: 0,
    )["selected"] == ("parent", "new")


def test_deadline_keeps_the_last_feasible_benefit_choice_and_original_bounds():
    rows = [candidate("short", 100, 1.)] + [candidate(str(i), 100, .1) for i in range(7)]
    ticks = chain((0, 0), repeat(500000))
    result = choose_combination(
        rows, ("6",), CombinationResources(100, 100000, 10000, 1),
        costs(), clock=lambda: next(ticks),
    )
    assert result["selected"] == ("short",)
    assert not result["search_complete"] and result["evaluated_subsets"] == 8


def test_artifact_changes_only_objective_and_derived_profile_identity():
    original = artifact(plan_device_capacity=True)
    before = deepcopy(original)
    updated = benefit_only_artifact(original)
    assert original == before
    profile = updated["admission_cost_profile"]
    assert profile["kind"] == KIND
    assert profile["costs"] == {
        **before["admission_cost_profile"]["costs"], "selection_objective": "benefit",
    }
    assert profile["identity"] != before["admission_cost_profile"]["identity"]
    updated["admission_cost_profile"] = before["admission_cost_profile"]
    assert updated == before
    with pytest.raises(ValueError, match="fingerprint"):
        invalid = deepcopy(original)
        invalid["admission_cost_profile"]["costs"]["decode_steps"] = 17
        benefit_only_artifact(invalid)


@pytest.mark.parametrize("objective", (None, True, "ratio", "unknown"))
def test_unknown_objective_is_rejected(objective):
    with pytest.raises(ValueError, match="cost profile"):
        replace(costs(), selection_objective=objective)


@pytest.mark.parametrize("kind,objective,valid", (
    ("r5_frozen_batch_cost_proxy_capacity_v6", "benefit_per_cost", True),
    ("r5_frozen_batch_cost_proxy_capacity_v6", "benefit", False),
    (KIND, "benefit_per_cost", False),
    (KIND, "benefit", True),
))
def test_actual_runtime_requires_explicit_matching_objective(monkeypatch, tmp_path, kind, objective, valid):
    config = configured(tmp_path)
    path = Path(config["WORKFLOW_WORK_MODEL"])
    raw = json.loads(path.read_bytes())
    raw["admission_cost_profile"]["kind"] = kind
    raw["admission_cost_profile"]["costs"].update(
        decode_cost_basis="full_batch", selection_objective=objective,
    )
    path.write_text(json.dumps(raw))
    config["WORKFLOW_WORK_MODEL_SHA256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name, value in arm_environment(config, "full").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    if not valid:
        with pytest.raises(ValueError, match="explicit v7"):
            NativeAdmissionRuntime(event_socket_path=str(tmp_path / "benefit.sock"))
        return
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "benefit.sock"))
    try:
        controller = runtime._agent_admission._combination
        assert controller.costs.selection_objective == objective
        assert controller.plan_device_capacity and controller.allow_feasible_resource_alternatives
        assert controller.allow_deep_prefix_aggregate and controller.include_unreferenced_capacity
    finally:
        runtime.close()


@pytest.mark.parametrize("declared", (False, True))
def test_complete_pair_review_requires_explicit_benefit_objective(tmp_path, declared):
    from beliefkv.experiments.source_provenance import sha256_file
    from scripts.review_learned_work_ranking import verify_learned_combination_pair
    from tests.test_learned_work_ranking_review import learned_pair

    control, learned = learned_pair(tmp_path)
    path = Path(learned["common_environment"]["WORKFLOW_WORK_MODEL"])
    raw = json.loads(path.read_bytes())
    raw["admission_cost_profile"]["kind"] = KIND
    raw["admission_cost_profile"]["costs"]["selection_objective"] = "benefit"
    path.write_text(json.dumps(raw))
    for prefix in ("", "BELIEFKV_"):
        learned["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = sha256_file(path)
    if declared:
        learned["comparison_contract"]["combination_selection_objective"] = "benefit"
        assert len(verify_learned_combination_pair(control, learned)) == 2
    else:
        with pytest.raises(ValueError, match="explicit learned"):
            verify_learned_combination_pair(control, learned)
