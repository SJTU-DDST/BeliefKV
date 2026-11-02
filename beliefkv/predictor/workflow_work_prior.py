"""A small total-work prior, conditioned on already observed model service."""

from dataclasses import dataclass
import math

from beliefkv.predictor.workflow_task_encoder import TASK_FEATURES, WorkflowTaskEncoder, task_features
from beliefkv.predictor.workflow_rank_input import TASK_CHARS


KIND = "observed_service_conditioned_total_work_prior_v1"


@dataclass(frozen=True)
class PriorProjection:
    progress_projection: object
    log_total_mean: float


def log_survival(z):
    value = .5 * math.erfc(z / math.sqrt(2.))
    if value:
        return math.log(value)
    # Normal-tail expansion avoids 0/0 for an exceptionally long observed job.
    inverse = 1. / (z * z)
    return -.5 * z * z - math.log(z) - .5 * math.log(2. * math.pi) + math.log(
        1. - inverse + 3. * inverse * inverse,
    )


class TotalWorkPrior:
    def __init__(self, raw):
        import numpy as np

        if not isinstance(raw, dict) or raw.keys() - {"activation"} != {
            "kind", "encoding", "task_encoder", "center", "coefficient", "bias",
            "log_total_sigma", "initial_blend", "blend_work", "target",
        } or (
            raw["kind"] != KIND or raw["target"] != "log1p_complete_logical_model_work"
            or raw["encoding"] not in ("task_structure_v1", "semantic_and_structure_v1")
        ):
            raise ValueError("invalid observed-service total-work prior")
        self.encoder = None
        if raw["encoding"] == "semantic_and_structure_v1":
            self.encoder = WorkflowTaskEncoder(raw["task_encoder"])
        elif raw["task_encoder"] is not None:
            raise ValueError("structured prior cannot contain an unused semantic encoder")
        dimension = self.encoder.dimension if self.encoder else len(TASK_FEATURES)
        self.center = np.asarray(raw["center"], dtype=float)
        self.coefficient = np.asarray(raw["coefficient"], dtype=float)
        if any(value.shape != (dimension,) or not np.isfinite(value).all()
               for value in (self.center, self.coefficient)):
            raise ValueError("invalid total-work prior dimensions")
        for name in ("bias", "log_total_sigma", "initial_blend", "blend_work"):
            if type(raw[name]) not in (int, float) or not math.isfinite(raw[name]):
                raise ValueError("nonfinite total-work prior")
        self.bias, self.sigma = raw["bias"], raw["log_total_sigma"]
        self.activation = raw.get("activation", "service_decay")
        if self.activation not in ("service_decay", "before_first_model_service"):
            raise ValueError("invalid total-work prior activation")
        self.initial_blend, self.blend_work = raw["initial_blend"], raw["blend_work"]
        if not .05 <= self.sigma <= 5. or not 0 < self.initial_blend <= 1. or self.blend_work <= 0:
            raise ValueError("invalid total-work prior scale or blend")

    def task_mean(self, text):
        import numpy as np

        if type(text) is not str or not text.strip() or len(text) > TASK_CHARS:
            raise ValueError("total-work prior requires bounded explicit task instructions")
        vector, coverage = (
            self.encoder.encode(text) if self.encoder
            else (np.asarray(task_features(text)), 1.)
        )
        if vector is None:
            return self.bias, coverage
        return self.bias + float((vector - self.center) @ self.coefficient), coverage

    def distribution(self, mean, attained, thresholds):
        if not math.isfinite(mean) or not math.isfinite(attained) or attained < 0:
            raise ValueError("invalid observed service for total-work conditioning")
        base = log_survival((math.log1p(attained) - mean) / self.sigma)
        tails = [
            math.exp(min(0., log_survival(
                (math.log1p(attained + value) - mean) / self.sigma,
            ) - base)) for value in thresholds
        ]
        return 1. - tails[0], tails[0] - tails[1], tails[1]

    def combine(self, progress_probabilities, mean, features, thresholds):
        attained = .01 * features[0] + features[1]
        if self.activation == "before_first_model_service" and attained > 0:
            return tuple(progress_probabilities)
        prior = self.distribution(mean, attained, thresholds)
        blend = self.initial_blend * math.exp(-attained / self.blend_work)
        return tuple(
            blend * left + (1. - blend) * right
            for left, right in zip(prior, progress_probabilities)
        )
