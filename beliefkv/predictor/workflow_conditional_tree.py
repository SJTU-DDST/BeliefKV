"""Bounded conditional work trees sharing the serving model's frozen representation."""

import math

from beliefkv.predictor.workflow_progress_tree import FEATURES, KIND as TREE_KIND, ProgressTreeHead, features
from beliefkv.predictor.workflow_work import R5_SCHEMA, before_observed_work
from beliefkv.predictor.workflow_work_readout import representation_fingerprint


KIND = "conditional_frozen_progress_work_trees_v1"


class ConditionalProgressTreeHead:
    def __init__(self, raw, base):
        width = base.arrays["hidden_weight"].shape[1]
        names = list(FEATURES) + ["frozen_hidden_" + str(index) for index in range(width)]
        if (
            not isinstance(raw, dict) or raw.keys() != {
                "kind", "features", "representation_sha256", "trees", "maximum_depth",
                "blend", "calibration_offset", "calibration_sigma",
            }
            or raw["kind"] != KIND or raw["features"] != names
            or raw["representation_sha256"] != representation_fingerprint(base)
            or base.schema != R5_SCHEMA or base.task_encoder is not None
            or base.work_prior is not None or base.observed_delegations
            or base.ordered_cutpoints is not None
            or type(raw["maximum_depth"]) is not int or not 1 <= raw["maximum_depth"] <= 5
            or not isinstance(raw["trees"], list) or not 1 <= len(raw["trees"]) <= 128
            or any(type(raw[name]) not in (int, float) or not math.isfinite(raw[name])
                   for name in ("blend", "calibration_offset", "calibration_sigma"))
        ):
            raise ValueError("invalid conditional frozen workflow progress trees")
        self.blend = raw["blend"]
        self.tree = ProgressTreeHead({
            "kind": TREE_KIND, "features": names, "objective": "log_remaining_work",
            "trees": raw["trees"], "maximum_depth": raw["maximum_depth"],
            "blend": raw["blend"], "temperature": 1.,
            "regression_offset": raw["calibration_offset"], "regression_scale": raw["calibration_sigma"],
        }, feature_names=names)

    def combine(self, original, observed, task_structure, hidden, thresholds):
        import numpy as np

        if before_observed_work(observed):
            return original
        values = np.concatenate((features(observed, task_structure), hidden))
        own = self.tree.probabilities(values, thresholds)
        return tuple(float((1. - self.blend) * old + self.blend * new)
                     for old, new in zip(original, own))
