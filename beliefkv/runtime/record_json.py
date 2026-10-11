"""Compact strict JSON for background telemetry records."""

from __future__ import annotations

import ujson


def encode_record(value: object) -> str:
    return ujson.dumps(
        value, ensure_ascii=True, escape_forward_slashes=False, allow_nan=False,
    )
