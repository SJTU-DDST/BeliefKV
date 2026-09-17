from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping, Sequence


_DEMAND_NUMERIC_NAMES = ("lc", "lg", "lc2", "lg2", "lcg")
_DEMAND_CATEGORY_FIELDS = (
    "agent_definition_id",
    "state",
    "tool_family",
    "backend_class",
    "command_class",
    "boundary_last",
)
_TIMING_NUMERIC_NAMES = (
    "log_tau",
    "log_elapsed",
    "log_total",
    "log_context",
    "log_active",
    "tau_fraction",
    "log_tau2",
    "log_elapsed2",
    "log_tau_elapsed",
)
_TIMING_CATEGORY_FIELDS = (
    "agent_definition_id",
    "tool_family",
    "backend_class",
    "command_class",
)
_CLASSIFIER_CATEGORY_FIELDS = (
    "agent_definition_id",
    "state",
    "tool_family",
    "backend_class",
    "command_class",
    "boundary_last",
)
_TIMING_CURVE_KNOTS_MS = tuple(float(2**index) for index in range(18))


def _feature_value(features: object, name: str, default: Any) -> Any:
    if isinstance(features, Mapping):
        return features.get(name, default)
    return getattr(features, name, default)


def _category(features: object, name: str) -> str:
    if name == "boundary_last":
        history = _feature_value(features, "boundary_history", ()) or ()
        return str(history[-1]) if history else "none"
    return str(_feature_value(features, name, "unknown") or "unknown")


def _demand_sparse_features(features: object) -> dict[str, float]:
    context = max(
        0.0, float(_feature_value(features, "current_sequence_tokens", 0) or 0)
    )
    generated = max(
        0.0, float(_feature_value(features, "generated_tokens", 0) or 0)
    )
    invocation_elapsed = max(
        0.0,
        float(_feature_value(features, "invocation_elapsed_ms", 0.0) or 0.0),
    )
    state_elapsed = max(
        0.0, float(_feature_value(features, "state_elapsed_ms", 0.0) or 0.0)
    )
    llm_round = max(
        0.0, float(_feature_value(features, "llm_round", 0) or 0)
    )
    child_count = max(
        0.0, float(_feature_value(features, "child_count", 0) or 0)
    )
    unfinished_child_count = max(
        0.0,
        float(_feature_value(features, "unfinished_child_count", 0) or 0),
    )
    lc = math.log1p(context) / 12.0
    lg = math.log1p(generated) / 8.0
    li = math.log1p(invocation_elapsed) / 12.0
    ls = math.log1p(state_elapsed) / 12.0
    lr = math.log1p(llm_round) / 4.0
    lch = math.log1p(child_count) / 3.0
    luch = math.log1p(unfinished_child_count) / 3.0
    output = {
        "bias": 1.0,
        "lc": lc,
        "lg": lg,
        "li": li,
        "ls": ls,
        "lr": lr,
        "lch": lch,
        "luch": luch,
        "lc2": lc * lc,
        "lg2": lg * lg,
        "li2": li * li,
        "ls2": ls * ls,
        "lcg": lc * lg,
        "lilr": li * lr,
    }
    for field in _DEMAND_CATEGORY_FIELDS:
        value = _category(features, field)
        output[f"cat:{field}:{value}"] = 1.0
    state = _category(features, "state")
    output[f"state_lc:{state}"] = lc
    output[f"state_lg:{state}"] = lg
    return output


def _timing_sparse_features(features: object, tau_ms: float) -> dict[str, float]:
    tau = max(0.0, float(tau_ms))
    elapsed = max(
        0.0, float(_feature_value(features, "elapsed_wait_ms", 0.0) or 0.0)
    )
    context = max(
        0.0, float(_feature_value(features, "current_sequence_tokens", 0) or 0)
    )
    active = max(
        0.0, float(_feature_value(features, "active_tool_count", 0) or 0)
    )
    log_tau = math.log1p(tau) / 8.0
    log_elapsed = math.log1p(elapsed) / 12.0
    output = {
        "bias": 1.0,
        "log_tau": log_tau,
        "log_elapsed": log_elapsed,
        "log_total": math.log1p(tau + elapsed) / 12.0,
        "log_context": math.log1p(context) / 12.0,
        "log_active": math.log1p(active) / 3.0,
        "tau_fraction": tau / (tau + elapsed + 1.0),
        "log_tau2": log_tau * log_tau,
        "log_elapsed2": log_elapsed * log_elapsed,
        "log_tau_elapsed": log_tau * log_elapsed,
    }
    for field in _TIMING_CATEGORY_FIELDS:
        value = _category(features, field)
        output[f"cat:{field}:{value}"] = 1.0
    for field in ("command_class", "tool_family"):
        value = _category(features, field)
        output[f"tau_slope:{field}:{value}"] = log_tau
        output[f"elapsed_slope:{field}:{value}"] = log_elapsed
    return output


def _classifier_sparse_features(features: object) -> dict[str, float]:
    context = max(
        0.0, float(_feature_value(features, "current_sequence_tokens", 0) or 0)
    )
    generated = max(
        0.0, float(_feature_value(features, "generated_tokens", 0) or 0)
    )
    elapsed = max(
        0.0, float(_feature_value(features, "elapsed_wait_ms", 0.0) or 0.0)
    )
    active = max(
        0.0, float(_feature_value(features, "active_tool_count", 0) or 0)
    )
    lc = math.log1p(context) / 12.0
    lg = math.log1p(generated) / 8.0
    le = math.log1p(elapsed) / 12.0
    output = {
        "bias": 1.0,
        "lc": lc,
        "lg": lg,
        "le": le,
        "la": math.log1p(active) / 3.0,
        "lc2": lc * lc,
        "lg2": lg * lg,
        "le2": le * le,
        "lcg": lc * lg,
    }
    categories = {
        field: _category(features, field)
        for field in _CLASSIFIER_CATEGORY_FIELDS
    }
    for field, value in categories.items():
        output[f"cat:{field}:{value}"] = 1.0
    history = tuple(_feature_value(features, "boundary_history", ()) or ())
    if len(history) >= 2:
        output[f"cat:boundary_bigram:{history[-2]}>{history[-1]}"] = 1.0
    output[
        f"cross:state_role:{categories['state']}|{categories['agent_definition_id']}"
    ] = 1.0
    output[
        f"cross:state_boundary:{categories['state']}|{categories['boundary_last']}"
    ] = 1.0
    output[
        f"cross:tool_command:{categories['tool_family']}|{categories['command_class']}"
    ] = 1.0
    return output


def _dot(
    feature_names: Sequence[str],
    coefficients: Sequence[float],
    sparse: Mapping[str, float],
) -> float:
    return sum(
        coefficient * sparse.get(name, 0.0)
        for name, coefficient in zip(feature_names, coefficients, strict=True)
    )


def _weighted_quantile(
    values: Sequence[float], weights: Sequence[float], quantile: float
) -> float:
    ordered = sorted(zip(values, weights), key=lambda item: item[0])
    total = sum(max(0.0, weight) for _value, weight in ordered)
    if not ordered or total <= 0:
        return 0.0
    threshold = min(1.0, max(0.0, quantile)) * total
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += max(0.0, weight)
        if cumulative >= threshold:
            return float(value)
    return float(ordered[-1][0])


def _isotonic_non_decreasing(values: Sequence[float]) -> tuple[float, ...]:
    blocks: list[list[float]] = []
    for value in values:
        blocks.append([min(1.0, max(0.0, float(value))), 1.0])
        while len(blocks) >= 2 and blocks[-2][0] > blocks[-1][0]:
            right = blocks.pop()
            left = blocks.pop()
            weight = left[1] + right[1]
            blocks.append(
                [
                    (left[0] * left[1] + right[0] * right[1]) / weight,
                    weight,
                ]
            )
    output: list[float] = []
    for value, weight in blocks:
        output.extend([value] * int(weight))
    return tuple(output)


@dataclass(frozen=True)
class ActionTimingCurve:
    tau_ms: tuple[float, ...]
    release_within_probability: tuple[float, ...]
    support_level: str
    training_support: float

    def __post_init__(self) -> None:
        if (
            not self.tau_ms
            or len(self.tau_ms) != len(self.release_within_probability)
            or any(value <= 0 for value in self.tau_ms)
            or tuple(sorted(self.tau_ms)) != self.tau_ms
        ):
            raise ValueError("action timing curve knots must be sorted and positive")
        if any(
            probability < 0 or probability > 1
            for probability in self.release_within_probability
        ):
            raise ValueError("action timing probabilities must be in [0, 1]")
        if any(
            left > right + 1e-12
            for left, right in zip(
                self.release_within_probability,
                self.release_within_probability[1:],
            )
        ):
            raise ValueError("release-within probability must be non-decreasing")
        if self.support_level not in {"pooled", "unavailable"}:
            raise ValueError("invalid action timing support level")
        if self.training_support < 0:
            raise ValueError("action timing support must be non-negative")

    def release_within(self, operational_tau_ms: float) -> float:
        tau = max(0.0, float(operational_tau_ms))
        if tau <= self.tau_ms[0]:
            return self.release_within_probability[0] * tau / self.tau_ms[0]
        if tau >= self.tau_ms[-1]:
            return self.release_within_probability[-1]
        log_tau = math.log1p(tau)
        for index in range(1, len(self.tau_ms)):
            if tau > self.tau_ms[index]:
                continue
            left_tau = math.log1p(self.tau_ms[index - 1])
            right_tau = math.log1p(self.tau_ms[index])
            fraction = (log_tau - left_tau) / max(right_tau - left_tau, 1e-12)
            left = self.release_within_probability[index - 1]
            right = self.release_within_probability[index]
            return left + fraction * (right - left)
        return self.release_within_probability[-1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tau_ms": list(self.tau_ms),
            "release_within_probability": list(self.release_within_probability),
            "support_level": self.support_level,
            "training_support": self.training_support,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ActionTimingCurve":
        return cls(
            tau_ms=tuple(float(value) for value in raw.get("tau_ms", ())),
            release_within_probability=tuple(
                float(value)
                for value in raw.get("release_within_probability", ())
            ),
            support_level=str(raw.get("support_level") or "unavailable"),
            training_support=float(raw.get("training_support", 0.0)),
        )


class PooledConditionalDemandModel:
    """Small pooled log-demand model with a calibrated residual distribution."""

    def __init__(self, *, regularization: float = 1e-3) -> None:
        if not math.isfinite(regularization) or regularization < 0:
            raise ValueError("demand regularization must be finite and non-negative")
        self.regularization = float(regularization)
        self.feature_names: tuple[str, ...] = ()
        self.coefficients: tuple[float, ...] = ()
        self.residual_quantiles: tuple[float, ...] = ()
        self.training_support = 0.0
        self.training_count = 0

    @property
    def fitted(self) -> bool:
        return bool(self.feature_names and self.coefficients)

    def fit(
        self,
        samples: Iterable[tuple[object, float, float]],
    ) -> dict[str, float | int]:
        values = [
            (features, float(target), float(weight))
            for features, target, weight in samples
            if target >= 0 and weight > 0
        ]
        if not values:
            return {"sample_count": 0, "episode_weight": 0.0}
        try:
            import numpy as np
        except ImportError as error:
            raise RuntimeError(
                "training pooled demand models requires the 'training' extra"
            ) from error
        sparse_rows = [_demand_sparse_features(item[0]) for item in values]
        names = sorted({name for row in sparse_rows for name in row})
        names.remove("bias")
        names.insert(0, "bias")
        index = {name: position for position, name in enumerate(names)}
        matrix = np.zeros((len(values), len(names)), dtype=np.float64)
        for row_index, sparse in enumerate(sparse_rows):
            for name, value in sparse.items():
                matrix[row_index, index[name]] = value
        target = np.log1p(
            np.asarray([item[1] for item in values], dtype=np.float64)
        )
        weights = np.asarray([item[2] for item in values], dtype=np.float64)
        weights *= len(weights) / max(float(weights.sum()), 1e-12)
        gram = matrix.T @ (weights[:, None] * matrix) / len(weights)
        right = matrix.T @ (weights * target) / len(weights)
        penalty = np.eye(len(names), dtype=np.float64) * self.regularization
        penalty[0, 0] = 0.0
        coefficients = np.linalg.solve(gram + penalty, right)
        residuals = target - matrix @ coefficients
        quantile_grid = tuple((index + 0.5) / 20.0 for index in range(20))
        self.feature_names = tuple(names)
        self.coefficients = tuple(float(value) for value in coefficients)
        self.residual_quantiles = tuple(
            _weighted_quantile(residuals, weights, quantile)
            for quantile in quantile_grid
        )
        self.training_support = float(sum(item[2] for item in values))
        self.training_count = len(values)
        prediction = matrix @ coefficients
        weighted_mae = float(
            np.sum(
                weights
                * np.abs(np.expm1(prediction) - np.expm1(target))
            )
            / np.sum(weights)
        )
        return {
            "sample_count": self.training_count,
            "episode_weight": self.training_support,
            "training_weighted_mae": weighted_mae,
            "feature_count": len(names),
        }

    def predict(
        self, features: object
    ) -> tuple[tuple[float, ...], tuple[float, ...], float, str]:
        if not self.fitted or not self.residual_quantiles:
            return (), (), 0.0, "unavailable"
        center = _dot(
            self.feature_names,
            self.coefficients,
            _demand_sparse_features(features),
        )
        values = tuple(
            max(0.0, math.expm1(center + residual))
            for residual in self.residual_quantiles
        )
        mass = tuple(1.0 / len(values) for _value in values)
        return values, mass, self.training_support, "pooled"

    def to_dict(self) -> dict[str, Any]:
        return {
            "regularization": self.regularization,
            "feature_names": list(self.feature_names),
            "coefficients": list(self.coefficients),
            "residual_quantiles": list(self.residual_quantiles),
            "training_support": self.training_support,
            "training_count": self.training_count,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PooledConditionalDemandModel":
        model = cls(regularization=float(raw.get("regularization", 1e-3)))
        model.feature_names = tuple(str(value) for value in raw.get("feature_names", ()))
        model.coefficients = tuple(float(value) for value in raw.get("coefficients", ()))
        model.residual_quantiles = tuple(
            float(value) for value in raw.get("residual_quantiles", ())
        )
        model.training_support = float(raw.get("training_support", 0.0))
        model.training_count = int(raw.get("training_count", 0))
        if len(model.feature_names) != len(model.coefficients):
            raise ValueError("pooled demand feature and coefficient counts differ")
        return model


class PooledConditionalClassifier:
    """Pooled multinomial model that retains rare-class training signal."""

    def __init__(
        self,
        *,
        regularization: float = 1e-3,
        balance_power: float = 0.5,
    ) -> None:
        if not math.isfinite(regularization) or regularization < 0:
            raise ValueError("classifier regularization must be finite and non-negative")
        if not math.isfinite(balance_power) or not 0.0 <= balance_power <= 1.0:
            raise ValueError("classifier balance power must be in [0, 1]")
        self.regularization = float(regularization)
        self.balance_power = float(balance_power)
        self.feature_names: tuple[str, ...] = ()
        self.class_names: tuple[str, ...] = ()
        self.coefficients: tuple[tuple[float, ...], ...] = ()
        self.class_training_multiplier: tuple[float, ...] = ()
        self.training_support = 0.0
        self.training_count = 0

    @property
    def fitted(self) -> bool:
        return bool(self.feature_names and len(self.class_names) >= 2)

    def fit(
        self,
        samples: Iterable[tuple[object, str, float]],
    ) -> dict[str, float | int]:
        values = [
            (features, str(target), float(weight))
            for features, target, weight in samples
            if str(target) and weight > 0
        ]
        classes = sorted({target for _features, target, _weight in values})
        if not values or len(classes) < 2:
            return {"sample_count": len(values), "episode_weight": 0.0}
        try:
            import numpy as np
            from scipy.optimize import minimize
        except ImportError as error:
            raise RuntimeError(
                "training pooled classifiers requires the 'training' extra"
            ) from error
        sparse_rows = [_classifier_sparse_features(item[0]) for item in values]
        names = sorted({name for row in sparse_rows for name in row})
        names.remove("bias")
        names.insert(0, "bias")
        feature_index = {name: position for position, name in enumerate(names)}
        class_index = {name: position for position, name in enumerate(classes)}
        matrix = np.zeros((len(values), len(names)), dtype=np.float64)
        for row_index, sparse in enumerate(sparse_rows):
            for name, value in sparse.items():
                matrix[row_index, feature_index[name]] = value
        target = np.asarray(
            [class_index[item[1]] for item in values], dtype=np.int64
        )
        base_weights = np.asarray([item[2] for item in values], dtype=np.float64)
        class_mass = np.bincount(
            target, weights=base_weights, minlength=len(classes)
        )
        total_mass = max(float(class_mass.sum()), 1e-12)
        multipliers = np.asarray(
            [
                (total_mass / max(float(mass) * len(classes), 1e-12))
                ** self.balance_power
                for mass in class_mass
            ],
            dtype=np.float64,
        )
        weights = base_weights * multipliers[target]
        weights *= len(weights) / max(float(weights.sum()), 1e-12)
        class_count = len(classes)
        feature_count = len(names)

        def objective(flat: Any) -> tuple[float, Any]:
            coefficients = flat.reshape(class_count, feature_count)
            logits = matrix @ coefficients.T
            logits -= logits.max(axis=1, keepdims=True)
            exp_logits = np.exp(logits)
            probabilities = exp_logits / exp_logits.sum(axis=1, keepdims=True)
            loss = float(
                np.sum(weights * -np.log(np.maximum(probabilities[np.arange(len(values)), target], 1e-15)))
                / len(values)
                + 0.5
                * self.regularization
                * np.sum(coefficients[:, 1:] ** 2)
            )
            residual = probabilities
            residual[np.arange(len(values)), target] -= 1.0
            gradient = (residual * weights[:, None]).T @ matrix / len(values)
            gradient[:, 1:] += self.regularization * coefficients[:, 1:]
            return loss, gradient.ravel()

        result = minimize(
            objective,
            np.zeros(class_count * feature_count, dtype=np.float64),
            jac=True,
            method="L-BFGS-B",
            options={"maxiter": 400, "ftol": 1e-11},
        )
        if not result.success and not math.isfinite(float(result.fun)):
            raise RuntimeError(f"pooled classifier fit failed: {result.message}")
        coefficients = result.x.reshape(class_count, feature_count)
        self.feature_names = tuple(names)
        self.class_names = tuple(classes)
        self.coefficients = tuple(
            tuple(float(value) for value in row) for row in coefficients
        )
        self.class_training_multiplier = tuple(
            float(value) for value in multipliers
        )
        self.training_support = float(base_weights.sum())
        self.training_count = len(values)
        predictions = []
        for features, _target, _weight in values:
            probabilities = self.predict(features)
            predictions.append(max(probabilities, key=probabilities.get))
        accuracy = sum(
            weight * (prediction == target_name)
            for prediction, (_features, target_name, weight) in zip(
                predictions, values, strict=True
            )
        ) / max(self.training_support, 1e-12)
        return {
            "sample_count": self.training_count,
            "episode_weight": self.training_support,
            "feature_count": feature_count,
            "class_count": class_count,
            "training_accuracy": accuracy,
            "optimizer_iterations": int(result.nit),
        }

    def predict(self, features: object) -> dict[str, float]:
        if not self.fitted:
            return {}
        sparse = _classifier_sparse_features(features)
        logits = [
            _dot(self.feature_names, coefficients, sparse)
            - math.log(max(multiplier, 1e-12))
            for coefficients, multiplier in zip(
                self.coefficients,
                self.class_training_multiplier,
                strict=True,
            )
        ]
        maximum = max(logits)
        values = [math.exp(max(-40.0, min(40.0, value - maximum))) for value in logits]
        total = sum(values)
        return {
            name: value / total
            for name, value in zip(self.class_names, values, strict=True)
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "regularization": self.regularization,
            "balance_power": self.balance_power,
            "feature_names": list(self.feature_names),
            "class_names": list(self.class_names),
            "coefficients": [list(row) for row in self.coefficients],
            "class_training_multiplier": list(self.class_training_multiplier),
            "training_support": self.training_support,
            "training_count": self.training_count,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PooledConditionalClassifier":
        model = cls(
            regularization=float(raw.get("regularization", 1e-3)),
            balance_power=float(raw.get("balance_power", 0.5)),
        )
        model.feature_names = tuple(str(value) for value in raw.get("feature_names", ()))
        model.class_names = tuple(str(value) for value in raw.get("class_names", ()))
        model.coefficients = tuple(
            tuple(float(value) for value in row)
            for row in raw.get("coefficients", ())
        )
        model.class_training_multiplier = tuple(
            float(value) for value in raw.get("class_training_multiplier", ())
        )
        model.training_support = float(raw.get("training_support", 0.0))
        model.training_count = int(raw.get("training_count", 0))
        if len(model.coefficients) != len(model.class_names):
            raise ValueError("classifier class and coefficient counts differ")
        if len(model.class_training_multiplier) != len(model.class_names):
            raise ValueError("classifier class multiplier count differs")
        if any(len(row) != len(model.feature_names) for row in model.coefficients):
            raise ValueError("classifier feature and coefficient counts differ")
        return model


class OperationalReleaseModel:
    """Direct P(tool release <= tau) model fitted from operational action rows."""

    def __init__(self, *, regularization: float = 1e-5) -> None:
        if not math.isfinite(regularization) or regularization < 0:
            raise ValueError("timing regularization must be finite and non-negative")
        self.regularization = float(regularization)
        self.feature_names: tuple[str, ...] = ()
        self.coefficients: tuple[float, ...] = ()
        self.training_support = 0.0
        self.training_count = 0

    @property
    def fitted(self) -> bool:
        return bool(self.feature_names and self.coefficients)

    def fit(
        self,
        samples: Iterable[tuple[object, float, bool, float]],
    ) -> dict[str, float | int]:
        values = [
            (features, float(tau), bool(release_within), float(weight))
            for features, tau, release_within, weight in samples
            if tau > 0 and weight > 0
        ]
        if not values:
            return {"sample_count": 0, "episode_weight": 0.0}
        try:
            import numpy as np
            from scipy.optimize import minimize
        except ImportError as error:
            raise RuntimeError(
                "training operational timing models requires the 'training' extra"
            ) from error
        sparse_rows = [
            _timing_sparse_features(features, tau)
            for features, tau, _target, _weight in values
        ]
        names = sorted({name for row in sparse_rows for name in row})
        names.remove("bias")
        names.insert(0, "bias")
        index = {name: position for position, name in enumerate(names)}
        matrix = np.zeros((len(values), len(names)), dtype=np.float64)
        for row_index, sparse in enumerate(sparse_rows):
            for name, value in sparse.items():
                matrix[row_index, index[name]] = value
        target = np.asarray([item[2] for item in values], dtype=np.float64)
        weights = np.asarray([item[3] for item in values], dtype=np.float64)
        weights *= len(weights) / max(float(weights.sum()), 1e-12)

        def objective(coefficients: Any) -> tuple[float, Any]:
            logits = matrix @ coefficients
            probabilities = 1.0 / (1.0 + np.exp(-np.clip(logits, -35.0, 35.0)))
            loss = float(
                np.sum(weights * (np.logaddexp(0.0, logits) - target * logits))
                / len(weights)
                + 0.5
                * self.regularization
                * np.sum(coefficients[1:] ** 2)
            )
            gradient = matrix.T @ (weights * (probabilities - target)) / len(weights)
            gradient[1:] += self.regularization * coefficients[1:]
            return loss, gradient

        result = minimize(
            objective,
            np.zeros(len(names), dtype=np.float64),
            jac=True,
            method="L-BFGS-B",
            options={"maxiter": 500, "ftol": 1e-12},
        )
        if not result.success and not math.isfinite(float(result.fun)):
            raise RuntimeError(f"operational timing fit failed: {result.message}")
        self.feature_names = tuple(names)
        self.coefficients = tuple(float(value) for value in result.x)
        self.training_support = float(sum(item[3] for item in values))
        self.training_count = len(values)
        probabilities = 1.0 / (
            1.0 + np.exp(-np.clip(matrix @ result.x, -35.0, 35.0))
        )
        brier = float(
            np.sum(weights * (probabilities - target) ** 2) / np.sum(weights)
        )
        return {
            "sample_count": self.training_count,
            "episode_weight": self.training_support,
            "feature_count": len(names),
            "training_brier": brier,
            "optimizer_iterations": int(result.nit),
        }

    def release_within_probability(self, features: object, tau_ms: float) -> float:
        if not self.fitted:
            return 0.0
        logit = _dot(
            self.feature_names,
            self.coefficients,
            _timing_sparse_features(features, tau_ms),
        )
        return 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, logit))))

    def curve(self, features: object) -> ActionTimingCurve | None:
        if not self.fitted:
            return None
        probabilities = _isotonic_non_decreasing(
            tuple(
                self.release_within_probability(features, tau)
                for tau in _TIMING_CURVE_KNOTS_MS
            )
        )
        return ActionTimingCurve(
            tau_ms=_TIMING_CURVE_KNOTS_MS,
            release_within_probability=probabilities,
            support_level="pooled",
            training_support=self.training_support,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "regularization": self.regularization,
            "feature_names": list(self.feature_names),
            "coefficients": list(self.coefficients),
            "training_support": self.training_support,
            "training_count": self.training_count,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "OperationalReleaseModel":
        model = cls(regularization=float(raw.get("regularization", 1e-5)))
        model.feature_names = tuple(str(value) for value in raw.get("feature_names", ()))
        model.coefficients = tuple(float(value) for value in raw.get("coefficients", ()))
        model.training_support = float(raw.get("training_support", 0.0))
        model.training_count = int(raw.get("training_count", 0))
        if len(model.feature_names) != len(model.coefficients):
            raise ValueError("timing feature and coefficient counts differ")
        return model
