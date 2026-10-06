"""Small work-only inference on the frozen phase representation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import numpy as np
from scipy.special import erf

from beliefkv.predictor.child_report_phase import ReportObservation


LEGACY_WORK_PROJECTION = "clip_then_expand_v1"
SIGNED_WORK_PROJECTION = "expand_then_clip_v2"


def project_work_bounds(
    raw: np.ndarray, *, bias: float, margin: float, projection: str,
) -> np.ndarray:
    """Expand signed residual bounds before projecting onto feasible work."""
    work = np.asarray(raw, dtype=float) + bias
    if projection == LEGACY_WORK_PROJECTION:
        work = np.maximum(0., work)
    elif projection != SIGNED_WORK_PROJECTION:
        raise ValueError("unsupported work interval projection")
    work = work + np.asarray((-margin, 0., margin))
    return np.maximum(0., work)


def structural_work_features(
    observations: Sequence[ReportObservation], *, version: str = "structural_v1",
) -> np.ndarray:
    if version not in ("structural_v1", "body_progress_v2"):
        raise ValueError("unsupported structural work features")
    values = []
    for row in observations:
        text = row.content_tail.rstrip()
        lower = text.lower()
        features = [
            float(text.endswith((".", "!", "?", "`", ")", "]", "}"))),
            float(text.count("```") % 2),
            float(text.count("(") > text.count(")")),
            float(text.count("[") > text.count("]")),
            float(text.count("{") > text.count("}")),
            float("\n" in text[-80:]),
            float(any(word in lower[-200:] for word in (
                "in summary", "in conclusion", "verified", "passed", "no further",
            ))),
            np.log1p(max(0, row.estimated_report_tokens - row.observed_output_tokens)),
        ]
        if version == "body_progress_v2":
            hint = row.estimated_report_tokens if row.notice_active else 0
            # A visible-body character estimate is not the native KV token count.
            body_tokens = row.content_chars / 4.
            features[-1] = np.log1p(max(0., hint - body_tokens))
            features.extend((
                np.log1p(body_tokens),
                min(8., body_tokens / hint) if hint else 0.,
                np.log1p(max(0., row.observed_output_tokens - body_tokens)),
                float(text.endswith(("**", "```"))),
            ))
        values.append(features)
    return np.asarray(values, dtype=np.float32)


class NeuralConditionalWork:
    def __init__(self, raw: dict) -> None:
        self.schema = raw.get("schema_version")
        if raw.get("kind") != "neural_conditional_work" or self.schema not in (1, 2):
            raise ValueError("unsupported neural work artifact")
        self.center = np.asarray(raw["center"], dtype=float)
        self.scale = np.asarray(raw["scale"], dtype=float)
        self.layers = tuple(
            (np.asarray(layer["weight"], dtype=float), np.asarray(layer["bias"], dtype=float))
            for layer in raw["layers"]
        )
        self.target = raw["target"]
        self.bias = float(raw["token_bias"] if self.schema == 1 else raw["log1p_bias"])
        self.margin = float(
            raw["interval_margin_tokens"] if self.schema == 1 else raw["interval_margin_log1p"],
        )
        self.projection = raw.get("work_interval_projection", LEGACY_WORK_PROJECTION)
        self.feature_version = raw.get("feature_version", "structural_v1")
        if (
            self.target not in ("remaining", "total") or self.margin < 0
            or self.schema == 2 and (
                self.target != "remaining"
                or raw.get("output_space") != "ordered_log1p_quantiles"
            )
            or self.projection not in (LEGACY_WORK_PROJECTION, SIGNED_WORK_PROJECTION)
            or self.feature_version not in ("structural_v1", "body_progress_v2")
            or self.schema == 1 and self.feature_version != "structural_v1"
            or not np.isfinite((self.bias, self.margin)).all()
            or len(self.center) != len(self.scale) or (self.scale <= 0).any()
            or not all(np.isfinite(a).all() for a in (self.center, self.scale))
            or len(self.layers) != 3
        ):
            raise ValueError("invalid neural work geometry")
        width = len(self.center)
        for weight, bias in self.layers:
            if weight.ndim != 2 or weight.shape[1] != width or bias.shape != (weight.shape[0],):
                raise ValueError("incompatible neural work layer")
            if not np.isfinite(weight).all() or not np.isfinite(bias).all():
                raise ValueError("nonfinite neural work weights")
            width = weight.shape[0]
        if width != (1 if self.schema == 1 else 3):
            raise ValueError("neural work output width does not match schema")

    @classmethod
    def load(cls, path: Path) -> "NeuralConditionalWork":
        return cls(json.loads(path.read_text()))

    def arrays(self, observations, embeddings, phase) -> np.ndarray:
        raw = np.column_stack((
            phase.design(observations, embeddings),
            structural_work_features(observations, version=self.feature_version),
        ))
        value = (raw - self.center) / self.scale
        for index, (weight, bias) in enumerate(self.layers):
            value = value @ weight.T + bias
            if index != len(self.layers)-1:
                value = .5 * value * (1. + erf(value / np.sqrt(2.)))
        if self.schema == 2:
            bounds = np.sort(value, axis=1) + self.bias
            bounds += np.asarray((-self.margin, 0., self.margin))
            return np.expm1(np.clip(bounds, 0., 16.))
        middle = np.expm1(np.clip(value[:, 0], 0, 16))
        if self.target == "total":
            middle -= np.asarray([row.observed_output_tokens for row in observations])
        return project_work_bounds(
            np.repeat(middle[:, None], 3, axis=1),
            bias=self.bias, margin=self.margin, projection=self.projection,
        )
