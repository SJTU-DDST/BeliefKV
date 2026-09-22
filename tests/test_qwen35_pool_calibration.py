from __future__ import annotations

import pytest

from beliefkv.experiments.server_contract import validate_native_pool_census


def _census() -> dict:
    return {
        "schema_version": 1,
        "source": "native_sglang_v0520",
        "scheduler_pid": 123,
        "capacity": {
            "pool_layout": "static_separate_full_mamba",
            "device_full_tokens": 192,
            "device_mamba_slots": 38,
            "device_full_bytes": 1552,
            "device_mamba_bytes": 760,
            "device_total_bytes": 2312,
            "host_full_tokens": 128,
            "host_mamba_slots": 64,
            "host_full_bytes": 512_000_000,
            "host_mamba_bytes": 488_000_000,
            "host_total_bytes": 1_000_000_000,
        },
    }


def _validate(census: dict) -> dict:
    return validate_native_pool_census(
        census, scheduler_pid=123, pool_tokens=190,
        host_budget_gb=1, full_bytes_per_token=8, gpu_total_bytes=3200,
    )


def test_static_device_pools_are_additive() -> None:
    result = _validate(_census())
    assert result["device_full_bytes"] + result["device_mamba_bytes"] == result["device_total_bytes"]


@pytest.mark.parametrize("field,value", [
    ("device_total_bytes", 4000),
    ("device_full_tokens", 100),
    ("host_mamba_bytes", 400_000_000),
    ("host_total_bytes", 700_000_000),
    ("device_mamba_slots", None),
])
def test_census_must_match_live_allocator_and_budget(field: str, value: object) -> None:
    census = _census()
    census["capacity"][field] = value
    with pytest.raises(RuntimeError):
        _validate(census)


def test_census_must_belong_to_live_scheduler() -> None:
    census = _census()
    census["scheduler_pid"] = 124
    with pytest.raises(RuntimeError, match="scheduler"):
        _validate(census)
