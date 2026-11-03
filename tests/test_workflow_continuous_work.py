from copy import deepcopy
from dataclasses import asdict
from itertools import chain, repeat
import json
import math

import pytest

from beliefkv.policy.workflow_combination import (
    CombinationCandidate, CombinationCosts, CombinationResources, choose_combination, probability_weight,
)
from beliefkv.predictor.workflow_conditional_tree import KIND as TREE_KIND
from beliefkv.predictor.workflow_continuous_work import (
    ContinuousWorkReadout, fit_training_reference, log_work_weight, normal_log_work_weight, remaining_work_weight,
)
from beliefkv.predictor.workflow_progress_tree import FEATURES
from beliefkv.predictor.workflow_work import R5_FEATURES, WorkflowWorkModel
from beliefkv.predictor.workflow_work_readout import representation_fingerprint
from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_continuous_work_combinations import (
    SEMANTIC_VARIANT, inspect, load_semantic_initial_study,
)
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


def test_uncertainty_utility_uses_the_existing_log_distribution_and_remains_bounded():
    reference = 19144.06
    for work in (0., 1., 24000., 230000., 1e200):
        assert log_work_weight(math.log1p(work), reference) == pytest.approx(
            remaining_work_weight(work, reference), rel=1e-12,
        )
        assert normal_log_work_weight(math.log1p(work), 0., reference) == log_work_weight(
            math.log1p(work), reference,
        )
    assert normal_log_work_weight(math.log1p(230000.), 1., reference) > remaining_work_weight(230000., reference)
    assert normal_log_work_weight(math.log1p(100.), 1., reference) < remaining_work_weight(100., reference)
    assert 0 <= normal_log_work_weight(1000., 1., reference) <= 1
    assert 0 <= normal_log_work_weight(-1000., 1., reference) <= 1
    for invalid in (-1., math.nan, math.inf):
        with pytest.raises(ValueError):
            normal_log_work_weight(10., invalid, reference)


def test_initial_distribution_utility_uses_training_class_work_instead_of_fixed_class_coefficients():
    serving, fitted = model(), contract()
    readout = ContinuousWorkReadout(serving, fitted)
    initial = readout.predict(
        serving.task_projection("ab"), observed(0.), probabilities=(0., 0., 1.),
        support="calibrated_distribution",
    )
    expected = sum(remaining_work_weight(work, fitted["reference_work"]) for work in (24000., 230000.)) / 2.
    assert initial["distribution_effective_weight"] == pytest.approx(expected)
    assert fitted["initial_class_expected_utility"] != [1., .5, .25]
    assert initial["distribution_effective_weight"] != initial["effective_weight"]


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


@pytest.mark.parametrize("protected", (False, True))
def test_semantic_initial_weight_uses_exact_task_and_keeps_join_rights(protected):
    row, observation, costs, serving = choice_fixture(protected=protected)
    texts = {
        candidate["workflow"]: "ab" if candidate["identity"] == "short" else "abab"
        for candidate in row["candidates"]
    }
    for entry in observation["candidates"]:
        entry["features"] = observed(0.)
        probabilities = serving.predict(
            serving.task_projection(texts[entry["key"]["root_workflow_id"]]), entry["features"],
        )
        entry["predictions"]["tree"].update(
            probabilities=probabilities, effective_weight=probability_weight(probabilities),
        )
    calls = []

    def semantic(text, probabilities):
        calls.append((text, probabilities))
        return {"distribution_effective_weight": .8 if text == "ab" else .2}

    result = inspect(
        [row], [observation], costs, {"identity": "test-cost"}, serving, contract(),
        texts, {"short-workflow", "long-workflow"}, semantic_initial=semantic,
    )
    decision = result["decisions"][0]
    assert "short" in decision["selections"][SEMANTIC_VARIANT]["selected"]
    assert "long" not in decision["selections"][SEMANTIC_VARIANT]["selected"]
    assert [value[0] for value in calls] == [texts[entry["key"]["root_workflow_id"]]
                                            for entry in observation["candidates"]]
    assert result["counts"]["semantic_initial_candidate_observations"] == len(calls)
    if protected:
        assert "protected" in decision["selections"][SEMANTIC_VARIANT]["selected"]


@pytest.mark.parametrize("initial", (False, True))
def test_semantic_variant_cannot_replace_progress_or_unsupported_forecasts(initial):
    row, observation, costs, serving = choice_fixture()
    if initial:
        for entry in observation["candidates"]:
            entry["features"] = observed(0.)
            probabilities = serving.predict(serving.task_projection("ab"), entry["features"])
            entry["predictions"]["tree"].update(
                probabilities=probabilities, effective_weight=.5, support="outside_training_support",
            )

    def forbidden(*_args):
        raise AssertionError("semantic initial head must not replace progress or unsupported forecasts")

    result = inspect(
        [row], [observation], costs, {"identity": "test-cost"}, serving, contract(),
        {"long-workflow": "ab", "short-workflow": "ab"}, set(), semantic_initial=forbidden,
    )
    decision = result["decisions"][0]
    assert decision["selections"][SEMANTIC_VARIANT] == decision["selections"]["tree_continuous_distribution"]
    assert all(value["semantic_initial_forecast"] is None for value in decision["candidate_details"].values())


def semantic_study(tmp_path):
    from tests.test_workflow_qwen_initial_work import contract as qwen_contract

    encoder = qwen_contract()
    dimension = encoder["embedding_dimension"] + len(encoder["task_features"])
    expert = {
        "center": [0.] * dimension, "scale": [1.] * dimension,
        "coefficient": [math.log1p(230000.) - math.log1p(24000.)] + [0.] * (dimension - 1),
        "bias": math.log1p(24000.), "log_total_sigma": 0.,
    }
    prior = {"kind": "semantic_class_conditional_log_work_v1",
             "class_conditional_priors": [expert] * 3}
    raw = {
        "kind": "offline_qwen_semantic_initial_work_v1", "family": "class_conditional",
        "prior": prior, "encoder": encoder, "reference": contract(),
        "metadata": {"decision_eligible": False},
    }
    inputs = {"/retained/model.json": "a" * 64}
    values = {
        "model.json": raw,
        "task_embeddings.json": {
            "encoder": encoder, "explicit_task_text_only": True, "future_work_labels_in_embeddings": False,
            "vectors": {"short": [0.] * dimension, "long": [1.] + [0.] * (dimension - 1)},
        },
        "previous59/prior.json": prior,
    }
    for name, value in values.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    evaluation = {
        "family": "class_conditional", "selected": "previous59",
        "candidate_selection_used_test_labels": False,
        "model_sha256": sha256_file(tmp_path / "model.json"),
        "task_embeddings_sha256": sha256_file(tmp_path / "task_embeddings.json"),
        "inputs_sha256": inputs,
        "calibration": {
            "retained": {"pair_ranking_accuracy": .6, "mean_squared_continuous_utility_error": .1},
            "previous59": {"pair_ranking_accuracy": .8, "mean_squared_continuous_utility_error": .05},
        },
    }
    (tmp_path / "evaluation.json").write_text(json.dumps(evaluation))
    return inputs, evaluation


def test_semantic_study_loader_binds_calibration_selected_model_and_exact_task_cache(tmp_path):
    inputs, _evaluation = semantic_study(tmp_path)
    forecast, pins = load_semantic_initial_study(tmp_path, contract(), inputs)
    short, long = (forecast(text, (0., 0., 1.)) for text in ("short", "long"))
    assert short["distribution_effective_weight"] > long["distribution_effective_weight"]
    assert math.expm1(short["log1p_remaining_work"]) == pytest.approx(24000.)
    assert math.expm1(long["log1p_remaining_work"]) == pytest.approx(230000.)
    assert pins[str(tmp_path / "model.json")] == sha256_file(tmp_path / "model.json")
    with pytest.raises(ValueError, match="exact observed task"):
        forecast("unknown", (0., 0., 1.))
    with pytest.raises(ValueError, match="matched inputs"):
        load_semantic_initial_study(tmp_path, contract(), {"/retained/model.json": "b" * 64})


@pytest.mark.parametrize("changed", ("model", "cache", "selection", "reference", "prior"))
def test_semantic_study_loader_rejects_changed_calibration_artifact_or_reference(tmp_path, changed):
    inputs, evaluation = semantic_study(tmp_path)
    reference = contract()
    if changed in ("model", "cache", "prior"):
        name = {"model": "model.json", "cache": "task_embeddings.json",
                "prior": "previous59/prior.json"}[changed]
        (tmp_path / name).write_text("{}")
    elif changed == "selection":
        evaluation["selected"] = "expanded77"
        (tmp_path / "evaluation.json").write_text(json.dumps(evaluation))
    else:
        reference["reference_work"] *= 2
    with pytest.raises(ValueError):
        load_semantic_initial_study(tmp_path, reference, inputs)
