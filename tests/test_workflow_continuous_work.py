from copy import deepcopy
from dataclasses import asdict
from itertools import chain, repeat
import math

import pytest

from beliefkv.policy.workflow_combination import (
    CombinationCandidate, CombinationCosts, CombinationResources, choose_combination, probability_weight,
)
from beliefkv.predictor.workflow_conditional_tree import KIND as TREE_KIND
from beliefkv.predictor.workflow_continuous_work import (
    ContinuousWorkReadout, fit_training_reference, remaining_work_weight,
)
from beliefkv.predictor.workflow_progress_tree import FEATURES
from beliefkv.predictor.workflow_work import R5_FEATURES, WorkflowWorkModel
from beliefkv.predictor.workflow_work_readout import representation_fingerprint
from scripts.audit_continuous_work_combinations import inspect
from tests.test_workflow_work_distribution import distribution_raw


def observed(output):
    state = [0.] * len(R5_FEATURES)
    state[R5_FEATURES.index("output_tokens")] = output
    state[R5_FEATURES.index("ready")] = 1.
    return state


def model():
    raw = distribution_raw()
    raw["work_thresholds"] = [1000., 2000.]
    base = WorkflowWorkModel(raw)
    raw["conditional_progress_tree_head"] = {
        "kind": TREE_KIND, "features": list(FEATURES) + ["frozen_hidden_0"],
        "representation_sha256": representation_fingerprint(base),
        "trees": [[
            [FEATURES.index("log_output_tokens"), math.log1p(3.), 1, 2, 0.],
            [-1, 0., -1, -1, math.log1p(24000.)],
            [-1, 0., -1, -1, math.log1p(230000.)],
        ]],
        "maximum_depth": 1, "blend": 1., "calibration_offset": 0., "calibration_sigma": .05,
    }
    return WorkflowWorkModel(raw)


def training_rows():
    return [{
        "workflow": "train-" + str(index), "project": "project-" + str(index),
        "snapshot": 0, "total_work": work, "attained_work": 0., "remaining_work": work,
    } for index, work in enumerate((100., 200., 1200., 1600., 24000., 230000.))]


def contract():
    return fit_training_reference(training_rows(), model().thresholds)


def test_training_reference_uses_only_unserved_training_execution_labels():
    rows = training_rows()
    before = deepcopy(rows)
    actual = fit_training_reference(rows, (1000., 2000.))
    assert rows == before
    assert actual["reference_work"] == 1200.
    assert actual["initial_class_execution_counts"] == [2, 2, 2]
    assert actual["training_executions"] == actual["training_projects"] == 6
    extended = rows + [{**row, "snapshot": 1, "attained_work": 100.} for row in rows]
    assert fit_training_reference(extended, (1000., 2000.)) == actual
    for invalid in (rows + [rows[0]], rows[:-1]):
        with pytest.raises(ValueError):
            fit_training_reference(invalid, (1000., 2000.))


@pytest.mark.parametrize("work,reference", ((-1., 1.), (math.inf, 1.), (1., 0.), (1., math.nan), (True, 1.)))
def test_continuous_utility_rejects_invalid_units(work, reference):
    with pytest.raises(ValueError):
        remaining_work_weight(work, reference)


def test_continuous_utility_has_work_based_scale_and_no_class_saturation():
    assert remaining_work_weight(0., 1200.) == 1.
    assert remaining_work_weight(1200., 1200.) == .5
    assert remaining_work_weight(24000., 1200.) > remaining_work_weight(230000., 1200.) > 0.
    assert remaining_work_weight(1e300, 1e-200) == 0.


def test_continuous_readout_recovers_distinct_work_hidden_by_equal_long_weights():
    serving = model()
    readout = ContinuousWorkReadout(serving, contract())
    projection = serving.task_projection("ab")
    values = []
    for output in (2., 4.):
        state = observed(output)
        probabilities = serving.predict(projection, state)
        assert probability_weight(probabilities) == .25
        values.append(readout.predict(
            projection, state, probabilities=probabilities, support="calibrated_distribution",
        ))
    assert [value["remaining_work_point"] for value in values] == pytest.approx([24000., 230000.])
    assert values[0]["effective_weight"] > values[1]["effective_weight"]
    assert all(value["source"] == "calibrated_continuous_tree_log_work" for value in values)
    unsupported = readout.predict(
        projection, observed(2.), probabilities=(0., 0., 1.), support="outside_training_support",
    )
    assert unsupported["effective_weight"] == .5
    initial = readout.predict(
        projection, observed(0.), probabilities=(0., 0., 1.), support="calibrated_distribution",
    )
    assert initial["source"] == "training_only_initial_class_log_prior"
    assert initial["log1p_remaining_work"] == contract()["initial_class_mean_log_work"][2]


def key(owner):
    return {
        "request_id": owner + "-request", "root_workflow_id": owner + "-workflow",
        "invocation_id": owner, "context_id": owner + "-context", "context_epoch": 0,
        "attempt_id": 0, "session_id": owner + "-session", "session_generation": 1,
    }


def choice_fixture(*, protected=False, deadline=False):
    candidates = [
        CombinationCandidate(owner, owner + "-workflow", 10, 1, index, .25, owner + "-group",
                             input_tokens=1, context_tokens=100)
        for index, owner in enumerate(("long", "short"))
    ]
    required, reference = (), ("long",)
    if protected:
        candidates.append(CombinationCandidate(
            "protected", "protected-workflow", 10, 0, 2, .25, "JOIN",
            unfinished_members=2, active_ready_members=1, input_tokens=1, context_tokens=100,
        ))
        required, reference = ("protected",), ("protected", "long")
    resources = CombinationResources(
        logical_tokens=20 if protected else 10, device_tokens=100, input_tokens=10,
        slots=2 if protected else 1, device_capacity_tokens=100,
    )
    costs = CombinationCosts(.1, .1, (.5, .6, .7), 100., decode_steps=1, decode_cost_basis="full_batch")
    times = chain((0,), repeat(500_000))
    decision = choose_combination(
        candidates, reference, resources, costs, required=required,
        clock=(lambda: next(times)) if deadline else lambda: 0,
        allow_feasible_resource_alternatives=True,
    )
    row = {
        "event": "workflow_combination_selection", "source": "periodic_decay_resume", "monotonic_s": 1.,
        "cost_identity": "test-cost", "physical_capacity_credit": False,
        "metadata_and_selection_cpu_ns": 10, "metadata_and_selection_wall_ns": 10,
        "resource_fallback_selection_enabled": True, "device_fit_scope": "planned_pool_geometry",
        "decision": decision, "resources": asdict(resources),
        "candidates": [asdict(candidate) for candidate in candidates],
        "keys": {candidate.identity: key(candidate.identity) for candidate in candidates},
    }
    serving = model()
    entries = []
    for candidate in candidates:
        short = candidate.identity == "short"
        state = observed(2. if short else 4.)
        probabilities = serving.predict(serving.task_projection("ab"), state)
        entries.append({
            "key": key(candidate.identity), "features": state,
            "observed_remaining_model_work": 24000. if short else 230000.,
            "predictions": {"tree": {
                "probabilities": probabilities, "effective_weight": probability_weight(probabilities),
                "support": "calibrated_distribution",
            }},
        })
    return row, {"monotonic_s": 1., "candidates": entries}, costs, serving


@pytest.mark.parametrize("protected", (False, True))
def test_weight_amplitude_changes_feasible_set_without_revoking_join_rights(protected):
    row, observation, costs, serving = choice_fixture(protected=protected)
    result = inspect(
        [row], [observation], costs, {"identity": "test-cost"}, serving, contract(),
        {candidate["workflow"]: "ab" for candidate in row["candidates"]}, {"short-workflow", "long-workflow"},
    )
    decision = result["decisions"][0]
    assert "long" in decision["selections"]["tree_binned"]["selected"]
    assert "short" in decision["selections"]["tree_continuous"]["selected"]
    assert decision["selections"]["tree_continuous"]["selected"] == (
        decision["selections"]["oracle_continuous"]["selected"]
    )
    if protected:
        assert all("protected" in value["selected"] for value in decision["selections"].values())
    assert result["selection_counts"]["tree_continuous"]["changes_tree_binned_set"] == 1
    assert result["ranking"]["tree_binned"]["both_development_workflows"]["boundary_equal_weight_accuracy"] == .5
    assert result["ranking"]["tree_continuous"]["both_development_workflows"]["boundary_equal_weight_accuracy"] == 1.


def test_incomplete_actual_search_is_reproduced_but_not_used_for_counterfactual():
    row, observation, costs, serving = choice_fixture(deadline=True)
    assert not row["decision"]["search_complete"]
    result = inspect(
        [row], [observation], costs, {"identity": "test-cost"}, serving, contract(),
        {"long-workflow": "ab", "short-workflow": "ab"}, set(),
    )
    assert result["counts"]["incomplete_searches_excluded"] == 1
    assert result["decisions"] == []
