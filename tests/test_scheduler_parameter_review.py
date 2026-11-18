from dataclasses import asdict, replace
from types import SimpleNamespace as NS

import pytest

from beliefkv.policy.workflow_combination import CombinationResources, _evaluate
from beliefkv.policy.workflow_maximal_admission import choose_maximal_admission
from beliefkv.policy.workflow_size_resume import WorkflowToolState
from beliefkv.runtime.ready_wait_priority import ReadyWaitPriority
from beliefkv.runtime.workflow_tool_pressure import ToolRetentionSettings, WorkflowToolPressure
from tests.test_shortest_work_admission import tool_controller
from tests.test_workflow_combination import candidate, cost
from tests.test_workflow_tool_pressure import policy
from beliefkv.policy.paper_baselines import ThunderProgram


def choose(rows, profile, *, required=()):
    return choose_maximal_admission(
        rows, ("long",), CombinationResources(100, 100000, 2048, 4),
        profile, required=required, selection_objective="benefit", clock=lambda: 0,
    )


def profile(**kwargs):
    return replace(cost(), decode_cost_basis="full_batch", selection_objective="benefit", **kwargs)


def test_linear_progress_does_not_apply_an_extra_fanout_penalty_to_a_short_workflow():
    rows = [
        candidate("child", 100, 1., workflow="short", group="join", unfinished_members=4),
        candidate("long", 100, .15),
    ]
    assert choose(rows, profile())["selected"] == ("long",)
    result = choose(rows, profile(join_progress="linear"))
    assert result["selected"] == ("child",)
    assert result["selected_evaluation"]["benefit"] == .25
    assert result["selected_evaluation"]["join_progress"] == "linear_unfinished_share_v1"


@pytest.mark.parametrize("total,active,selected,expected", (
    (4, 0, 1, .25), (4, 0, 4, 1.), (4, 2, 2, .5), (1, 0, 1, 1.),
))
def test_linear_progress_has_interpretable_marginal_shares_and_no_fanout_bonus(
    total, active, selected, expected,
):
    rows = [
        candidate(
            str(index), 10, 1., workflow="same", group="join",
            unfinished_members=total, active_ready_members=active,
        ) for index in range(selected)
    ]
    result = _evaluate(rows, CombinationResources(100, 100000, 2048, 4), profile(join_progress="linear"))
    assert result["benefit"] == expected


def test_join_rights_and_jointly_fitting_siblings_remain_in_force():
    rows = [
        candidate("child", 100, 1., workflow="short", group="join", unfinished_members=4),
        candidate("long", 100, .15),
    ]
    assert choose(rows, profile(join_progress="linear"), required=("long",))["selected"] == ("long",)
    siblings = [
        candidate(str(index), 20, 1., workflow="short", group="join", unfinished_members=4)
        for index in range(4)
    ]
    result = choose_maximal_admission(
        siblings, ("0",), CombinationResources(100, 100000, 2048, 4),
        profile(join_progress="linear"), selection_objective="benefit", clock=lambda: 0,
    )
    assert set(result["selected"]) == {"0", "1", "2", "3"}
    assert result["selected_evaluation"]["benefit"] == 1.


@pytest.mark.parametrize("kwargs", (
    {"prefill_ms_per_token": True}, {"h2d_ms_per_token": True},
    {"reference_context_tokens": True}, {"epsilon_ms": True}, {"relative_tie": False},
    {"decode_ms_by_width": (True,)}, {"join_progress": "cubic"},
))
def test_profile_rejects_implicit_boolean_costs_and_unknown_progress_rules(kwargs):
    with pytest.raises(ValueError, match="cost profile"):
        replace(cost(), **kwargs)


def make_pressure(weights, *, relative=None, ranks=None):
    states = {name: WorkflowToolState(True, True, value) for name, value in weights.items()}
    live = policy(states, ranks=ranks)
    live.tool_retention_relative_tie = relative
    live.programs = {
        name: ThunderProgram(name, tokens, steps=2, active=True, acting=True)
        for name, tokens in (("cold", 90), ("warm", 20))
    }
    return live


def test_relative_retention_does_not_merge_clearly_different_tools_into_one_quarter_band():
    old = make_pressure({"cold": .02, "warm": .24})
    old.tick(now=10.)
    assert not old.programs["warm"].active
    revised = make_pressure({"cold": .02, "warm": .24}, relative=.01)
    revised.tick(now=10.)
    assert not revised.programs["cold"].active
    assert revised.programs["warm"].active


def test_a_quarter_boundary_does_not_override_work_order_for_nearly_equal_retention():
    ranks = {"cold": 1., "warm": 100.}
    old = make_pressure({"cold": .249, "warm": .251}, ranks=ranks)
    old.tick(now=10.)
    assert not old.programs["cold"].active
    revised = make_pressure({"cold": .249, "warm": .251}, relative=.01, ranks=ranks)
    revised.tick(now=10.)
    assert not revised.programs["warm"].active


def test_zero_retention_does_not_acquire_an_arbitrary_absolute_tolerance():
    live = make_pressure({"cold": 0., "warm": 1e-12}, relative=.01)
    live.tick(now=10.)
    assert not live.programs["cold"].active and live.programs["warm"].active


@pytest.mark.parametrize("interval_s", (.5, 1., 2., 5.))
def test_explicit_tool_grace_and_half_life_are_independent_of_the_control_period(interval_s):
    settings = ToolRetentionSettings()
    admission = NS(
        control_interval_s=interval_s,
        _work=NS(model=NS(raw={"tool_retention_policy": asdict(settings)})),
    )
    helper = WorkflowToolPressure(admission)
    assert helper._elapsed_retention(1.) == 1.
    assert helper._elapsed_retention(2.) == 1.
    assert helper._elapsed_retention(3.) == .5
    assert helper._elapsed_retention(4.) == .25


@pytest.mark.parametrize("patch", (
    {"short_grace_ms": True}, {"short_grace_ms": -1},
    {"half_life_ms": 0}, {"half_life_ms": 1.5},
    {"relative_tie": True}, {"relative_tie": .11}, {"relative_tie": float("nan")},
    {"kind": "unbounded"},
))
def test_tool_retention_requires_bounded_explicit_parameters(patch):
    raw = {**asdict(ToolRetentionSettings()), **patch}
    with pytest.raises(ValueError, match="tool retention"):
        ToolRetentionSettings.from_artifact({"tool_retention_policy": raw})


@pytest.mark.parametrize("patch", ({}, {"kind": "relative_tool_retention_v2"}, None))
def test_partial_tool_retention_declarations_are_rejected(patch):
    with pytest.raises(ValueError, match="tool retention"):
        ToolRetentionSettings.from_artifact({"tool_retention_policy": patch})


def test_missing_continuous_utility_cannot_become_a_unit_scale_short_task():
    control, admission, keys = tool_controller()
    admission._protected_join = lambda key: False
    control.continuous_work_weights = True
    control.neutral_work_weight = 1. / 20000.
    values = {
        keys[0].root_workflow_id: NS(evidence="model", probabilities=(1., 0., 0.)),
        keys[1].root_workflow_id: NS(evidence="model", effective_weight=1. / 10000.),
    }
    control.prediction = values.get
    control.ready_wait_priority = ReadyWaitPriority(admission, 120000)
    assert control._work_weight(keys[0], {}) == control.neutral_work_weight
    assert control.order_tool_replacement(keys[:2]) == [keys[1], keys[0]]


def test_only_wrong_scale_predictions_preserve_the_original_order():
    control, admission, keys = tool_controller()
    admission._protected_join = lambda key: False
    control.continuous_work_weights = True
    control.prediction = lambda workflow: NS(evidence="model", probabilities=(1., 0., 0.))
    assert control.order_tool_replacement(keys) is keys


@pytest.mark.parametrize("weight", (None, True, -.1, 1.1, float("nan"), float("inf")))
def test_invalid_continuous_weights_use_the_same_scale_neutral_fallback(weight):
    control, admission, keys = tool_controller()
    control.continuous_work_weights = True
    control.neutral_work_weight = 1. / 20000.
    control.prediction = lambda _: NS(
        evidence="model", probabilities=(1., 0., 0.), effective_weight=weight,
    )
    assert control._work_weight(keys[0], {}) == control.neutral_work_weight
