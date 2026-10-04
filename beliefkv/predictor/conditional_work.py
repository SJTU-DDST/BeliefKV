"""Small work-only inference on the frozen phase representation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import numpy as np
from scipy.special import erf

from beliefkv.predictor.child_report_phase import ReportObservation


def structural_work_features(observations: Sequence[ReportObservation]) -> np.ndarray:
    values = []
    for row in observations:
        text = row.content_tail.rstrip()
        lower = text.lower()
        values.append([
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
        ])
    return np.asarray(values, dtype=np.float32)


class NeuralConditionalWork:
    def __init__(self, raw: dict) -> None:
        if raw.get("kind") != "neural_conditional_work" or raw.get("schema_version") != 1:
            raise ValueError("unsupported neural work artifact")
        self.center = np.asarray(raw["center"], dtype=float)
        self.scale = np.asarray(raw["scale"], dtype=float)
        self.layers = tuple(
            (np.asarray(layer["weight"], dtype=float), np.asarray(layer["bias"], dtype=float))
            for layer in raw["layers"]
        )
        self.target = raw["target"]
        self.bias = float(raw["token_bias"])
        self.margin = float(raw["interval_margin_tokens"])
        if (
            self.target not in ("remaining", "total") or self.margin < 0
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
        if width != 1:
            raise ValueError("neural work output is not scalar")

    @classmethod
    def load(cls, path: Path) -> "NeuralConditionalWork":
        return cls(json.loads(path.read_text()))

    def arrays(self, observations, embeddings, phase) -> np.ndarray:
        raw = np.column_stack((
            phase.design(observations, embeddings),
            structural_work_features(observations),
        ))
        value = (raw - self.center) / self.scale
        for index, (weight, bias) in enumerate(self.layers):
            value = value @ weight.T + bias
            if index != len(self.layers)-1:
                value = .5 * value * (1. + erf(value / np.sqrt(2.)))
        middle = np.expm1(np.clip(value[:, 0], 0, 16))
        if self.target == "total":
            middle -= np.asarray([row.observed_output_tokens for row in observations])
        middle = np.maximum(0., middle + self.bias)
        return np.column_stack((
            np.maximum(0., middle-self.margin), middle, middle+self.margin,
        ))
