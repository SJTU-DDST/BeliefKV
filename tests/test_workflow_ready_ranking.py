from copy import deepcopy

import pytest

from beliefkv.predictor.workflow_work import (
    OBSERVED_RATIOS, RATIO_FEATURES, R5_FEATURES, R5_SCHEMA, WorkflowWorkModel, work_numeric_features,
)
from beliefkv.predictor.workflow_work_training import (
    READY_MIXED_PAIRS, continuous_work_targets, work_ranking_pairs,
)
from scripts.train_workflow_ready_ranking import pair_quality
from scripts.train_workflow_work import fit
from scripts.rescore_workflow_predictions import rescore
from tests.test_workflow_work_distribution import distribution_raw
from tests.test_workflow_work_distribution_training import row


def samples():
    values = []
    for workflow, total in (("a", 100.), ("b", 300.), ("c", 600.)):
        for snapshot, fraction in enumerate((0., 0., .2, .5, .9)):
            features = [0.] * len(R5_FEATURES)
            features[R5_FEATURES.index("ready")] = snapshot != 0
            features[R5_FEATURES.index("completed_calls")] = max(0, snapshot - 1)
            values.append({
                **row(workflow, snapshot), "features": features,
                "event": "llm_submit" if snapshot == 1 else "other",
                "total_work": total, "attained_work": total * fraction,
                "remaining_work": total * (1. - fraction),
            })
    return values


def test_mixed_pairing_adds_first_request_and_cross_progress_ready_states_without_mutating_inputs():
    rows = samples()
    original = deepcopy(rows)
    pairs = work_ranking_pairs(rows, pairs_per_phase=64, mode=READY_MIXED_PAIRS)
    assert rows == original
    assert pairs == work_ranking_pairs(rows, pairs_per_phase=64, mode=READY_MIXED_PAIRS)
    assert any(rows[a]["snapshot"] == rows[b]["snapshot"] == 1 for a, b, _d, _w in pairs)
    assert any(abs(rows[a]["attained_work"] / rows[a]["total_work"]
                   - rows[b]["attained_work"] / rows[b]["total_work"]) > .5
               for a, b, _d, _w in pairs)
    assert sum(weight for _a, _b, _d, weight in pairs) == pytest.approx(6.)
    for a, b, direction, weight in pairs:
        assert rows[a]["workflow"] != rows[b]["workflow"]
        assert direction * (rows[b]["remaining_work"] - rows[a]["remaining_work"]) > 0
        assert weight > 0


def test_mixed_pairing_rejects_incompatible_features_and_unknown_modes():
    with pytest.raises(ValueError, match="R5"):
        work_ranking_pairs([{**item, "features": [0.]} for item in samples()], mode=READY_MIXED_PAIRS)
    with pytest.raises(ValueError, match="unknown"):
        work_ranking_pairs(samples(), mode="future_information")


def test_ready_pair_evaluation_preserves_neutral_fallback_and_workflow_pair_population():
    import json

    raw = distribution_raw()
    raw["metadata"]["decision_eligible"] = False
    result = pair_quality(WorkflowWorkModel(raw), samples())
    assert result["accuracy"] == .5
    assert result["workflow_pairs"] == 3
    assert result["both_supported_pairs"] == 0
    assert result["effective_ties"] == result["snapshot_pairs"]
    json.dumps(result, allow_nan=False)
    first = pair_quality(WorkflowWorkModel(raw), samples(), first_request=True)
    assert first["snapshot_pairs"] == first["workflow_pairs"] == 3
    assert pair_quality(WorkflowWorkModel(raw), samples()[:5])["accuracy"] is None


def test_sealed_competition_rescoring_preserves_inputs_and_refuses_missing_dynamic_text():
    rows = [{
        "monotonic_s": 1.,
        "candidates": [{"key": {"root_workflow_id": "a"}, "features": [0.] * len(R5_FEATURES),
                        "observed_remaining_model_work": 42., "protected_JOIN": False}],
    }]
    original = deepcopy(rows)
    result = rescore(rows, {"current": WorkflowWorkModel(distribution_raw())}, {"a": "ab"})
    assert rows == original
    candidate = result[0]["candidates"][0]
    assert candidate["features"] == rows[0]["candidates"][0]["features"]
    assert candidate["observed_remaining_model_work"] == 42.
    assert sum(candidate["predictions"]["current"]["probabilities"]) == pytest.approx(1.)
    raw = distribution_raw()
    raw["observed_delegation_inputs"] = True
    with pytest.raises(ValueError, match="delegation text"):
        rescore(rows, {"unsupported": WorkflowWorkModel(raw)}, {"a": "ab"})


def test_new_training_objectives_export_the_same_small_probability_model():
    training = samples()
    calibration = [{
        **value, **{name: item for name, item in row("cal-" + value["workflow"]).items()
                    if name in ("workflow", "logical_workflow_id", "project", "task_template",
                                "source_capture", "source_workflow_sha256")},
    } for value in samples()]
    raw = fit(
        training, calibration, epochs=2, split_mode="workflow",
        hidden_width=4, vocabulary_size=4, feature_schema=R5_SCHEMA,
        work_thresholds=(200., 400.), ranking_loss_weight=.1,
        ranking_pair_mode=READY_MIXED_PAIRS, ordinal_loss_weight=.5,
    )
    model = WorkflowWorkModel(raw)
    assert model.arrays["output_weight"].shape == (4, 3)
    assert raw["metadata"]["ranking_training"]["pair_mode"] == READY_MIXED_PAIRS
    assert raw["metadata"]["ordinal_training"]["new_online_features"] is False
    assert sum(model.predict(model.task_projection("ab"), [0.] * len(R5_FEATURES))) == pytest.approx(1.)


def test_continuous_targets_conserve_labels_preserve_inputs_and_handle_finished_work():
    import numpy as np

    rows = samples()
    original = deepcopy(rows)
    weights = np.full(len(rows), 1. / len(rows))
    target, normalization = continuous_work_targets(rows, weights)
    assert rows == original
    assert np.average(target, weights=weights) == pytest.approx(0., abs=1e-12)
    assert np.exp(
        target * normalization["log_scale"] + normalization["log_mean"]
    ) - 1. == pytest.approx([item["remaining_work"] for item in rows])
    finished = [{**rows[0], "attained_work": 100., "remaining_work": 0.}]
    assert np.isfinite(continuous_work_targets(finished, [1.])[0]).all()
    for invalid in (
        [{**rows[0], "remaining_work": float("nan")}],
        [{**rows[0], "remaining_work": -1.}],
        [{**rows[0], "remaining_work": 99.}],
    ):
        with pytest.raises(ValueError, match="conserved"):
            continuous_work_targets(invalid, [1.])


def test_continuous_supervision_leaves_runtime_shapes_and_legacy_zero_weight_unchanged():
    import numpy as np
    from scripts.train_workflow_work import probabilities

    training = samples()
    calibration = [{
        **value, **{name: item for name, item in row("cal-" + value["workflow"]).items()
                    if name in ("workflow", "logical_workflow_id", "project", "task_template",
                                "source_capture", "source_workflow_sha256")},
    } for value in samples()]
    options = {
        "epochs": 3, "split_mode": "workflow", "hidden_width": 4,
        "vocabulary_size": 4, "feature_schema": R5_SCHEMA, "work_thresholds": (200., 400.),
    }
    original = fit(training, calibration, **options)
    zero = fit(training, calibration, **options, remaining_work_loss_weight=0.)
    assert original == zero
    augmented = fit(training, calibration, **options, remaining_work_loss_weight=.3)
    supervision = augmented["metadata"]["continuous_work_supervision"]
    assert supervision["offline_labels_only"] and not supervision["auxiliary_head_exported"]
    assert supervision["online_model_dimensions_unchanged"]
    first, second = WorkflowWorkModel(original), WorkflowWorkModel(augmented)
    assert {key: value.shape for key, value in first.arrays.items()} == {
        key: value.shape for key, value in second.arrays.items()
    }
    actual = probabilities(second, calibration)
    assert np.isfinite(actual).all() and np.allclose(actual.sum(1), 1.)
    assert not np.array_equal(probabilities(first, calibration), actual)


def test_ratio_features_use_observed_counters_and_preserve_original_input_and_legacy_transform():
    import numpy as np

    values = np.zeros(len(R5_FEATURES))
    for name, value in {
        "input_tokens": 200., "output_tokens": 100., "completed_calls": 4.,
        "children": 4., "returned_children": 2., "joins": 2., "completed_joins": 1.,
        "child_input_tokens": 100., "child_output_tokens": 50.,
    }.items():
        values[R5_FEATURES.index(name)] = value
    before = values.copy()
    actual = work_numeric_features(values, OBSERVED_RATIOS)
    assert np.array_equal(work_numeric_features(values), before)
    assert work_numeric_features(values.astype("float32")).dtype == np.dtype("float32")
    assert np.array_equal(values, before)
    assert actual[:len(values)].tolist() == values.tolist()
    ratios = dict(zip(RATIO_FEATURES, actual[len(values):]))
    assert ratios["attained_model_work"] == 102.
    assert ratios["output_per_call"] == 25.
    assert ratios["completed_join_fraction"] == ratios["returned_child_fraction"] == .5
    assert ratios["child_model_work_fraction"] == .5
    assert np.isfinite(work_numeric_features(np.zeros(len(values)), OBSERVED_RATIOS)).all()
    with pytest.raises(ValueError, match="unknown"):
        work_numeric_features(values, "future_remaining_work")


def test_ratio_training_and_portable_inference_use_identical_transformed_dimensions():
    import numpy as np
    from scripts.train_workflow_work import probabilities

    training = samples()
    calibration = [{
        **value, **{name: item for name, item in row("cal-" + value["workflow"]).items()
                    if name in ("workflow", "logical_workflow_id", "project", "task_template",
                                "source_capture", "source_workflow_sha256")},
    } for value in samples()]
    raw = fit(
        training, calibration, epochs=2, split_mode="workflow",
        hidden_width=4, vocabulary_size=4, feature_schema=R5_SCHEMA,
        work_thresholds=(200., 400.), numeric_feature_transform=OBSERVED_RATIOS,
    )
    model = WorkflowWorkModel(raw)
    assert model.arrays["dynamic_weight"].shape == (len(R5_FEATURES) + len(RATIO_FEATURES), 4)
    actual = np.asarray([model.predict(model.task_projection(value["text"]), value["features"])
                         for value in calibration])
    assert np.allclose(actual, probabilities(model, calibration), atol=1e-7)
    with pytest.raises(ValueError, match="transform"):
        WorkflowWorkModel({**raw, "numeric_feature_transform": "future_label"})


@pytest.mark.parametrize("blend", (.25, .5, 1.))
def test_initial_head_is_exactly_inactive_after_observed_work_or_children_and_falls_back_when_unsupported(blend):
    import numpy as np

    base = distribution_raw()
    child = deepcopy(base)
    child["output_bias"] = [2., 0., -2.]
    base["initial_state_head"] = {
        "kind": "before_observed_work_and_children_v1", "blend": blend, "model": child,
    }
    model, progress = WorkflowWorkModel(base), WorkflowWorkModel(distribution_raw())
    features = np.zeros(len(R5_FEATURES))
    projection = model.task_projection("ab")
    initial = WorkflowWorkModel(child).predict(
        WorkflowWorkModel(child).task_projection("ab"), features,
    )
    expected = tuple(
        (1. - blend) * base_probability + blend * initial_probability
        for base_probability, initial_probability in zip(
            progress.predict(progress.task_projection("ab"), features), initial,
        )
    )
    assert model.predict(projection, features) == expected
    for name, amount in (("input_tokens", 1.), ("output_tokens", 128.), ("children", 1.)):
        values = features.copy()
        values[R5_FEATURES.index(name)] = amount
        assert model.predict(projection, values) == progress.predict(progress.task_projection("ab"), values)
    unsupported = deepcopy(base)
    unsupported["initial_state_head"]["model"]["distribution_contract"]["minimum_task_coverage"] = 1.
    fallback = WorkflowWorkModel(unsupported)
    text = "ab unrecognized words"
    assert fallback.predict(fallback.task_projection(text), features) == progress.predict(
        progress.task_projection(text), features,
    )


@pytest.mark.parametrize("change", ("nested", "thresholds", "future", "blend", "support"))
def test_initial_head_rejects_recursive_or_incompatible_prediction_artifacts(change):
    raw = distribution_raw()
    child = deepcopy(raw)
    raw["initial_state_head"] = {
        "kind": "before_observed_work_and_children_v1", "blend": 1., "model": child,
    }
    if change == "nested":
        child["initial_state_head"] = deepcopy(raw["initial_state_head"])
    elif change == "thresholds":
        child["work_thresholds"] = [1., 2.]
    elif change == "future":
        child["workflow_graphs"] = {}
    elif change == "blend":
        raw["initial_state_head"]["blend"] = float("nan")
    else:
        child["metadata"]["decision_eligible"] = False
    with pytest.raises(ValueError):
        WorkflowWorkModel(raw)


@pytest.mark.parametrize("settings", (
    {"ordinal_loss_weight": -1.}, {"ordinal_loss_weight": float("nan")},
    {"ranking_pair_mode": "unknown"}, {"ranking_pair_mode": READY_MIXED_PAIRS},
    {"remaining_work_loss_weight": -1.}, {"remaining_work_loss_weight": float("nan")},
    {"remaining_work_loss_weight": True}, {"remaining_work_loss_weight": 3.},
))
def test_invalid_training_objectives_fail_before_training(settings):
    with pytest.raises(ValueError, match="regularization"):
        fit(samples(), [row("cal")], epochs=1, feature_schema=R5_SCHEMA, **settings)
