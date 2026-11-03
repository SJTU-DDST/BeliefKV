from copy import deepcopy

import numpy as np

from beliefkv.predictor.workflow_work import (
    R5_FEATURES, SERVICE_PROGRESS, SCHEDULER_STATE_FEATURES, WorkflowWorkModel, work_numeric_features,
)
from tests.test_workflow_work_distribution import distribution_raw


def test_scheduler_state_mask_copies_observations_and_retains_attained_work_and_application_dependencies():
    original = np.arange(len(R5_FEATURES), dtype=float)
    saved = original.copy()
    transformed = work_numeric_features(original, SERVICE_PROGRESS)
    assert np.array_equal(original, saved)
    for index, name in enumerate(R5_FEATURES):
        assert transformed[index] == (0. if name in SCHEDULER_STATE_FEATURES else original[index])


def test_same_served_work_and_dependencies_predict_identically_across_scheduler_states():
    raw = distribution_raw()
    raw["numeric_feature_transform"] = SERVICE_PROGRESS
    # Make masked channels influential so this checks the actual inference boundary.
    for name in SCHEDULER_STATE_FEATURES:
        raw["dynamic_weight"][R5_FEATURES.index(name)] = [10.] * len(raw["bias"])
    raw["output_weight"] = [[1., 0., -1.]]
    raw["output_bias"] = [0., 0., 0.]
    model = WorkflowWorkModel(raw)
    projection = model.task_projection("ab")
    original = [0.] * len(R5_FEATURES)
    original[1] = 128.
    changed = list(original)
    for name in SCHEDULER_STATE_FEATURES:
        changed[R5_FEATURES.index(name)] = 1000.
    assert model.predict(projection, original) == model.predict(projection, changed)
    legacy_raw = deepcopy(raw)
    legacy_raw.pop("numeric_feature_transform")
    legacy = WorkflowWorkModel(legacy_raw)
    assert legacy.numeric_transform is None
    assert legacy.predict(projection, original) != legacy.predict(projection, changed)
