"""Size/shape-local native transfer timing, separate from action profitability."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from typing import Iterable


@dataclass(frozen=True)
class NativeServiceSample:
    size_bytes: int
    submit_to_ack_ms: float
    direction: str = "h2d"
    shape: str = "unknown"
    enqueue_to_submit_ms: float | None = None

    def __post_init__(self) -> None:
        if (
            type(self.size_bytes) is not int or self.size_bytes <= 0
            or not math.isfinite(self.submit_to_ack_ms) or self.submit_to_ack_ms <= 0
            or self.direction not in ("h2d", "d2h")
            or self.shape not in ("unknown", "full", "mamba", "hybrid")
            or self.enqueue_to_submit_ms is not None and (
                not math.isfinite(self.enqueue_to_submit_ms) or self.enqueue_to_submit_ms < 0
            )
        ):
            raise ValueError("invalid native transfer service sample")


@dataclass(frozen=True)
class NativeServiceEstimate:
    submit_to_ack_p50_ms: float
    submit_to_ack_p90_ms: float
    enqueue_to_submit_p90_ms: float | None
    sample_count: int
    support: str


def pool_shape(full_units: int, mamba_units: int) -> str:
    if full_units and mamba_units:
        return "hybrid"
    if mamba_units:
        return "mamba"
    return "full" if full_units else "unknown"


def percentile(values: Iterable[float], level: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("empty timing sample")
    position = (len(ordered) - 1) * level
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def estimate_native_service(
    samples: Iterable[NativeServiceSample | tuple[int, float]],
    size_bytes: int,
    *,
    direction: str = "h2d",
    shape: str = "unknown",
    minimum: int = 3,
) -> NativeServiceEstimate | None:
    """Do not multiply polling/fixed overhead by a transfer's byte ratio."""
    if size_bytes <= 0:
        return None
    compatible = []
    for item in samples:
        sample = item if isinstance(item, NativeServiceSample) else NativeServiceSample(*item)
        if sample.direction != direction:
            continue
        distance = abs(math.log2(size_bytes / sample.size_bytes))
        if distance <= 2.:
            compatible.append((sample, distance))
    exact = [(row, distance) for row, distance in compatible if row.shape == shape]
    if len(exact) >= minimum:
        compatible = exact
        support = "matched_pool_shape_and_size"
    else:
        compatible = [
            (row, distance) for row, distance in compatible
            if row.shape in ("unknown", shape) or shape == "unknown"
        ]
        support = "nearby_size_with_legacy_shape"
    if len(compatible) < minimum:
        return None
    chosen = sorted(compatible, key=lambda pair: pair[1])[:16]
    # Estimate only the incremental byte cost; the ACK/polling overhead is fixed.
    timings = [
        row.submit_to_ack_ms + max(0, size_bytes - row.size_bytes) / 24_000_000.
        for row, _ in chosen
    ]
    queues = [
        row.enqueue_to_submit_ms for row, _ in chosen
        if row.enqueue_to_submit_ms is not None
    ]
    return NativeServiceEstimate(
        float(median(timings)), percentile(timings, .9),
        percentile(queues, .9) if queues else None, len(chosen), support,
    )


def load_native_service_seed(path: str, sha256: str) -> tuple[NativeServiceSample, ...]:
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError("native service seed fingerprint changed")
    raw = json.loads(data)
    if (
        raw.get("schema_version") != 1 or raw.get("kind") != "native_transfer_service_seed"
        or raw.get("pool_bytes_per_unit") != {"kv": 20480, "mamba": 64389120}
        or raw.get("model") != "Qwen3.5-35B-A3B"
        or raw.get("scope") != "controller_completed_payload_timing_only"
    ):
        raise ValueError("incompatible native service seed")
    return tuple(NativeServiceSample(**row) for row in raw["samples"])
