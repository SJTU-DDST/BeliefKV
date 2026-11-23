from copy import deepcopy
from dataclasses import replace
import math
from types import SimpleNamespace as NS

import pytest

from beliefkv.predictor.workflow_completion_path import (
    WORK_PRIOR_KIND, RESIDUAL_KIND, feature_names, model_work_log_seconds, work_forecast_fingerprint,
)
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from beliefkv.policy.workflow_shortest_frontier import choose_shortest_work_first
from scripts.optimize_r5_join_path import (
    choose, evaluate_choices, joint_difference, prepared_decisions, selection_key, validation_windows,
)
from tests.test_r5_completion_path_audit import row
from tests.test_workflow_completion_path import path_model
from tests.test_workflow_continuous_work import observed
from tests.test_workflow_shortest_frontier import fixture


def model_with_work_prior():
    base = path_model()
    raw = deepcopy(base.raw)
    profile = raw["completion_path_readout"]
    names = list(feature_names(base, with_work_prior=True))
    profile.update(
        kind=WORK_PRIOR_KIND, features=names,
        work_forecast_sha256=work_forecast_fingerprint(base),
        trees=[[
            [names.index("predicted_log_remaining_model_work"), math.log1p(100000.), 1, 2, 0.],
            [-1, 0., -1, -1, math.log1p(10.)],
            [-1, 0., -1, -1, math.log1p(200.)],
        ]],
    )
    return WorkflowWorkModel(raw)


def test_existing_work_forecast_is_an_observed_path_input_and_uses_one_pass(monkeypatch):
    model = model_with_work_prior()
    calls = []
    score = model.conditional_tree.tree.score
    monkeypatch.setattr(
        model.conditional_tree.tree, "score", lambda values: (calls.append(1), score(values))[1],
    )
    paths = []
    for progress in (2., 4.):
        _, value = model.predict(
            model.task_projection("ab"), observed(progress), with_work=True, path_timing=[0.] * 6,
        )
        paths.append(value["remaining_path_s"])
    assert paths == pytest.approx([10., 200.])
    assert len(calls) == 2


def test_work_prior_head_binds_the_actual_conditional_forecast():
    from beliefkv.predictor.workflow_continuous_work import forecast_fingerprint

    raw = deepcopy(model_with_work_prior().raw)
    raw["conditional_progress_tree_head"]["calibration_offset"] += .2
    raw["continuous_work_readout"]["forecast_sha256"] = forecast_fingerprint(raw)
    with pytest.raises(ValueError, match="forecast binding differs"):
        WorkflowWorkModel(raw)
    raw = deepcopy(model_with_work_prior().raw)
    raw["completion_path_readout"].pop("work_forecast_sha256")
    with pytest.raises(ValueError):
        WorkflowWorkModel(raw)


@pytest.mark.parametrize("capacity,expected_siblings", ((216, {"s1", "s2"}), (65, {"s1"})))
def test_predicted_short_priority_jointly_admits_ready_siblings_across_recovery_categories(
    capacity, expected_siblings,
):
    model = model_with_work_prior()
    weights = {}
    for workflow, progress in (("short", 2.), ("long", 4.)):
        _, forecast = model.predict(
            model.task_projection("ab"), observed(progress), with_work=True, path_timing=[0.] * 6,
        )
        weights[workflow] = 1. / (1. + forecast["remaining_path_s"] / model.completion_path.reference_s)
    rows, resources, costs = fixture()
    siblings = [
        replace(
            rows[0], identity=identity, workflow="short", group="actual-join",
            logical_tokens=60, context_tokens=60, category=category,
            weight=weights["short"], unfinished_members=3,
        ) for identity, category in (("s1", 0), ("s2", 2))
    ]
    long = replace(
        rows[1], identity="long", workflow="long", logical_tokens=100, context_tokens=100,
        weight=weights["long"],
    )
    result = choose_shortest_work_first(
        [long, *siblings], ("long",), replace(resources, logical_tokens=capacity), costs,
        strict_remaining_work=True, clock=lambda: 0,
    )
    assert result["search_complete"]
    assert set(result["selected"]) & {"s1", "s2"} == expected_siblings
    assert "long" not in result["selected"]
    assert result["group_ready_siblings"]


def test_residual_head_preserves_work_scale_within_a_shared_tree_leaf():
    raw = deepcopy(model_with_work_prior().raw)
    raw["completion_path_readout"].update(
        kind=RESIDUAL_KIND, trees=[[[-1, 0., -1, -1, 0.]]], calibration_offset=0.,
    )
    model = WorkflowWorkModel(raw)
    estimates = []
    for progress in (2., 4.):
        _, value = model.predict(
            model.task_projection("ab"), observed(progress), with_work=True, path_timing=[0.] * 6,
        )
        estimates.append(value["remaining_path_s"])
        assert value["remaining_path_s"] == pytest.approx(
            value["remaining_work_point"] * model.completion_path.raw["model_seconds_per_work_token"],
        )
    assert estimates[1] > estimates[0] * 9
    assert model_work_log_seconds({"log1p_remaining_work": 0.}, .02) == 0.
    assert model_work_log_seconds({"log1p_remaining_work": 1000.}, .02) == pytest.approx(1000. + math.log(.02))


def test_join_metric_distinguishes_joint_ready_admission_from_unready_members():
    rows, _resources, _costs = fixture()
    first = replace(rows[0], identity="a", group="join", unfinished_members=3)
    second = replace(first, identity="b")
    joint = joint_difference([first, second], ("a",), ("a", "b"))
    assert joint["missed_oracle_multi_ready_sibling_groups"] == 1
    assert joint["different_JOIN_frontiers"] == 1
    assert joint_difference([first, second], ("a", "b"), ("a", "b"))[
        "missed_oracle_full_visible_JOIN_frontiers"
    ] == 0
    assert joint_difference([first, second], ("a",), ("a",))["different_JOIN_frontiers"] == 0


def test_model_selection_uses_join_and_set_quality_before_scalar_error():
    def metrics(join, sets, error):
        return {
            "calibration_whole_windows": {
                "boundaries": 10, "different_JOIN_frontiers": join, "set_differences": sets,
            },
            "calibration_pairs": {"pairs": 10, "wrong": 1, "ties": 0},
            "calibration_mean_workflow_absolute_log_error": error,
        }

    assert selection_key(metrics(0, 2, 100.)) < selection_key(metrics(1, 1, 0.))
    assert selection_key(metrics(0, 1, 100.)) < selection_key(metrics(0, 2, 0.))


def test_validation_excludes_fixed18_labels_even_when_their_candidates_are_present(monkeypatch):
    from scripts import optimize_r5_join_path as module

    candidates, resources, costs = fixture()
    original = row(candidates, resources, ("long-a", "long-b"))
    partition = {
        candidates[0].workflow: "evaluation", candidates[1].workflow: "calibration",
        candidates[2].workflow: "training",
    }
    original["states"] = {
        candidates[0].workflow: {"no_evaluation_target_available": True},
        candidates[1].workflow: {"features": [10.], "timing": [0.] * 6, "path": [10.]},
        candidates[2].workflow: {"features": [100.], "timing": [0.] * 6, "path": [100.]},
    }
    items = prepared_decisions({"decisions": [original]}, costs)
    assert not validation_windows(items, partition)
    monkeypatch.setattr(module, "support_status", lambda *_args: "calibrated_distribution")
    model = NS(
        completion_path=NS(raw={"reference_path_s": 1., "model_seconds_per_work_token": 1.}),
        predict=lambda _projection, features, **_kwargs: (None, {"remaining_path_s": features[0]}),
    )
    report = evaluate_choices(
        model, items, {wf: (None, 1.) for wf in partition}, costs, partition, validation_only=True,
    )
    assert report["calibration_whole_windows"]["boundaries"] == 0
    assert report["calibration_pairs"]["pairs"] == 1
    assert report["calibration_pairs"]["wrong"] == 0


def test_project_folds_keep_same_project_together_and_exclude_measurement():
    from scripts.crossvalidate_r5_join_path import fold_assignment

    projects = {"a": "project-a", "b": "project-a", "c": "project-c", "measured": "project-m"}
    assignment = fold_assignment(projects, {"measured"})
    assert assignment["a"] == assignment["b"]
    assert "measured" not in assignment
    assert assignment == fold_assignment(dict(reversed(list(projects.items()))), {"measured"})


def test_crossfit_choice_rejects_a_competitor_without_its_own_project_score():
    from scripts.crossvalidate_r5_join_path import heldout_choice_review

    candidates, resources, costs = fixture()
    candidates[1:] = [
        replace(peer, workflow="long-a", group="join", unfinished_members=2)
        for peer in candidates[1:]
    ]
    original = row(candidates, resources, ("long-a", "long-b"))
    original["states"] = {value.workflow: {} for value in candidates}
    items = prepared_decisions({"decisions": [original]}, costs)
    offsets = {id(item): {wf: index for index, wf in enumerate(original["states"])} for item in items}
    assignment = {wf: index for index, wf in enumerate(original["states"])}
    truth = [.9, .4]
    verified = heldout_choice_review(items, costs, truth, truth, offsets, assignment)
    assert verified["workflow_equal_choice_error"] == 0
    assert verified["workflow_equal_JOIN_error"] == 0
    assert verified["common"]["workflow_equal_JOIN_error"] is None
    items[0]["in_common_window"] = True
    common = heldout_choice_review(items, costs, truth, truth, offsets, assignment)["common"]
    assert common == {key: value for key, value in verified.items() if key != "common"}
    with pytest.raises(ValueError, match="each competitor"):
        heldout_choice_review(items, costs, [.9, float("nan")], truth, offsets, assignment)


@pytest.mark.parametrize("guard", (None, False, True))
def test_crossfit_scores_the_workflow_and_join_frontiers_under_the_recorded_capacity_guard(guard):
    from scripts.crossvalidate_r5_join_path import heldout_choice_review

    candidates, resources, costs = fixture()
    candidates[1:] = [
        replace(peer, workflow="long", group="join", unfinished_members=2)
        for peer in candidates[1:]
    ]
    reference = ("long-b", "long-a")
    original = row(candidates, resources, reference)
    if guard is not None:
        original["preserve_reference_service_capacity"] = guard
    items = prepared_decisions({"decisions": [original]}, costs)
    item, = items
    item["in_common_window"] = True
    assignment = {"short": 0, "long": 1}
    offsets = {id(item): assignment}
    expected_error = float(not guard)
    report = heldout_choice_review(items, costs, [.1, .9], [.9, .1], offsets, assignment)
    for scope in (report, report["common"]):
        assert scope["workflow_equal_choice_error"] == expected_error
        assert scope["workflow_equal_JOIN_error"] == expected_error

    original["preserve_reference_service_capacity"] = "false"
    with pytest.raises(ValueError, match="reference service capacity guard"):
        choose(item, {"short": .9, "long": .1}, costs)


def test_crossfit_selection_requires_both_workflow_and_join_noninferiority():
    from scripts.crossvalidate_r5_join_path import choice_noninferior, select_variant

    def metrics(choice, join):
        return {
            "validated_workflows": 4,
            "validated_JOIN_workflows": 3,
            "workflow_equal_choice_error": choice, "workflow_equal_JOIN_error": join,
        }

    def review(choice, join, *, common=None):
        return {
            **metrics(choice, join), "workflow_equal_absolute_log_error": 1.,
            "common": metrics(*(common or (choice, join))),
        }

    reviews = {
        "original_absolute": review(.3, .2),
        "short_only_regression": review(.1, .3),
        "joint_only_regression": review(.4, .1),
        "common_workflow_regression": review(.1, .1, common=(.4, .1)),
        "common_JOIN_regression": review(.1, .1, common=(.1, .3)),
        "both_improve": review(.2, .1),
    }
    missing_common = review(.1, .1)
    missing_common["common"]["validated_JOIN_workflows"] = 0
    missing_common["common"]["workflow_equal_JOIN_error"] = None
    assert not choice_noninferior(missing_common, reviews["original_absolute"])
    assert not choice_noninferior(reviews["both_improve"], missing_common)
    reviews["unverified_common"] = missing_common
    assert select_variant(reviews) == "both_improve"
    reviews.pop("both_improve")
    assert select_variant(reviews) == "original_absolute"


def test_rank_query_sampling_ignores_targets_and_relevance_orders_shorter_paths_higher():
    from scripts.rank_r5_join_path import query_labels, rank_queries

    workflows = ["a", "b", "c", "a", "b", "c", "heldout"]
    labels = [100., 10., 1000., 50., 5., 500., 0.]
    actual, synthetic = rank_queries(
        [], {}, workflows, labels, {"a", "b", "c"}, synthetic_count=10, seed=21,
    )
    assert not actual
    assert len(synthetic) == 10
    for query in synthetic:
        assert len({workflows[index] for index in query}) == 3
        assert 6 not in query
        relevance = query_labels(query, labels)
        assert all(
            (labels[left] - labels[right]) * (a - b) < 0
            for left, a in zip(query, relevance) for right, b in zip(query, relevance) if left != right
        )
    _, changed = rank_queries(
        [], {}, workflows, list(reversed(labels)), {"a", "b", "c"}, synthetic_count=10, seed=21,
    )
    assert changed == synthetic


def test_rank_seconds_calibration_preserves_shorter_relevance_order():
    import numpy as np
    from scripts.rank_r5_join_path import calibration

    slope, intercept = calibration(
        np.asarray([-2., 0., 2.]), np.asarray([7., 5., 3.]), np.ones(3),
    )
    assert slope == pytest.approx(-1.)
    assert intercept == pytest.approx(5.)


def test_rank_training_equalizes_workflows_without_fanout_or_queue_frequency_reward():
    from collections import defaultdict
    from scripts.rank_r5_join_path import training_queries

    workflows = ["a", "b", "a", "c", "a", "d"]
    queries = [[0, 1], [2, 3], [4, 5]]
    indices, relevance, sizes, weights = training_queries(
        [queries], [1., 2., 1., 3., 1., 4.], workflows, weighting="workflow",
    )
    totals = defaultdict(float)
    for index, weight in zip(indices, weights):
        totals[workflows[index]] += weight
    assert sizes == [2, 2, 2]
    assert relevance == [1, 0, 1, 0, 1, 0]
    assert list(totals.values()) == pytest.approx([1.5] * 4)
    with pytest.raises(ValueError, match="distinct workflows"):
        training_queries([[[0, 2]]], [1.] * 6, workflows, weighting="workflow")
