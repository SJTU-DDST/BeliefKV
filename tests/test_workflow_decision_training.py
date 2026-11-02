from copy import deepcopy

import pytest

from beliefkv.predictor.workflow_work import R5_FEATURES
from beliefkv.predictor.workflow_work_training import (
    DECISION_OBSERVATION, decision_observation_weights,
)
from scripts.train_workflow_decision_states import augment_training, require_causal_scope
from tests.test_workflow_ready_ranking import samples


def fixture():
    rows = samples()
    scope = {
        "protected_JOIN_identification": "zero_global_rank_not_reference_category_v2",
        "client_published_events_and_observed_native_progress_only_for_features": True,
        "protected_JOIN_pairs_excluded_from_predictor_rank_quality": True,
        "future_original_work_and_logged_paths_are_labels_only": True,
    }
    observed = [0.] * len(R5_FEATURES)
    observed[1] = 20.
    audit = {
        "scope": scope,
        "decisions": [{
            "monotonic_s": 1., "candidates": [{
                "key": {"root_workflow_id": rows[0]["logical_workflow_id"]},
                "selector_metadata_valid": True, "protected_JOIN": False,
                "features": observed, "observed_remaining_model_work": 80.,
            }],
        }],
    }
    return rows, audit


def test_observed_states_only_augment_known_training_identities_and_keep_labels_out_of_features():
    rows, audit = fixture()
    original_rows, original_audit = deepcopy(rows), deepcopy(audit)
    candidate = deepcopy(audit["decisions"][0]["candidates"][0])
    candidate["key"]["root_workflow_id"] = "held-out"
    candidate["features"] = [float("nan")]
    candidate["observed_remaining_model_work"] = "future-test-label"
    audit["decisions"][0]["candidates"].append(candidate)
    augmented, report = augment_training(rows, audit)
    assert rows == original_rows
    assert augmented[:len(rows)] == original_rows
    assert report["added_states"] == 1
    assert report["held_out_observations_used_for_training"] is False
    value = augmented[-1]
    assert value["logical_workflow_id"] == rows[0]["logical_workflow_id"]
    assert value["features"] == original_audit["decisions"][0]["candidates"][0]["features"]
    assert value["snapshot"] > max(row["snapshot"] for row in rows)
    assert value["attained_work"] + value["remaining_work"] == value["total_work"]
    assert value["text"] == rows[0]["text"]


def test_distinct_states_are_bounded_and_repeated_boundaries_do_not_add_training_mass():
    rows, audit = fixture()
    candidate = audit["decisions"][0]["candidates"][0]
    for count in range(1, 20):
        value = deepcopy(candidate)
        value["features"][1] = float(count)
        value["observed_remaining_model_work"] = 100. - count
        audit["decisions"].append({"monotonic_s": count + 1., "candidates": [value]})
    audit["decisions"] += deepcopy(audit["decisions"])
    augmented, report = augment_training(rows, audit, maximum_states=4)
    assert report["added_states"] == 4
    assert len({tuple(value["features"]) for value in augmented[len(rows):]}) == 4
    assert all(value["training_observation_kind"] == DECISION_OBSERVATION
               for value in augmented[len(rows):])


def test_augmentation_rejects_wrong_work_population_and_ignores_join_protection():
    rows, audit = fixture()
    bad = deepcopy(audit)
    bad["scope"]["protected_JOIN_identification"] = "category_zero"
    with pytest.raises(ValueError, match="corrected"):
        augment_training(rows, bad)
    bad = deepcopy(audit)
    bad["decisions"][0]["candidates"][0]["observed_remaining_model_work"] = 79.
    with pytest.raises(ValueError, match="complete original work"):
        augment_training(rows, bad)
    audit["decisions"][0]["candidates"][0]["protected_JOIN"] = True
    augmented, report = augment_training(rows, audit)
    assert augmented == rows and report["added_states"] == 0
    del audit["scope"]["future_original_work_and_logged_paths_are_labels_only"]
    with pytest.raises(ValueError, match="corrected"):
        require_causal_scope(audit)


def test_decision_weights_preserve_whole_workflow_mass_and_unchanged_groups():
    import numpy as np

    rows, audit = fixture()
    values, _ = augment_training(rows, audit)
    base = np.ones(len(values))
    for workflow in {row["workflow"] for row in values}:
        positions = [index for index, row in enumerate(values) if row["workflow"] == workflow]
        base[positions] = 1. / 3. / len(positions)
    original = base.copy()
    weighted = decision_observation_weights(values, base, .25)
    assert np.array_equal(base, original)
    assert weighted.sum() == pytest.approx(1.)
    for workflow in {row["workflow"] for row in values}:
        positions = [index for index, row in enumerate(values) if row["workflow"] == workflow]
        assert weighted[positions].sum() == pytest.approx(1. / 3.)
        ordinary = [index for index in positions if "training_observation_kind" not in values[index]]
        live = [index for index in positions if index not in ordinary]
        if live:
            assert weighted[live].sum() == pytest.approx(.25 / 3.)
            assert weighted[ordinary].sum() == pytest.approx(.75 / 3.)
        else:
            assert np.array_equal(weighted[positions], original[positions])
    with pytest.raises(ValueError, match="no supported"):
        decision_observation_weights(rows, np.ones(len(rows)), .25)
    with pytest.raises(ValueError, match="complete original"):
        decision_observation_weights([values[-1]], [1.], .25)


@pytest.mark.parametrize("mass", (-1., 0., .8, float("nan"), True))
def test_invalid_decision_weights_are_rejected(mass):
    rows, _ = fixture()
    with pytest.raises(ValueError):
        decision_observation_weights(rows, [1.] * len(rows), mass)
