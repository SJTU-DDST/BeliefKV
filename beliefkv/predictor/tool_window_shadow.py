"""Frozen, opt-in 100 ms tool timing probe; never emits a control action."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from threading import Lock
from typing import Any, Mapping

import lightgbm as lgb
import numpy as np


def _nonnegative(value: Any) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        return 0.
    return max(0., float(value))


def feature_row(attrs: Mapping[str, Any], vocabulary: Mapping[str, int]) -> np.ndarray:
    """Match the seven feature columns of the frozen training head."""

    shape = str(attrs.get("observed_command_shape") or "unknown")
    return np.asarray([[
        vocabulary.get(shape, -1),
        math.log1p(_nonnegative(attrs.get("input_chars"))),
        math.log1p(_nonnegative(attrs.get("project_class_completed_support"))),
        math.log1p(_nonnegative(attrs.get(
            "project_class_inflight_other_workflow_2s_peers"
        ))),
        math.log1p(_nonnegative(attrs.get("project_class_duration_median_ms"))),
        math.log1p(_nonnegative(attrs.get("project_input_neighbor_duration_ms"))),
        math.log1p(_nonnegative(attrs.get("project_input_neighbor_support"))),
    ]], dtype=np.float32)


@dataclass(frozen=True)
class ToolWindowEstimate:
    probability: float
    total_eta_ms: float
    global_eta_ms: float


class FrozenToolWindowShadow:
    def __init__(self, artifact: Path) -> None:
        payload = artifact.read_bytes()
        manifest = json.loads(payload)
        if (
            manifest.get("schema_version") != 1
            or manifest.get("target_total_ms") != 600
            or not isinstance(manifest.get("training_projects"), list)
            or not manifest["training_projects"]
            or not isinstance(manifest.get("classifier_vocabulary"), dict)
            or not isinstance(manifest.get("eta_vocabulary"), dict)
            or not 0 < manifest.get("frozen_probability_threshold", -1) < 1
        ):
            raise ValueError("incompatible or incomplete tool window artifact")
        self.artifact_sha256 = hashlib.sha256(payload).hexdigest()
        self.training_projects = frozenset(manifest["training_projects"])
        self.classifier_vocabulary = manifest["classifier_vocabulary"]
        self.eta_vocabulary = manifest["eta_vocabulary"]
        self.threshold = float(manifest["frozen_probability_threshold"])
        self.global_eta_ms = float(manifest["global_long_total_eta_ms"])
        if not math.isfinite(self.global_eta_ms) or self.global_eta_ms < 600:
            raise ValueError("invalid frozen global tool ETA")
        models = []
        for name in ("classifier", "conditional_eta"):
            record = manifest["models"][name]
            basename = record["filename"]
            if (
                not isinstance(basename, str) or basename != Path(basename).name
            ):
                raise ValueError("tool window model must be next to the manifest")
            path = artifact.parent / basename
            if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
                raise ValueError(f"tool window {name} checksum mismatch")
            models.append(lgb.Booster(model_file=str(path)))
        self._classifier, self._eta = models
        self._lock = Lock()

    def estimate(self, attrs: Mapping[str, Any]) -> ToolWindowEstimate:
        classifier_row = feature_row(attrs, self.classifier_vocabulary)
        eta_row = feature_row(attrs, self.eta_vocabulary)
        with self._lock:
            probability = float(
                self._classifier.predict(classifier_row, num_threads=1)[0]
            )
            eta = float(
                np.expm1(self._eta.predict(eta_row, num_threads=1)[0])
            )
        if not math.isfinite(probability) or not math.isfinite(eta):
            raise ValueError("non-finite frozen tool window prediction")
        return ToolWindowEstimate(
            probability=probability,
            total_eta_ms=min(1_000_000., max(600., eta)),
            global_eta_ms=self.global_eta_ms,
        )
