import hashlib
import json

import pytest

from beliefkv.runtime.native_h2d_seed import load_h2d_seed


def test_h2d_seed_accepts_only_pinned_measured_geometry(tmp_path):
    path = tmp_path / "seed.json"
    artifact = {
        "schema_version": 1, "kind": "native_h2d_ack_seed",
        "model": "Qwen3.5-35B-A3B",
        "pool_bytes_per_unit": {"kv": 20480, "mamba": 64389120},
        "timing_boundary": "native_submit_to_synchronized_ack",
        "samples": [{"actual_bytes": 64389120, "submit_to_ack_ms": 60.}] * 3,
    }
    path.write_text(json.dumps(artifact))
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    assert load_h2d_seed(str(path), sha) == ((64389120, 60.),) * 3
    with pytest.raises(ValueError, match="fingerprint"):
        load_h2d_seed(str(path), "0" * 64)
    artifact["pool_bytes_per_unit"]["mamba"] = 10
    path.write_text(json.dumps(artifact))
    with pytest.raises(ValueError, match="incompatible"):
        load_h2d_seed(str(path), hashlib.sha256(path.read_bytes()).hexdigest())
