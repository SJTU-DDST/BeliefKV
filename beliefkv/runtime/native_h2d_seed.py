"""Pinned native submit-to-ACK evidence for cold-start H2D timing only."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


def load_h2d_seed(path: str, sha256: str) -> tuple[tuple[int, float], ...]:
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError("native H2D seed fingerprint changed")
    artifact = json.loads(raw)
    if (
        artifact.get("schema_version") != 1
        or artifact.get("kind") != "native_h2d_ack_seed"
        or artifact.get("timing_boundary") != "native_submit_to_synchronized_ack"
        or artifact.get("model") != "Qwen3.5-35B-A3B"
        or artifact.get("pool_bytes_per_unit") != {"kv": 20480, "mamba": 64389120}
    ):
        raise ValueError("incompatible native H2D seed")
    samples = []
    for row in artifact["samples"]:
        size, elapsed = row["actual_bytes"], row["submit_to_ack_ms"]
        if (
            type(size) is not int or size <= 0
            or type(elapsed) not in (int, float)
            or not math.isfinite(elapsed) or elapsed <= 0
        ):
            raise ValueError("invalid native H2D seed sample")
        samples.append((size, float(elapsed)))
    if len(samples) < 3:
        raise ValueError("insufficient native H2D ACK evidence")
    return tuple(samples)
