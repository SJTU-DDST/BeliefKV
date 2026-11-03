"""Small regularized probability corrections without new online observations."""

import math

from beliefkv.predictor.workflow_work import before_observed_work


KIND = "regularized_log_probability_temperature_and_class_bias_v1"


def correct_probabilities(probabilities, parameters):
    import numpy as np

    values = np.asarray(probabilities, dtype=float)
    parameters = np.asarray(parameters, dtype=float)
    if (
        values.ndim not in (1, 2) or values.shape[-1] != 3
        or parameters.shape != (3,) or not np.isfinite(parameters).all()
        or not np.isfinite(values).all() or (values < 0).any()
        or not np.allclose(values.sum(axis=-1), 1., atol=1e-6, rtol=0.)
        or abs(parameters[0]) > math.log(4.) + 1e-9 or (np.abs(parameters[1:]) > 3.).any()
    ):
        raise ValueError("invalid calibrated workflow probability values or parameters")
    if not parameters.any():
        return values.copy()
    logits = np.log(np.maximum(1e-12, values)) * math.exp(parameters[0])
    logits = logits + np.asarray((parameters[1], 0., parameters[2]))
    logits -= logits.max(axis=-1, keepdims=True)
    result = np.exp(logits)
    return result / result.sum(axis=-1, keepdims=True)


def fit_probability_correction(probabilities, targets, weights, *, regularization):
    """Inputs and labels must already be confined to the chosen calibration fold."""
    import numpy as np
    from scipy.optimize import minimize
    from scipy.special import logsumexp

    probabilities = np.asarray(probabilities, dtype=float)
    correct_probabilities(probabilities, (0., 0., 0.))
    targets, weights = np.asarray(targets), np.asarray(weights, dtype=float)
    if (
        targets.shape != (len(probabilities),) or targets.dtype.kind not in "iu"
        or (targets < 0).any() or (targets > 2).any()
        or weights.shape != targets.shape or not np.isfinite(weights).all()
        or (weights <= 0).any() or not len(targets)
        or type(regularization) not in (int, float)
        or not math.isfinite(regularization) or regularization <= 0
    ):
        raise ValueError("invalid whole-workflow calibration objective")
    weights = weights / weights.sum()
    logp = np.log(np.maximum(probabilities, 1e-12))
    truth = np.eye(3)[targets]

    def objective(parameters):
        inverse_temperature = math.exp(parameters[0])
        logits = logp * inverse_temperature + np.asarray((parameters[1], 0., parameters[2]))
        normalizer = logsumexp(logits, axis=1, keepdims=True)
        loss = float((weights * (normalizer[:, 0] - logits[np.arange(len(targets)), targets])).sum())
        errors = weights[:, None] * (np.exp(logits - normalizer) - truth)
        gradient = np.asarray((
            (errors * logp).sum() * inverse_temperature,
            errors[:, 0].sum(), errors[:, 2].sum(),
        ))
        return (
            loss + .5 * regularization * float(parameters @ parameters),
            gradient + regularization * parameters,
        )

    result = minimize(
        objective, np.zeros(3), jac=True, method="L-BFGS-B",
        bounds=((-math.log(4.), math.log(4.)), (-3., 3.), (-3., 3.)),
        options={"maxiter": 100, "ftol": 1e-12},
    )
    if not result.success or not np.isfinite(result.x).all():
        raise ValueError("workflow probability calibration did not converge: " + result.message)
    return result.x.tolist()


class CalibratedWorkCandidate:
    def __init__(self, base, parameters, *, progress_only):
        if type(progress_only) is not bool:
            raise ValueError("workflow calibration needs an explicit observed-progress condition")
        correct_probabilities((1. / 3.,) * 3, parameters)
        self.base, self.parameters, self.progress_only = base, tuple(parameters), progress_only

    def __getattr__(self, name):
        return getattr(self.base, name)

    def predict(self, projection, features):
        original = self.base.predict(projection, features)
        if self.progress_only and before_observed_work(features):
            return original
        return tuple(float(value) for value in correct_probabilities(original, self.parameters))
