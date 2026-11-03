"""Offline continuous readouts of a frozen, observed-progress workflow representation."""

import hashlib
import json
import math

from beliefkv.predictor.workflow_work import (
    InitialWorkProjection, R5_FEATURES, R5_SCHEMA, before_observed_work,
    work_numeric_features, work_prediction_features,
)


KIND = "offline_frozen_workflow_readout_v1"
TRUNK_FIELDS = (
    "schema", "features", "vocabulary", "idf", "center", "scale",
    "static_weight", "dynamic_weight", "bias", "hidden_weight", "hidden_bias",
    "numeric_feature_transform", "task_only_before_observed_work", "task_text_encoding",
    "observed_delegation_inputs", "task_encoder", "total_work_prior",
)


def representation_fingerprint(model):
    return hashlib.sha256(json.dumps(
        {name: model.raw.get(name) for name in TRUNK_FIELDS}, sort_keys=True,
    ).encode()).hexdigest()


def representation(model, projection, observed):
    import numpy as np

    if (
        model.schema != R5_SCHEMA or model.task_encoder is not None or model.work_prior is not None
        or model.ordered_cutpoints is not None or model.observed_delegations
    ):
        raise ValueError("frozen readouts require the declared plain R5 progress representation")
    if isinstance(projection, InitialWorkProjection):
        projection = projection.progress_projection
    observed = np.asarray(observed, dtype=float)
    if (
        observed.shape != (len(R5_FEATURES),) or not np.isfinite(observed).all()
        or (observed < 0).any()
    ):
        raise ValueError("frozen readout needs finite nonnegative observed R5 features")
    if model.task_only_before_work:
        observed = work_prediction_features(observed, task_only_before_work=True)
    arrays = model.arrays
    numeric = (np.log1p(work_numeric_features(observed, model.numeric_transform))
               - arrays["center"]) / arrays["scale"]
    hidden = np.maximum(0., projection + np.einsum(
        "i,ij->j", numeric, arrays["dynamic_weight"], optimize=False,
    ) + arrays["bias"])
    return np.maximum(0., np.einsum(
        "i,ij->j", hidden, arrays["hidden_weight"], optimize=False,
    ) + arrays["hidden_bias"])


def ridge_fit(x, y, weights, alpha):
    """Primal ridge keeps fitting bounded by representation width, not snapshot count."""
    import numpy as np

    x, y, weights = (np.asarray(value, dtype=float) for value in (x, y, weights))
    if (
        x.ndim != 2 or y.ndim != 2 or y.shape[0] != len(x)
        or weights.shape != (len(x),) or not np.isfinite(x).all()
        or not np.isfinite(y).all() or not np.isfinite(weights).all()
        or (weights <= 0).any() or not math.isfinite(alpha) or alpha <= 0
    ):
        raise ValueError("continuous readout requires finite positive training mass")
    weights = weights / weights.sum()
    center = np.average(x, axis=0, weights=weights)
    scale = np.maximum(.05, np.sqrt(np.average((x - center) ** 2, axis=0, weights=weights)))
    design = (x - center) / scale
    bias = np.average(y, axis=0, weights=weights)
    coefficient = np.linalg.solve(
        design.T @ (weights[:, None] * design) + alpha * np.eye(x.shape[1]),
        design.T @ (weights[:, None] * (y - bias)),
    )
    return {"center": center.tolist(), "scale": scale.tolist(),
            "coefficient": coefficient.tolist(), "bias": bias.tolist()}


def ridge_predict(raw, x):
    import numpy as np

    return ((np.asarray(x) - raw["center"]) / raw["scale"]) @ raw["coefficient"] + raw["bias"]


def log_work_proxy(head, latent, observed):
    import numpy as np

    predictions = ridge_predict(head["ridge"], latent)
    if head["target"] == "work":
        return predictions[:, 0]
    if head["target"] != "branch_calls":
        raise ValueError("unknown frozen readout target")
    counts = np.expm1(np.clip(predictions, 0., 20.))
    observed = np.asarray(observed, dtype=float)
    costs = []
    for owner in ("root", "child"):
        completed = observed[:, R5_FEATURES.index(owner + "_completed_calls")]
        work = .01 * observed[:, R5_FEATURES.index(owner + "_input_tokens")]
        work += observed[:, R5_FEATURES.index(owner + "_output_tokens")]
        strength = head["cost_prior_strength"]
        costs.append((work + strength * head["per_call_prior"][owner]) / (completed + strength))
    return np.log1p((counts * np.column_stack(costs)).sum(1))


class FrozenReadoutCandidate:
    """CPU study adapter; the serving model does not recognize this artifact kind."""

    def __init__(self, base, head, blend):
        import numpy as np

        width = base.arrays["hidden_weight"].shape[1]
        channels = 1 if head.get("target") == "work" else 2
        ridge = head.get("ridge", {})
        if (
            head.get("kind") != KIND or head.get("target") not in ("work", "branch_calls")
            or head.get("representation_sha256") != representation_fingerprint(base)
            or not math.isfinite(blend) or not 0 < blend <= 1
            or not isinstance(ridge, dict)
            or ridge.keys() != {"center", "scale", "coefficient", "bias"}
            or np.asarray(ridge.get("coefficient")).shape != (width, channels)
            or np.asarray(ridge.get("center")).shape != (width,)
            or np.asarray(ridge.get("scale")).shape != (width,)
            or np.asarray(ridge.get("bias")).shape != (channels,)
            or any(not np.isfinite(value).all() for value in map(np.asarray, ridge.values()))
            or (np.asarray(ridge["scale"]) <= 0).any()
            or not math.isfinite(head["calibration_offset"])
            or not math.isfinite(head["calibration_sigma"]) or head["calibration_sigma"] <= 0
        ):
            raise ValueError("invalid or mismatched offline continuous workflow readout")
        if head["target"] == "branch_calls" and (
            not math.isfinite(head["cost_prior_strength"]) or head["cost_prior_strength"] <= 0
            or any(not math.isfinite(head["per_call_prior"][owner]) or head["per_call_prior"][owner] <= 0
                   for owner in ("root", "child"))
        ):
            raise ValueError("call-cost priors require finite positive training support")
        self.base, self.head, self.blend = base, head, blend
        self.metadata = {**base.metadata, "calibration_class_workflows": head["calibration_class_workflows"]}

    def __getattr__(self, name):
        return getattr(self.base, name)

    def predict(self, projection, observed):
        original = self.base.predict(projection, observed)
        if before_observed_work(observed):
            return original
        latent = representation(self.base, projection, observed)
        mean = float(log_work_proxy(self.head, [latent], [observed])[0]) + self.head["calibration_offset"]
        scale = self.head["calibration_sigma"]
        cumulative = [
            .5 * (1. + math.erf((math.log1p(cut) - mean) / scale / math.sqrt(2.)))
            for cut in self.thresholds
        ]
        probabilities = (cumulative[0], cumulative[1] - cumulative[0], 1. - cumulative[1])
        return tuple(float((1. - self.blend) * old + self.blend * new)
                     for old, new in zip(original, probabilities))
