"""Compact strict JSON for background telemetry records."""

from __future__ import annotations

import ujson
from numpy import bool_, floating, integer


def _numpy_scalar(value: object) -> bool | int | float:
    if isinstance(value, bool_):
        return bool(value)
    if isinstance(value, integer):
        return int(value)
    if isinstance(value, floating):
        return float(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def encode_record(value: object) -> str:
    return ujson.dumps(
        value, ensure_ascii=True, escape_forward_slashes=False, allow_nan=False,
        default=_numpy_scalar,
    )
