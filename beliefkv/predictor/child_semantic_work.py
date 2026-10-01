"""Frozen semantic features and disjoint score/work calibration; advisory only."""

from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path
from typing import Sequence

import numpy as np
from scipy.optimize import minimize, minimize_scalar
from scipy.special import logsumexp, softmax
import torch

from beliefkv.predictor.child_report_phase import PHASES, ReportObservation, ReportPrediction


class FrozenTextEncoder:
    def __init__(self, snapshot: str | Path, *, max_tokens: int = 256) -> None:
        from transformers import AutoModel, AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(snapshot, local_files_only=True)
        self.model = AutoModel.from_pretrained(
            snapshot, local_files_only=True, trust_remote_code=False,
        ).eval()
        self.max_tokens = max_tokens

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        result = []
        with torch.inference_mode():
            for start in range(0, len(texts), batch_size):
                batch = self.tokenizer(
                    list(texts[start:start + batch_size]), padding=True,
                    truncation=True, max_length=self.max_tokens,
                    return_tensors="pt",
                )
                hidden = self.model(**batch).last_hidden_state
                mask = batch["attention_mask"].unsqueeze(-1)
                pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
                pooled = torch.nn.functional.normalize(pooled, dim=1)
                result.append(pooled.numpy())
        return np.concatenate(result) if result else np.empty((0, 384), dtype=np.float32)


class SemanticReportPredictor:
    """Single-worker-owned predictor for an asynchronous, read-only consumer."""

    def __init__(
        self, head: "SemanticHead", encoder: FrozenTextEncoder | None, *, cache_size: int = 128,
        work_head: "SemanticHead | None" = None,
    ) -> None:
        self.head = head
        self.encoder = encoder
        self.work_head = work_head
        if cache_size < 0:
            raise ValueError("semantic text cache size must be nonnegative")
        self.cache_size = cache_size
        self._text_cache: OrderedDict[str, np.ndarray] = OrderedDict()

    @classmethod
    def load(cls, path: str | Path) -> "SemanticReportPredictor":
        path = Path(path).resolve()
        raw = json.loads(path.read_text())
        head = SemanticHead.load(path)
        work_head = None
        if reference := raw.get("conditional_work_head"):
            work_path = path.parent / reference["path"]
            if hashlib.sha256(work_path.read_bytes()).hexdigest() != reference["sha256"]:
                raise ValueError("conditional work head fingerprint changed")
            work_raw = json.loads(work_path.read_text())
            phase_encoder = raw["metadata"]["adapted_encoder"]["weights_sha256"]
            work_encoder = work_raw["metadata"]["plan"]["encoder"]["revision"]
            if phase_encoder != work_encoder:
                raise ValueError("phase/work heads require identical frozen encoders")
            work_head = SemanticHead.load(work_path)
        encoder = None
        if len(head.components):
            metadata = raw["metadata"]
            adapted = metadata.get("adapted_encoder")
            snapshot = (
                Path(adapted["snapshot"]) if adapted
                else Path(metadata["plan"]["encoder"]["local_snapshot"])
            )
            if not snapshot.is_absolute():
                snapshot = path.parent / "adapted_encoder"
            encoder = FrozenTextEncoder(
                snapshot, max_tokens=metadata["plan"]["encoder"]["max_tokens"],
            )
        return cls(head, encoder, work_head=work_head)

    def predict(self, observations: Sequence[ReportObservation]) -> list[ReportPrediction]:
        if not observations:
            return []
        if self.encoder is None:
            embeddings = np.zeros((len(observations), len(self.head.embedding_center)))
        else:
            missing = list(dict.fromkeys(
                row.content_tail for row in observations
                if row.content_tail not in self._text_cache
            ))
            encoded = dict(zip(missing, self.encoder.encode(missing)))
            # Build this batch before eviction, including batches larger than the cache.
            values = []
            for row in observations:
                if row.content_tail in encoded:
                    value = encoded[row.content_tail]
                else:
                    value = self._text_cache[row.content_tail]
                values.append(value)
                self._text_cache[row.content_tail] = value
                self._text_cache.move_to_end(row.content_tail)
            embeddings = np.asarray(values)
            while len(self._text_cache) > self.cache_size:
                self._text_cache.popitem(last=False)
        predictions = self.head.predictions(observations, embeddings)
        if self.work_head is not None:
            work = self.work_head.predictions(observations, embeddings)
            predictions = [
                replace(phase, conditional_remaining_tokens=remaining.conditional_remaining_tokens,
                        work_interval_status=remaining.work_interval_status)
                for phase, remaining in zip(predictions, work)
            ]
        return predictions


def workflow_weights(tasks: Sequence[str]) -> np.ndarray:
    counts = Counter(tasks)
    weights = np.asarray([1. / counts[task] for task in tasks])
    return weights / weights.sum()


@dataclass
class SemanticHead:
    numeric_center: np.ndarray
    numeric_scale: np.ndarray
    embedding_center: np.ndarray
    components: np.ndarray
    component_scale: np.ndarray
    phase_coefficients: np.ndarray
    work_coefficients: np.ndarray
    work_residual_quantiles: np.ndarray
    temperature: float = 1.
    token_bias: float = 0.
    interval_margin: float = 0.
    calibration_status: str = "uncalibrated"
    work_bounds_calibrated: bool = False
    work_target: str = "remaining"

    def design(
        self, observations: Sequence[ReportObservation], embeddings: np.ndarray,
    ) -> np.ndarray:
        raw = np.asarray([row.features(with_events=True) for row in observations])
        numeric = (raw - self.numeric_center) / self.numeric_scale
        semantic = (
            (embeddings - self.embedding_center) @ self.components.T
        ) / self.component_scale
        return np.column_stack((np.ones(len(raw)), numeric, semantic))

    def raw(
        self, observations: Sequence[ReportObservation], embeddings: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        design = self.design(observations, embeddings)
        logits = design @ self.phase_coefficients
        center = design @ self.work_coefficients
        work = np.maximum(0., np.expm1(np.clip(
            center[:, None] + self.work_residual_quantiles, 0., 16.,
        )))
        if self.work_target == "total":
            # Apply bias and nonnegative clipping only after subtracting observed
            # progress, avoiding a positive residual floor near completion.
            work -= np.asarray([row.observed_output_tokens for row in observations])[:, None]
        elif self.work_target != "remaining":
            raise ValueError("unsupported semantic work target")
        return logits, work

    def arrays(
        self, observations: Sequence[ReportObservation], embeddings: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        logits, raw_work = self.raw(observations, embeddings)
        work = np.maximum(0., raw_work + self.token_bias)
        work[:, 0] = np.maximum(0., work[:, 0] - self.interval_margin)
        work[:, 2] += self.interval_margin
        return softmax(logits / self.temperature, axis=1), work

    def predictions(
        self, observations: Sequence[ReportObservation], embeddings: np.ndarray,
    ) -> list[ReportPrediction]:
        if not observations:
            return []
        scores, work = self.arrays(observations, embeddings)
        return [
            ReportPrediction(
                row.request_id, row.invocation_id, row.context_id,
                row.context_epoch, row.ts_ms,
                0. if row.tool_chunk_seen else float(score[2]),
                None if row.tool_chunk_seen else tuple(float(v) for v in bounds),
                "observed_tool" if row.tool_chunk_seen else "unresolved",
                "observed_tool" if row.tool_chunk_seen else PHASES[int(np.argmax(score))],
                0. if row.tool_chunk_seen else float(score[1]),
                score_status=self.calibration_status,
                work_interval_status=(
                    "workflow_split_calibrated_bounds_not_quantiles"
                    if self.work_bounds_calibrated
                    else "training_residual_interval_uncalibrated"
                ),
            )
            for row, score, bounds in zip(observations, scores, work)
        ]

    def save(self, path: Path, *, metadata: dict) -> None:
        values = {
            name: value.tolist() if isinstance(value, np.ndarray) else value
            for name, value in self.__dict__.items()
        }
        path.write_text(json.dumps({
            "schema_version": 1, "physical_action_authorized": False,
            "metadata": metadata, "head": values,
        }, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "SemanticHead":
        raw = json.loads(path.read_text())
        if raw.get("schema_version") != 1:
            raise ValueError("unsupported semantic head schema")
        values = raw["head"]
        for name in (
            "numeric_center", "numeric_scale", "embedding_center", "components",
            "component_scale", "phase_coefficients", "work_coefficients",
            "work_residual_quantiles",
        ):
            values[name] = np.asarray(values[name], dtype=float)
        values["components"] = values["components"].reshape(
            -1, len(values["embedding_center"]),
        )
        return cls(**values)


def fit_head(
    samples: list[dict], embeddings: np.ndarray, *, dimensions: int,
    phase_regularization: float, work_regularization: float,
    work_target: str = "remaining",
) -> SemanticHead:
    if work_target not in ("remaining", "total"):
        raise ValueError("unsupported semantic work target")
    observations = [row["observation"] for row in samples]
    numeric = np.asarray([row.features(with_events=True) for row in observations])
    center, scale = numeric.mean(axis=0), np.maximum(numeric.std(axis=0), .25)
    embedding_center = embeddings.mean(axis=0)
    if dimensions:
        _, _, vectors = np.linalg.svd(embeddings - embedding_center, full_matrices=False)
        components = vectors[:dimensions]
    else:
        components = np.empty((0, embeddings.shape[1]))
    projected = (embeddings - embedding_center) @ components.T
    component_scale = np.maximum(projected.std(axis=0), .01)
    design = np.column_stack((
        np.ones(len(samples)), (numeric - center) / scale, projected / component_scale,
    ))
    weights = workflow_weights([row["task"] for row in samples])
    labels = np.asarray([row["phase_label"] for row in samples])
    class_mass = np.bincount(labels, weights=weights, minlength=len(PHASES))
    class_weights = 1. / np.sqrt(np.maximum(class_mass, 1e-4))
    phase_weights = weights * class_weights[labels]
    phase_weights /= phase_weights.sum()

    def objective(flat: np.ndarray) -> tuple[float, np.ndarray]:
        coefficients = flat.reshape(design.shape[1], len(PHASES))
        logits = design @ coefficients
        value = phase_weights @ (
            logsumexp(logits, axis=1) - logits[np.arange(len(samples)), labels]
        )
        value += .5 * phase_regularization * np.sum(coefficients[1:] ** 2)
        residual = softmax(logits, axis=1)
        residual[np.arange(len(samples)), labels] -= 1.
        gradient = design.T @ (phase_weights[:, None] * residual)
        gradient[1:] += phase_regularization * coefficients[1:]
        return float(value), gradient.ravel()

    initial = np.zeros((design.shape[1], len(PHASES)))
    initial[0] = np.log(np.maximum(class_mass, 1e-5))
    fitted = minimize(
        objective, initial.ravel(), jac=True, method="L-BFGS-B",
        options={"maxiter": 300},
    )
    if not fitted.success:
        raise RuntimeError(f"semantic phase fit failed: {fitted.message}")
    mask = np.asarray([row["remaining_tokens"] is not None for row in samples])
    x = design[mask]
    target = np.log1p([
        row["remaining_tokens"] + (
            row["observation"].observed_output_tokens if work_target == "total" else 0
        )
        for row in samples if row["remaining_tokens"] is not None
    ])
    work_weights = workflow_weights([row["task"] for row in samples if row["remaining_tokens"] is not None])
    ridge = work_regularization * np.eye(design.shape[1])
    ridge[0, 0] = 0.
    work = np.linalg.solve(
        x.T @ (work_weights[:, None] * x) + ridge,
        x.T @ (work_weights * target),
    )
    residuals = target - x @ work
    low, high = np.quantile(residuals, (.1, .9))
    return SemanticHead(
        center, scale, embedding_center, components, component_scale,
        fitted.x.reshape(design.shape[1], len(PHASES)), work,
        np.asarray([min(0., low), 0., max(0., high)]),
        work_target=work_target,
    )


def calibrate_scores_and_bias(
    head: SemanticHead, samples: list[dict], embeddings: np.ndarray,
) -> dict:
    logits, work = head.raw([row["observation"] for row in samples], embeddings)
    labels = np.asarray([row["phase_label"] for row in samples])
    weights = workflow_weights([row["task"] for row in samples])

    def objective(temperature: float) -> float:
        scaled = logits / temperature
        return float(weights @ (
            logsumexp(scaled, axis=1) - scaled[np.arange(len(labels)), labels]
        ))

    fitted = minimize_scalar(objective, bounds=(.25, 8.), method="bounded")
    if not fitted.success:
        raise RuntimeError("temperature calibration failed")
    head.temperature = float(fitted.x)
    residuals = defaultdict(list)
    for row, bounds in zip(samples, work):
        if row["remaining_tokens"] is not None:
            residuals[row["task"]].append(row["remaining_tokens"] - bounds[1])
    if not residuals:
        raise ValueError("score/bias calibration has no final-report work labels")
    head.token_bias = float(np.median([
        np.median(values) for values in residuals.values()
    ]))
    head.calibration_status = "project_disjoint_development_calibration"
    return {
        "temperature": head.temperature, "token_bias": head.token_bias,
        "bias_workflows": len(residuals),
    }


def calibrate_work_bounds(
    head: SemanticHead, samples: list[dict], embeddings: np.ndarray, *, coverage: float,
) -> dict:
    _, work = head.arrays([row["observation"] for row in samples], embeddings)
    scores = defaultdict(list)
    for row, (low, _, high) in zip(samples, work):
        if row["remaining_tokens"] is not None:
            value = row["remaining_tokens"]
            scores[row["task"]].append(max(0., low - value, value - high))
    values = sorted(max(group) for group in scores.values())
    rank = math.ceil((len(values) + 1) * coverage)
    if not 0 < rank <= len(values):
        raise ValueError("insufficient independent calibration workflows for finite bounds")
    head.interval_margin = float(values[rank - 1])
    head.work_bounds_calibrated = True
    return {
        "nominal_workflow_coverage": coverage, "workflow_count": len(values),
        "rank": rank, "added_token_margin": head.interval_margin,
        "assumption": (
            "Workflow clustering handles repeated snapshots. Cross-project "
            "exchangeability is unverified; report measured coverage, not a guarantee."
        ),
    }


def choose_request_threshold(
    samples: list[dict], scores: np.ndarray, *, precision: float, minimum: int,
) -> dict:
    requests = {}
    for row, score in zip(samples, scores):
        rid = row["observation"].request_id
        previous = requests.get(rid, (float("-inf"), row["is_return"]))
        requests[rid] = (max(previous[0], float(score)), row["is_return"])
    choices = []
    for threshold in sorted({score for score, _ in requests.values()}):
        selected = [truth for score, truth in requests.values() if score >= threshold]
        if len(selected) >= minimum and sum(selected) / len(selected) >= precision:
            choices.append((threshold, sum(selected), len(selected)))
    if not choices:
        return {"threshold": None, "status": "no_calibration_operating_point"}
    threshold, true, total = min(choices)
    return {
        "threshold": threshold, "status": "calibration_only_request_operating_point",
        "selected": total, "true": true, "precision": true / total,
        "meaning": "Advisory classification statistic, not a child guard or H2D permit.",
    }
