import json

import pytest

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
