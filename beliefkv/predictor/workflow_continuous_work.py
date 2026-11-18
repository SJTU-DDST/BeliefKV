"""Continuous remaining-work estimates with a training-derived scheduling scale."""

import hashlib
import json
import math
from functools import lru_cache

from beliefkv.predictor.workflow_progress_tree import FEATURES, TreeProjection, features
from beliefkv.predictor.workflow_work import before_observed_work
from beliefkv.predictor.workflow_work_readout import representation
from beliefkv.predictor.workflow_work_training import project_task_weights


KIND = "training_referenced_continuous_model_work_v2"
SERVING_KIND = "single_pass_conditional_continuous_work_v1"
ADMISSION_UTILITY_KIND = "soft_inverse_remaining_model_work_v1"


def forecast_fingerprint(raw):
    from beliefkv.predictor.workflow_work_readout import TRUNK_FIELDS

    fields = TRUNK_FIELDS + (
        "work_thresholds", "output_weight", "output_bias", "temperature",
        "ordered_work_cutpoints", "initial_state_head", "conditional_progress_tree_head",
        "distribution_contract",
    )
    return hashlib.sha256(json.dumps(
        {name: raw.get(name) for name in fields}, sort_keys=True,
    ).encode()).hexdigest()


def serving_contract(model, rows):
    return {
        **fit_training_reference(rows, model.thresholds),
        "serving_kind": SERVING_KIND, "forecast_sha256": forecast_fingerprint(model.raw),
    }


def remaining_work_weight(remaining_work, reference_work):
    if (
        type(remaining_work) not in (int, float) or not math.isfinite(remaining_work)
        or remaining_work < 0 or type(reference_work) not in (int, float)
        or not math.isfinite(reference_work) or reference_work <= 0
    ):
        raise ValueError("continuous scheduling weights require finite nonnegative work and a positive reference")
    return 1. / (1. + remaining_work / reference_work)


def log_work_weight(log1p_work, reference_work):
    remaining_work_weight(0., reference_work)
    if type(log1p_work) not in (int, float) or not math.isfinite(log1p_work):
        raise ValueError("continuous work needs a finite log-work forecast")
    if log1p_work <= 0.:
        return 1.
    log_work = (
        math.log(math.expm1(log1p_work)) if log1p_work < 50.
        else log1p_work + math.log1p(-math.exp(-log1p_work))
    )
    difference = log_work - math.log(reference_work)
    if difference >= 0.:
        inverse = math.exp(-difference)
        return inverse / (1. + inverse)
    return 1. / (1. + math.exp(difference))


@lru_cache(maxsize=1)
def _quadrature():
    import numpy as np

    nodes, weights = np.polynomial.hermite.hermgauss(12)
    return tuple(nodes * math.sqrt(2.)), tuple(weights / math.sqrt(math.pi))


def normal_log_work_weight(mean, sigma, reference_work):
    if type(sigma) not in (int, float) or not math.isfinite(sigma) or sigma < 0.:
        raise ValueError("continuous work uncertainty needs a finite nonnegative log scale")
    if sigma == 0.:
        return log_work_weight(mean, reference_work)
    nodes, weights = _quadrature()
    value = float(sum(
        weight * log_work_weight(float(mean + sigma * node), reference_work)
        for node, weight in zip(nodes, weights)
    ))
    return min(1., max(0., value))


def utility_reference_work(contract):
    """Keep admission regularization separate from the retained training scale."""
    reference = contract.get("reference_work")
    remaining_work_weight(0., reference)
    utility = contract.get("admission_utility")
    if utility is None:
        return reference
    if (
        not isinstance(utility, dict)
        or set(utility) != {
            "kind", "reference_work", "initial_class_expected_utility",
            "initial_prior_uses_training_labels_only",
        }
        or utility["kind"] != ADMISSION_UTILITY_KIND
        or utility["initial_prior_uses_training_labels_only"] is not True
    ):
        raise ValueError("admission utility requires its explicit scale and training-only initial prior")
    remaining_work_weight(0., utility["reference_work"])
    priors = utility["initial_class_expected_utility"]
    if (
        not isinstance(priors, (list, tuple)) or len(priors) != 3
        or any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1
               for value in priors)
        or list(priors) != sorted(priors, reverse=True)
    ):
        raise ValueError("admission utility requires three ordered finite initial class expectations")
    return utility["reference_work"]


def fit_admission_utility(contract, rows, *, reference_work):
    """Reweight the same complete training executions, without refitting forecasts."""
    import numpy as np

    remaining_work_weight(0., reference_work)
    fitted = fit_training_reference(rows, contract["work_thresholds"])
    if "admission_utility" in contract or any(contract.get(key) != value for key, value in fitted.items()):
        raise ValueError("admission utility must retain the exact original training reference")
    initial = [row for row in rows if row["snapshot"] == 0]
    weights = project_task_weights(initial, np.ones(len(initial)))
    classes = np.searchsorted(contract["work_thresholds"], [row["total_work"] for row in initial], side="right")
    values = np.asarray([remaining_work_weight(row["total_work"], reference_work) for row in initial])
    result = {
        **contract,
        "admission_utility": {
            "kind": ADMISSION_UTILITY_KIND, "reference_work": reference_work,
            "initial_class_expected_utility": [
                float(np.average(values[classes == label], weights=weights[classes == label]))
                for label in range(3)
            ],
            "initial_prior_uses_training_labels_only": True,
        },
    }
    utility_reference_work(result)
    return result


def fit_training_reference(rows, thresholds):
    """All future work here belongs to training executions, never an online query."""
    import numpy as np

    initial = [row for row in rows if row["snapshot"] == 0]
    if (
        not initial or len({row["workflow"] for row in initial}) != len(initial)
        or {row["workflow"] for row in initial} != {row["workflow"] for row in rows}
        or len(thresholds) != 2 or not 0 < thresholds[0] < thresholds[1]
        or any(
            not math.isfinite(row["total_work"]) or row["total_work"] <= 0
            or row["attained_work"] != 0. or row["remaining_work"] != row["total_work"]
            for row in initial
        )
    ):
        raise ValueError("continuous priors require complete training workflows with one unserved initial label")
    weights = project_task_weights(initial, np.ones(len(initial)))
    work = np.asarray([row["total_work"] for row in initial])
    order = np.argsort(work, kind="stable")
    median = float(work[order[np.searchsorted(np.cumsum(weights[order]), .5)]])
    classes = np.searchsorted(thresholds, work, side="right")
    counts = [int((classes == label).sum()) for label in range(3)]
    if min(counts) < 2:
        raise ValueError("initial class work priors require at least two complete training executions per class")
    return {
        "kind": KIND, "target": "remaining_logical_model_work", "reference_work": median,
        "work_thresholds": list(thresholds),
        "initial_class_mean_log_work": [
            float(np.average(np.log1p(work[classes == label]), weights=weights[classes == label]))
            for label in range(3)
        ],
        "initial_class_expected_utility": [
            float(np.average(
                [remaining_work_weight(float(value), median) for value in work[classes == label]],
                weights=weights[classes == label],
            )) for label in range(3)
        ],
        "training_executions": len(initial), "training_projects": len({row["project"] for row in initial}),
        "initial_class_execution_counts": counts,
        "reference_and_initial_priors_use_training_labels_only": True,
        "initial_prior_still_has_no_within_class_semantic_resolution": True,
    }


class ContinuousWorkReadout:
    """Interpret one conditional log-work forecast without class quantization."""

    def __init__(self, model, contract):
        if (
            model.conditional_tree is None or model.conditional_tree.blend != 1.
            or contract.get("kind") != KIND
            or contract.get("target") != "remaining_logical_model_work"
            or contract.get("work_thresholds") != list(model.thresholds)
            or contract.get("reference_and_initial_priors_use_training_labels_only") is not True
            or len(contract.get("initial_class_mean_log_work", ())) != 3
            or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                   for value in contract["initial_class_mean_log_work"])
            or list(contract["initial_class_mean_log_work"]) != sorted(contract["initial_class_mean_log_work"])
            or len(contract.get("initial_class_expected_utility", ())) != 3
            or any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1
                   for value in contract["initial_class_expected_utility"])
            or list(contract["initial_class_expected_utility"]) != sorted(
                contract["initial_class_expected_utility"], reverse=True,
            )
        ):
            raise ValueError("continuous work requires its evaluated direct-work tree and fixed training prior")
        remaining_work_weight(0., contract["reference_work"])
        self.model, self.contract = model, contract
        self.utility_reference = utility_reference_work(contract)
        self.neutral_weight = remaining_work_weight(contract["reference_work"], self.utility_reference)

    def estimate(self, probabilities, *, mean=None, sigma=None, support="calibrated_distribution"):
        if (
            len(probabilities) != 3
            or any(not math.isfinite(value) or not 0 <= value <= 1 for value in probabilities)
            or not math.isclose(sum(probabilities), 1., abs_tol=1e-6)
        ):
            raise ValueError("initial continuous work requires valid observed-state probabilities")
        if mean is None and sigma is None:
            log_work = sum(
                probability * center for probability, center in zip(
                    probabilities, self.contract["initial_class_mean_log_work"],
                )
            )
            source = "training_only_initial_class_log_prior"
            utility = sum(
                probability * value for probability, value in zip(
                    probabilities, self.contract.get(
                        "admission_utility", self.contract,
                    )["initial_class_expected_utility"],
                )
            )
        else:
            if (
                type(mean) not in (int, float) or not math.isfinite(mean)
                or type(sigma) not in (int, float) or not math.isfinite(sigma) or sigma < 0
            ):
                raise ValueError("continuous work requires a finite conditional log distribution")
            log_work = mean
            source = "calibrated_continuous_tree_log_work"
            utility = normal_log_work_weight(mean, sigma, self.utility_reference)
        log_work = max(0., float(log_work))
        if log_work > 700.:
            raise ValueError("continuous workflow work forecast exceeds numerical support")
        work = math.expm1(log_work)
        return {
            "log1p_remaining_work": log_work, "remaining_work_point": work,
            "log_work_sigma": sigma, "reference_work": self.contract["reference_work"],
            "point_semantics": "exponentiated_conditional_log_work_not_arithmetic_work_mean",
            "source": source, "support": support,
            "distribution_effective_weight": (
                float(utility) if support == "calibrated_distribution" else self.neutral_weight
            ),
            "distribution_semantics": "calibration_log_residual_normal_or_training_initial_class_empirical_utility",
            "effective_weight": (
                remaining_work_weight(work, self.utility_reference)
                if support == "calibrated_distribution" else self.neutral_weight
            ),
            **({
                "utility_reference_work": self.utility_reference,
                "unsupported_effective_weight": self.neutral_weight,
            } if "admission_utility" in self.contract else {}),
        }

    def predict(self, projection, observed, *, probabilities, support):
        import numpy as np

        if not isinstance(projection, TreeProjection):
            raise ValueError("continuous work requires the existing cached task/tree representation")
        if before_observed_work(observed):
            return self.estimate(probabilities, support=support)
        hidden = representation(self.model, projection.progress_projection, observed)
        values = np.concatenate((features(observed, projection.task_structure), hidden))
        if len(values) != len(FEATURES) + len(hidden):
            raise ValueError("continuous work representation dimensions differ")
        tree = self.model.conditional_tree.tree
        return self.estimate(
            probabilities, mean=tree.score(values) + tree.offset, sigma=tree.scale, support=support,
        )
