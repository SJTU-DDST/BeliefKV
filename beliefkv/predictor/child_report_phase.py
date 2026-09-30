"""Small delivered-text phase/work predictor; never authorizes KV actions."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Sequence

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F


MAX_WORDS = 64
PROGRESS_DIM = 5
EVENT_DIM = 3
QUANTILES = (0.1, 0.5, 0.9)
PHASES = ("continue_work", "completion_notice", "final_report")
_WORDS = re.compile(r"[a-z_][a-z_0-9]*|[^\s]", re.IGNORECASE)


@dataclass(frozen=True)
class ReportObservation:
    request_id: str
    invocation_id: str
    context_id: str
    context_epoch: int
    ts_ms: float
    content_tail: str
    content_chars: int
    observed_output_tokens: int
    notice_active: bool = False
    estimated_report_tokens: int = 0
    prior_tool_calls: int = 0
    prior_model_rounds: int = 0
    tool_chunk_seen: bool = False

    def __post_init__(self) -> None:
        if (
            not all((self.request_id, self.invocation_id, self.context_id))
            or not math.isfinite(self.ts_ms)
            or min(self.context_epoch, self.content_chars,
                   self.observed_output_tokens, self.estimated_report_tokens,
                   self.prior_tool_calls, self.prior_model_rounds) < 0
        ):
            raise ValueError("invalid report observation identity or progress")

    def features(self, *, with_events: bool) -> list[float]:
        progress = [
            math.log1p(self.content_chars),
            math.log1p(self.observed_output_tokens),
            math.log1p(self.prior_tool_calls),
            math.log1p(self.prior_model_rounds),
            float(self.content_tail.rstrip().endswith((".", "!", "?"))),
        ]
        hint = self.estimated_report_tokens if self.notice_active else 0
        return progress + ([
            float(self.notice_active),
            math.log1p(hint),
            math.log1p(max(0, hint - self.observed_output_tokens)),
        ] if with_events else [0., 0., 0.])


@dataclass(frozen=True)
class ReportPrediction:
    request_id: str
    invocation_id: str
    context_id: str
    context_epoch: int
    observed_ts_ms: float
    final_report_score: float
    conditional_remaining_tokens: tuple[float, float, float] | None
    observed_stage: str
    predicted_phase: str = "unknown"
    completion_notice_score: float = 0.
    score_status: str = "uncalibrated"
    return_eta_ms: None = None
    physical_action_authorized: bool = False


def words(text: str) -> list[str]:
    return _WORDS.findall(text.lower())[-MAX_WORDS:]


def fit_vocabulary(
    observations: Sequence[ReportObservation], *, limit: int = 2048,
) -> dict[str, int]:
    counts = Counter(token for row in observations for token in words(row.content_tail))
    common = sorted(
        (pair for pair in counts.items() if pair[1] >= 2),
        key=lambda pair: (-pair[1], pair[0]),
    )[:limit]
    return {token: i + 2 for i, (token, _) in enumerate(common)}


def tensors(
    observations: Sequence[ReportObservation],
    vocabulary: dict[str, int],
    *,
    with_events: bool,
    center: np.ndarray | None = None,
    scale: np.ndarray | None = None,
) -> tuple[torch.Tensor, torch.Tensor]:
    ids = np.zeros((len(observations), MAX_WORDS), dtype=np.int64)
    for i, row in enumerate(observations):
        encoded = [vocabulary.get(token, 1) for token in words(row.content_tail)]
        ids[i, :len(encoded)] = encoded
    numeric = np.asarray(
        [row.features(with_events=with_events) for row in observations],
        dtype=np.float32,
    ).reshape(-1, PROGRESS_DIM + EVENT_DIM)
    if center is not None and scale is not None:
        numeric = (numeric - center) / scale
    return torch.from_numpy(ids), torch.from_numpy(numeric)


class ReportPhaseNetwork(nn.Module):
    def __init__(self, vocabulary_size: int) -> None:
        super().__init__()
        self.embedding = nn.Embedding(vocabulary_size, 24, padding_idx=0)
        self.convolutions = nn.ModuleList(
            nn.Conv1d(24, 16, width) for width in (2, 3, 5)
        )
        self.shared = nn.Sequential(
            nn.Linear(48 + PROGRESS_DIM + EVENT_DIM, 32),
            nn.Tanh(),
        )
        self.stage = nn.Linear(32, len(PHASES))
        self.work = nn.Linear(32, 3)

    def forward(
        self, token_ids: torch.Tensor, numeric: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        embedded = self.embedding(token_ids).transpose(1, 2)
        pooled = torch.cat([
            torch.amax(torch.relu(layer(embedded)), dim=-1)
            for layer in self.convolutions
        ], dim=1)
        state = self.shared(torch.cat((pooled, numeric), dim=1))
        # Positive increments ensure non-crossing log-token quantiles.
        quantiles = torch.cumsum(F.softplus(self.work(state)), dim=1)
        return self.stage(state), quantiles


def pinball_loss(
    prediction: torch.Tensor, target: torch.Tensor,
) -> torch.Tensor:
    levels = prediction.new_tensor(QUANTILES)
    residual = target[:, None] - prediction
    return torch.maximum(levels * residual, (levels - 1) * residual).mean(dim=1)


class ChildReportPredictor:
    def __init__(
        self, network: ReportPhaseNetwork, vocabulary: dict[str, int],
        center: np.ndarray, scale: np.ndarray, *, with_events: bool,
    ) -> None:
        self.network = network.eval()
        self.vocabulary = vocabulary
        self.center = center
        self.scale = scale
        self.with_events = with_events

    def save(self, path: str | Path, *, training_projects: Sequence[str]) -> None:
        torch.save({
            "schema_version": 2,
            "online_eligible": False,
            "training_projects": sorted(set(training_projects)),
            "vocabulary": self.vocabulary,
            "center": self.center.tolist(),
            "scale": self.scale.tolist(),
            "with_events": self.with_events,
            "state_dict": self.network.state_dict(),
        }, path)

    @classmethod
    def load(cls, path: str | Path) -> "ChildReportPredictor":
        raw = torch.load(path, map_location="cpu", weights_only=True)
        if raw.get("schema_version") != 2:
            raise ValueError("unsupported report predictor schema")
        network = ReportPhaseNetwork(len(raw["vocabulary"]) + 2)
        network.load_state_dict(raw["state_dict"])
        return cls(
            network, raw["vocabulary"], np.asarray(raw["center"], dtype=np.float32),
            np.asarray(raw["scale"], dtype=np.float32),
            with_events=raw["with_events"],
        )

    def predict(
        self, observations: Sequence[ReportObservation],
    ) -> list[ReportPrediction]:
        if not observations:
            return []
        ids, numeric = tensors(
            observations, self.vocabulary, with_events=self.with_events,
            center=self.center, scale=self.scale,
        )
        with torch.inference_mode():
            logits, work = self.network(ids, numeric)
            scores = torch.softmax(logits, dim=1).numpy()
            remaining = torch.expm1(work.clamp(max=16.)).numpy()
        return [
            ReportPrediction(
                row.request_id, row.invocation_id, row.context_id,
                row.context_epoch, row.ts_ms,
                0. if row.tool_chunk_seen else float(score[2]),
                None if row.tool_chunk_seen else tuple(float(v) for v in quantiles),
                "observed_tool" if row.tool_chunk_seen else "unresolved",
                "observed_tool" if row.tool_chunk_seen else PHASES[int(np.argmax(score))],
                0. if row.tool_chunk_seen else float(score[1]),
            )
            for row, score, quantiles in zip(observations, scores, remaining)
        ]
