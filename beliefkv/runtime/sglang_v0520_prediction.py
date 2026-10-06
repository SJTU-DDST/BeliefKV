"""Short-lived, identity-bound admission demand hints for native v0.5.20.

These hints reorder existing, visible requests. They never authorize an
allocator reservation, a retraction, or a physical transfer.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Mapping

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey


PREDICTION_ATTRIBUTE = "beliefkv_native_admission_prediction"
MAX_HINT_AGE_MS = 10_000.0
MAX_OUTPUT_TOKENS = 131_072
MAX_TOOL_WAIT_MS = 3_600_000.0
_MODEL_MANIFEST_FILES = frozenset((
    "config.json",
    "model.safetensors.index.json",
    "tokenizer.json",
    "tokenizer_config.json",
))


def validate_tool_timing_artifact(
    artifact_path: str, *, expected_sha256: str, model_path: str,
) -> None:
    """Validate event-time evidence; never promote legacy action eligibility."""
    data = Path(artifact_path).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("tool timing artifact SHA-256 mismatch")
    raw = json.loads(data)
    metadata = raw.get("metadata") or {}
    timing = metadata.get("native_event_timing_report") or {}
    if (
        metadata.get("calibration_status") not in ("calibrated_native_heads_only", "calibrated")
        or timing.get("target") != "time_until_all_active_tools_return_not_first_gpu_service"
        or not (raw.get("components") or {}).get("operational_release")
        or not 0 < raw.get("calibration_coverage", 0) <= 1
    ):
        raise ValueError("tool predictor lacks calibrated event-time evidence")
    datasets = metadata.get("dataset_dirs") or []
    hashes = metadata.get("dataset_manifest_file_sha256s") or []
    if not datasets or len(datasets) != len(hashes):
        raise ValueError("tool timing predictor has no pinned source manifests")
    for dataset, expected in zip(datasets, hashes):
        manifest_data = (Path(dataset) / "dataset_manifest.json").read_bytes()
        if hashlib.sha256(manifest_data).hexdigest() != expected:
            raise ValueError("tool timing source manifest changed")
        contract = json.loads(manifest_data)["source"]["runtime_environment_contract"]
        identity = contract.get("server_identity") or {}
        model_hashes = contract.get("model_revision_sha256") or {}
        if identity.get("sglang_version") != "0.5.20" or "config.json" not in model_hashes:
            raise ValueError("tool timing model belongs to a different runtime")
        if set(model_hashes) - _MODEL_MANIFEST_FILES:
            raise ValueError("unsupported tool timing model manifest")
        for name, digest in model_hashes.items():
            if hashlib.sha256((Path(model_path) / name).read_bytes()).hexdigest() != digest:
                raise ValueError("tool timing model revision changed")


def validate_admission_artifact(
    artifact_path: str,
    *,
    expected_sha256: str,
    model_path: str,
    require_physical_actions: bool = False,
) -> None:
    """Admission demand may only come from a calibrated model for this stack."""
    source = Path(artifact_path).resolve()
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("admission predictor artifact SHA-256 mismatch")
    raw = json.loads(data)
    if type(raw) is not dict or type(raw.get("metadata")) is not dict:
        raise ValueError("invalid admission predictor artifact")
    metadata = raw["metadata"]
    if metadata.get("calibration_status") != "calibrated" or metadata.get("online_eligible") is not True:
        raise ValueError("admission predictor is not calibrated and online eligible")
    if require_physical_actions and metadata.get("predictive_action_eligible") is not True:
        raise ValueError("admission-only predictor cannot authorize physical actions")
    model_root = Path(model_path).resolve()
    sources = metadata.get("semantic_source_runtime_environment_contracts")
    if not isinstance(sources, list) or not sources:
        raise ValueError("admission predictor has no semantic source contract")
    for contract in sources:
        if not isinstance(contract, dict):
            raise ValueError("invalid admission predictor source contract")
        hashes = contract.get("model_revision_sha256")
        identity = contract.get("server_identity")
        if (
            not isinstance(hashes, dict)
            or not isinstance(identity, dict)
            or "config.json" not in hashes
            or set(hashes) - _MODEL_MANIFEST_FILES
            or identity.get("sglang_version") != "0.5.20"
        ):
            raise ValueError("admission predictor belongs to another model/runtime")
        for filename, expected in hashes.items():
            if (
                type(expected) is not str
                or len(expected) != 64
                or any(c not in "0123456789abcdef" for c in expected)
                or hashlib.sha256((model_root / filename).read_bytes()).hexdigest()
                != expected
            ):
                raise ValueError("admission predictor model file SHA-256 mismatch")


@dataclass(frozen=True)
class NativeDemandHint:
    key: PrefillCandidateKey
    next_output_tokens: int
    issued_monotonic_ms: float
    expires_monotonic_ms: float
    predictor_sha256: str
    invocation_revision_ts_ms: float | None = None

    def live(self, key: PrefillCandidateKey, *, now_ms: float) -> bool:
        return self.key == key and self.issued_monotonic_ms <= now_ms < self.expires_monotonic_ms


@dataclass(frozen=True)
class NativeToolWaitHint:
    key: PrefillCandidateKey
    wait_p10_ms: float
    wait_p50_ms: float
    wait_p90_ms: float
    issued_monotonic_ms: float
    expires_monotonic_ms: float
    predictor_sha256: str
    invocation_revision_ts_ms: float | None = None
    release_cdf: tuple[tuple[float, float], ...] = ()

    def live(self, key: PrefillCandidateKey, *, now_ms: float) -> bool:
        return self.key == key and self.issued_monotonic_ms <= now_ms < self.expires_monotonic_ms

    @cached_property
    def _release_curve(self):
        if not self.release_cdf:
            return None
        from beliefkv.predictor.action_frontier import ActionTimingCurve

        return ActionTimingCurve(
            tuple(point[0] for point in self.release_cdf),
            tuple(point[1] for point in self.release_cdf), "pooled", 0.,
        )

    def release_probability_within(self, horizon_ms: float, *, now_ms: float) -> float | None:
        curve = self._release_curve
        if curve is None:
            return None
        age = max(0., now_ms - self.issued_monotonic_ms)
        past = curve.release_within(age)
        if past >= 1. - 1e-9:
            return None
        future = curve.release_within(age + max(0., horizon_ms))
        return max(0., min(1., (future - past) / max(1. - past, 1e-9)))

    def remaining_quantile(self, quantile: float, *, now_ms: float) -> float | None:
        """Condition residual work on the tool still being active at this age."""
        if not math.isfinite(quantile) or not 0 < quantile < 1:
            raise ValueError("tool timing quantile must be strictly between zero and one")
        age = max(0., now_ms - self.issued_monotonic_ms)
        curve = self._release_curve
        if curve is not None:
            past = curve.release_within(age)
            if past >= 1. - 1e-9:
                return None
            finish = curve.quantile(past + quantile * (1. - past))
            return max(0., finish - age) if finish is not None else None
        values = {.1: self.wait_p10_ms, .5: self.wait_p50_ms, .9: self.wait_p90_ms}
        if quantile not in values:
            raise ValueError("quantile-only hint supports P10/P50/P90")
        remaining = values[quantile] - age
        return remaining if remaining > 0 else None


@dataclass(frozen=True)
class NativeJoinWaitHint:
    key: PrefillCandidateKey
    join_id: str
    join_mode: str
    member_ids: tuple[str, ...]
    child_revisions: tuple[tuple[str, float, str, int], ...]
    wait_p10_ms: float
    wait_p50_ms: float
    wait_p90_ms: float
    issued_monotonic_ms: float
    expires_monotonic_ms: float
    predictor_sha256: str
    invocation_revision_ts_ms: float

    def live(self, key: PrefillCandidateKey, *, now_ms: float) -> bool:
        return self.key == key and self.issued_monotonic_ms <= now_ms < self.expires_monotonic_ms


def _id(raw: Mapping[str, object], field: str) -> str:
    value = raw.get(field)
    if type(value) is not str or not value:
        raise ValueError(f"invalid prediction {field}")
    return value


def _optional_id(raw: Mapping[str, object], field: str) -> str | None:
    value = raw.get(field)
    if value is not None and (type(value) is not str or not value):
        raise ValueError(f"invalid prediction {field}")
    return value


def parse_native_demand_hint(
    event: RuntimeEvent,
    raw: Mapping[str, object],
    *,
    expected_sha256: str,
    now_ms: float | None = None,
) -> NativeDemandHint:
    """Reject stale, unsupported and cross-context predictions before use."""
    if event.kind is not RuntimeEventKind.STRUCTURED_ACTION:
        raise ValueError("admission prediction must use structured_action")
    if type(raw) is not dict:
        raise ValueError("admission prediction must be an object")
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
        raise ValueError("admission predictor must be pinned by SHA-256")
    if _id(raw, "predictor_sha256") != expected_sha256:
        raise ValueError("admission predictor fingerprint mismatch")
    workflow = _id(raw, "root_workflow_id")
    invocation = _id(raw, "invocation_id")
    context = _id(raw, "context_id")
    epoch = raw.get("context_epoch")
    attempt = raw.get("attempt_id")
    output = raw.get("next_output_tokens")
    generation = raw.get("session_generation")
    session = _optional_id(raw, "session_id")
    if (
        type(epoch) is not int or epoch < 0
        or type(attempt) is not int or attempt < 0
        or type(output) is not int or not 0 < output <= MAX_OUTPUT_TOKENS
        or (generation is not None and (
            type(generation) is not int or generation < 0 or session is None
        ))
        or (workflow, invocation, context, epoch)
        != (event.workflow_id, event.invocation_id, event.context_id, event.context_epoch)
    ):
        raise ValueError("admission prediction identity or demand invalid")
    issued = raw.get("issued_monotonic_ms")
    expires = raw.get("expires_monotonic_ms")
    if (
        type(issued) not in (int, float)
        or type(expires) not in (int, float)
        or not math.isfinite(issued)
        or not math.isfinite(expires)
    ):
        raise ValueError("invalid admission prediction clock")
    now = time.monotonic() * 1000 if now_ms is None else now_ms
    if (
        issued > now + 100.0
        or expires <= now
        or expires <= issued
        or expires - issued > MAX_HINT_AGE_MS
    ):
        raise ValueError("stale or excessive admission prediction lifetime")
    return NativeDemandHint(
        key=PrefillCandidateKey(
            _id(raw, "request_id"),
            workflow,
            invocation,
            context,
            epoch,
            attempt,
            session,
            generation,
        ),
        next_output_tokens=output,
        issued_monotonic_ms=float(issued),
        expires_monotonic_ms=float(expires),
        predictor_sha256=expected_sha256,
    )
