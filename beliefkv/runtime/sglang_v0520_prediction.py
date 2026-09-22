"""Short-lived, identity-bound admission demand hints for native v0.5.20.

These hints reorder existing, visible requests. They never authorize an
allocator reservation, a retraction, or a physical transfer.
"""

from __future__ import annotations

from dataclasses import dataclass
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


def validate_admission_artifact(
    artifact_path: str,
    *,
    expected_sha256: str,
    model_path: str,
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
