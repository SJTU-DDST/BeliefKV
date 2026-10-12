import json

import pytest
import numpy as np

from beliefkv.runtime.record_json import encode_record


def test_record_encoding_preserves_nested_telemetry_values():
    row = {
        "event": "gpu_service_sample", "ts_ms": 1791731759799.0525,
        "duration": -0.0, "bytes": 2 ** 63 - 1, "empty": None,
        "units": {0: 1, 2: 3}, "tuple": (True, False, 0.012345678901234567),
        "text": "\u5b50\u4efb\u52a1/\u7ed3\u675f\n\"\U0001f642",
    }
    encoded = encode_record(row)
    assert json.loads(encoded) == json.loads(json.dumps(row, allow_nan=False))
    assert "\\/" not in encoded
    assert encoded.isascii()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_record_encoding_rejects_nonfinite_nested_measurements(value):
    with pytest.raises((ValueError, OverflowError)):
        encode_record({"measurements": [None, {"duration_ms": value}]})


def test_record_encoding_preserves_numpy_capacity_diagnostics_as_native_types():
    free = np.int64(8)
    row = {
        "event": "wait_prefetch_plan",
        "fits_current_free_lists": free >= 4,
        "capacity_reclaim": {
            "full_target_satisfied": free >= 16,
            "free_full_tokens_after": free,
            "ratio": np.float32(.5),
            "bytes": np.uint64(2 ** 64 - 1),
        },
    }
    encoded = json.loads(encode_record(row))
    assert encoded["fits_current_free_lists"] is True
    assert encoded["capacity_reclaim"]["full_target_satisfied"] is False
    assert encoded["capacity_reclaim"]["free_full_tokens_after"] == 8
    assert encoded["capacity_reclaim"]["bytes"] == 2 ** 64 - 1
    assert encoded["capacity_reclaim"]["ratio"] == .5


@pytest.mark.parametrize("value", (
    np.float32("nan"), np.float32("inf"), np.float64("-inf"),
))
def test_record_encoding_still_rejects_nonfinite_numpy_measurements(value):
    with pytest.raises((ValueError, OverflowError)):
        encode_record({"measurements": [{"duration_ms": value}]})


def test_record_encoding_does_not_expand_numpy_arrays_into_telemetry():
    with pytest.raises(TypeError, match="ndarray"):
        encode_record({"unexpected_array": np.zeros(4)})
