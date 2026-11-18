"""Causal elapsed-tool features and a bounded remaining completion-path readout."""

from dataclasses import dataclass, field
import math
import sys

from beliefkv.core.events import EventConfidence, RuntimeEventKind as Kind, is_unobserved_join_event
from beliefkv.predictor.workflow_progress_tree import FEATURES, KIND as TREE_KIND, ProgressTreeHead, features
from beliefkv.predictor.workflow_work_readout import representation_fingerprint


KIND = "conditional_remaining_completion_path_v1"
TARGET = "remaining_model_tool_critical_path_seconds"
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


def feature_names(base):
    return (
        *FEATURES,
        *("frozen_hidden_" + str(index) for index in range(base.arrays["hidden_weight"].shape[1])),
        *TIMING_FEATURES,
    )


def path_features(observed, task, hidden, timing):
    import numpy as np

    timing = np.asarray(timing, dtype=float)
    if timing.shape != (len(TIMING_FEATURES),) or not np.isfinite(timing).all() or (timing < 0).any():
        raise ValueError("completion path needs finite causal tool features")
    return np.concatenate((features(observed, task), hidden, timing))


class CompletionPathReadout:
    def __init__(self, raw, base):
        if (
            not isinstance(raw, dict) or raw.keys() != {
                "kind", "target", "representation_sha256", "features", "trees",
                "maximum_depth", "calibration_offset", "reference_path_s",
                "calibration_status", "future_graph_used_online", "training_source",
                "model_seconds_per_work_token", "feature_log_min", "feature_log_max",
            }
            or raw["kind"] != KIND or raw["target"] != TARGET
            or raw["representation_sha256"] != representation_fingerprint(base)
            or raw["features"] != list(feature_names(base))
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
        bounds = raw["feature_log_min"], raw["feature_log_max"]
        if any(not isinstance(values, list) or len(values) != len(TIMING_FEATURES) for values in bounds) or any(
            type(low) not in (int, float) or type(high) not in (int, float)
            or not math.isfinite(low) or not math.isfinite(high) or not 0 <= low <= high
            for low, high in zip(*bounds)
        ):
            raise ValueError("completion path tool feature support differs")
        self.raw = raw
        self.reference_s = raw["reference_path_s"]
        self.feature_margin = base.distribution_contract["feature_log_margin"]
        self.tree = ProgressTreeHead({
            "kind": TREE_KIND, "features": raw["features"], "objective": "log_remaining_work",
            "trees": raw["trees"], "maximum_depth": raw["maximum_depth"],
            "blend": 1., "temperature": 1., "regression_offset": raw["calibration_offset"],
            "regression_scale": 1.,
        }, feature_names=feature_names(base))

    def estimate(self, observed, task, hidden, timing):
        values = path_features(observed, task, hidden, timing)
        if any(
            value < low - self.feature_margin or value > high + self.feature_margin
            for value, low, high in zip(timing, self.raw["feature_log_min"], self.raw["feature_log_max"])
        ):
            return {"completion_path_support": "tool_observations_outside_training_support"}
        score = self.tree.score(values) + self.tree.offset
        if not math.isfinite(score) or score >= math.log(sys.float_info.max):
            return {"completion_path_support": "nonfinite_completion_path"}
        seconds = math.expm1(max(0., score))
        return {
            "completion_path_support": "calibrated_completion_path",
            "remaining_path_s": seconds, "path_reference_s": self.reference_s,
            "path_source": TARGET,
        }
