"""Observed-state continuous work readout for CPU policy diagnostics."""

import math
from functools import lru_cache

from beliefkv.predictor.workflow_progress_tree import FEATURES, TreeProjection, features
from beliefkv.predictor.workflow_work import before_observed_work
from beliefkv.predictor.workflow_work_readout import representation
from beliefkv.predictor.workflow_work_training import project_task_weights


KIND = "training_referenced_continuous_model_work_v2"


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
    """Offline adapter: no serving eligibility or cache refresh behavior is added."""

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

    def predict(self, projection, observed, *, probabilities, support):
        import numpy as np

        if not isinstance(projection, TreeProjection):
            raise ValueError("continuous work requires the existing cached task/tree representation")
        if (
            len(probabilities) != 3
            or any(not math.isfinite(value) or not 0 <= value <= 1 for value in probabilities)
            or not math.isclose(sum(probabilities), 1., abs_tol=1e-6)
        ):
            raise ValueError("initial continuous work requires valid observed-state probabilities")
        if before_observed_work(observed):
            log_work = sum(
                probability * center for probability, center in zip(
                    probabilities, self.contract["initial_class_mean_log_work"],
                )
            )
            source = "training_only_initial_class_log_prior"
            distribution_utility = sum(
                probability * utility for probability, utility in zip(
                    probabilities, self.contract["initial_class_expected_utility"],
                )
            )
        else:
            hidden = representation(self.model, projection.progress_projection, observed)
            values = np.concatenate((features(observed, projection.task_structure), hidden))
            if len(values) != len(FEATURES) + len(hidden):
                raise ValueError("continuous work representation dimensions differ")
            tree = self.model.conditional_tree.tree
            log_work = tree.score(values) + tree.offset
            source = "calibrated_continuous_tree_log_work"
            distribution_utility = normal_log_work_weight(
                log_work, tree.scale, self.contract["reference_work"],
            )
        if not math.isfinite(log_work):
            raise ValueError("nonfinite continuous workflow work forecast")
        log_work = max(0., float(log_work))
        if log_work > 700.:
            raise ValueError("continuous workflow work forecast exceeds numerical support")
        work = math.expm1(log_work)
        return {
            "log1p_remaining_work": log_work, "remaining_work_point": work,
            "point_semantics": "exponentiated_conditional_log_work_not_arithmetic_work_mean",
            "source": source, "support": support,
            "distribution_effective_weight": (
                float(distribution_utility) if support == "calibrated_distribution" else .5
            ),
            "distribution_semantics": "calibration_log_residual_normal_or_training_initial_class_empirical_utility",
            "effective_weight": (
                remaining_work_weight(work, self.contract["reference_work"])
                if support == "calibrated_distribution" else .5
            ),
        }
