"""Causal elapsed-tool features and a bounded remaining completion-path readout."""

from dataclasses import dataclass, field
import math
import sys

from beliefkv.core.events import EventConfidence, RuntimeEventKind as Kind, is_unobserved_join_event
from beliefkv.predictor.workflow_progress_tree import FEATURES, KIND as TREE_KIND, ProgressTreeHead, features
from beliefkv.predictor.workflow_work_readout import representation_fingerprint


KIND = "conditional_remaining_completion_path_v1"
WORK_PRIOR_KIND = "conditional_remaining_completion_path_with_work_prior_v2"
RESIDUAL_KIND = "conditional_remaining_completion_path_work_residual_v3"
TARGET = "remaining_model_tool_critical_path_seconds"
WORK_FEATURES = ("predicted_log_remaining_model_work", "predicted_log_work_sigma")
TIMING_FEATURES = (
    "log_active_tool_count", "log_min_active_tool_elapsed_s",
    "log_max_active_tool_elapsed_s", "log_sum_active_tool_elapsed_s",
    "log_completed_tool_count", "log_mean_completed_tool_duration_s",
)


@dataclass
class ObservedToolClock:
    started: dict = field(default_factory=dict)
    completed_count: int = 0
    completed_seconds: float = 0.

    def event(self, event, now):
        if event.confidence is EventConfidence.INFERRED or is_unobserved_join_event(event):
            return
        if not math.isfinite(now):
            raise ValueError("tool observations need a finite local clock")
        tool = event.invocation_id, (
            event.attributes.get("tool_run_id") or event.attributes.get("tool_call_id")
        )
        if event.kind is Kind.TOOL_START:
            self.started.setdefault(tool, now)
        elif event.kind is Kind.TOOL_END:
            begin = self.started.pop(tool, None)
            if begin is not None:
                self.completed_count += 1
                self.completed_seconds += max(0., now - begin)
        elif event.kind in (Kind.RETURN, Kind.INVOCATION_CANCEL):
            self.started = {
                key: begin for key, begin in self.started.items() if key[0] != event.invocation_id
            }

    def features(self, now):
        if not math.isfinite(now):
            raise ValueError("tool observation clock must be finite")
        elapsed = [max(0., now - begin) for begin in self.started.values()]
        # Sums here are input statistics, never a sum of parallel waits in the target.
        return tuple(math.log1p(value) for value in (
            len(elapsed), min(elapsed, default=0.), max(elapsed, default=0.), sum(elapsed),
            self.completed_count, self.completed_seconds / max(1, self.completed_count),
        ))


def feature_names(base, *, with_work_prior=False):
    return (
        *FEATURES,
        *("frozen_hidden_" + str(index) for index in range(base.arrays["hidden_weight"].shape[1])),
        *(WORK_FEATURES if with_work_prior else ()),
        *TIMING_FEATURES,
    )


def work_forecast_fingerprint(base):
    import hashlib
    import json
    from beliefkv.predictor.workflow_continuous_work import forecast_fingerprint

    return hashlib.sha256(json.dumps({
        "forecast": forecast_fingerprint(base.raw),
        "continuous": base.continuous_readout.contract,
    }, sort_keys=True).encode()).hexdigest()


def model_work_log_seconds(work_forecast, seconds_per_work):
    value = work_forecast.get("log1p_remaining_work")
    if (
        type(value) not in (int, float) or not math.isfinite(value) or value < 0
        or type(seconds_per_work) not in (int, float)
        or not math.isfinite(seconds_per_work) or seconds_per_work <= 0
    ):
        raise ValueError("model-time prior requires finite work and a positive service rate")
    if value == 0:
        return 0.
    log_work = (
        math.log(math.expm1(value)) if value <= math.log(2.)
        else value + math.log1p(-math.exp(-value))
    )
    log_seconds = math.log(seconds_per_work) + log_work
    return max(0., log_seconds) + math.log1p(math.exp(-abs(log_seconds)))


def path_features(observed, task, hidden, timing, *, work_forecast=None):
    import numpy as np

    timing = np.asarray(timing, dtype=float)
    if timing.shape != (len(TIMING_FEATURES),) or not np.isfinite(timing).all() or (timing < 0).any():
        raise ValueError("completion path needs finite causal tool features")
    values = [features(observed, task), hidden]
    if work_forecast is not None:
        mean = work_forecast.get("log1p_remaining_work")
        sigma = work_forecast.get("log_work_sigma")
        if sigma is None:
            sigma = 0.
        if any(
            type(value) not in (int, float) or not math.isfinite(value) or value < 0
            for value in (mean, sigma)
        ):
            raise ValueError("completion path requires its finite observed model-work forecast")
        values.append((mean, sigma))
    return np.concatenate((*values, timing))


class CompletionPathReadout:
    def __init__(self, raw, base):
        with_work_prior = isinstance(raw, dict) and raw.get("kind") in (WORK_PRIOR_KIND, RESIDUAL_KIND)
        if (
            not isinstance(raw, dict) or raw.keys() != {
                "kind", "target", "representation_sha256", "features", "trees",
                "maximum_depth", "calibration_offset", "reference_path_s",
                "calibration_status", "future_graph_used_online", "training_source",
                "model_seconds_per_work_token", "feature_log_min", "feature_log_max",
            } | ({"work_forecast_sha256"} if with_work_prior else set())
            or raw["kind"] not in (KIND, WORK_PRIOR_KIND, RESIDUAL_KIND) or raw["target"] != TARGET
            or raw["representation_sha256"] != representation_fingerprint(base)
            or raw["features"] != list(feature_names(base, with_work_prior=with_work_prior))
            or raw["future_graph_used_online"] is not False
            or raw["calibration_status"] != "whole_workflow_heldout"
            or base.conditional_tree is None or base.continuous_readout is None
            or not isinstance(raw["training_source"], dict)
            or raw["training_source"].keys() != {"path", "sha256"}
            or not isinstance(raw["training_source"]["path"], str)
            or not raw["training_source"]["path"]
            or not isinstance(raw["training_source"].get("sha256"), str)
            or len(raw["training_source"]["sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in raw["training_source"]["sha256"])
            or any(type(raw[name]) not in (int, float) or not math.isfinite(raw[name])
                   or raw[name] <= 0 for name in ("reference_path_s", "model_seconds_per_work_token"))
        ):
            raise ValueError("completion path requires a source-bound causal seconds head")
        if with_work_prior and raw["work_forecast_sha256"] != work_forecast_fingerprint(base):
            raise ValueError("completion path model-work forecast binding differs")
        bounds = raw["feature_log_min"], raw["feature_log_max"]
        if any(not isinstance(values, list) or len(values) != len(TIMING_FEATURES) for values in bounds) or any(
            type(low) not in (int, float) or type(high) not in (int, float)
            or not math.isfinite(low) or not math.isfinite(high) or not 0 <= low <= high
            for low, high in zip(*bounds)
        ):
            raise ValueError("completion path tool feature support differs")
        self.raw = raw
        self.with_work_prior = with_work_prior
        self.reference_s = raw["reference_path_s"]
        self.feature_margin = base.distribution_contract["feature_log_margin"]
        self.tree = ProgressTreeHead({
            "kind": TREE_KIND, "features": raw["features"], "objective": "log_remaining_work",
            "trees": raw["trees"], "maximum_depth": raw["maximum_depth"],
            "blend": 1., "temperature": 1., "regression_offset": raw["calibration_offset"],
            "regression_scale": 1.,
        }, feature_names=feature_names(base, with_work_prior=with_work_prior))

    def estimate(self, observed, task, hidden, timing, *, work_forecast=None):
        if self.with_work_prior and work_forecast is None:
            raise ValueError("completion path requires its existing model-work forecast")
        values = path_features(
            observed, task, hidden, timing,
            work_forecast=work_forecast if self.with_work_prior else None,
        )
        if any(
            value < low - self.feature_margin or value > high + self.feature_margin
            for value, low, high in zip(timing, self.raw["feature_log_min"], self.raw["feature_log_max"])
        ):
            return {"completion_path_support": "tool_observations_outside_training_support"}
        score = self.tree.score(values) + self.tree.offset
        if self.raw["kind"] == RESIDUAL_KIND:
            score += model_work_log_seconds(work_forecast, self.raw["model_seconds_per_work_token"])
        if not math.isfinite(score) or score >= math.log(sys.float_info.max):
            return {"completion_path_support": "nonfinite_completion_path"}
        seconds = math.expm1(max(0., score))
        return {
            "completion_path_support": "calibrated_completion_path",
            "remaining_path_s": seconds, "path_reference_s": self.reference_s,
            "path_source": TARGET,
        }
