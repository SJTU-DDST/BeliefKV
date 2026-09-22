from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from beliefkv.experiments.server_contract import (
    capacity_contract,
    fetch_server_info,
    resolved_kv_dtype,
    validate_server_identity,
    validate_native_reactive_v0520,
)


def _server_info(model_path: Path) -> dict[str, object]:
    return {
        "status": "ready",
        "served_model_name": "Qwen3-Coder-30B-A3B-Instruct",
        "model_path": str(model_path),
        "dtype": "bfloat16",
        "kv_cache_dtype": "auto",
        "max_total_num_tokens": 800_000,
        "context_length": 262_144,
        "max_running_requests": 16,
        "page_size": 1,
        "chunked_prefill_size": 4096,
        "hicache_size": 96,
        "version": "0.5.2rc1",
        "internal_states": [
            {
                "memory_usage": {
                    "weight": 57.0,
                    "kvcache": 73.2,
                    "cuda_graph": 2.0,
                    "token_capacity": 800_000,
                }
            }
        ],
    }


def test_auto_kv_dtype_resolves_to_bfloat16(tmp_path: Path) -> None:
    info = _server_info(tmp_path / "model")
    assert resolved_kv_dtype(info) == "bfloat16"
    identity = validate_server_identity(
        info,
        expected_model="Qwen3-Coder-30B-A3B-Instruct",
        expected_model_path=tmp_path / "model",
        expected_weight_dtype="bfloat16",
        expected_kv_dtype="bfloat16",
    )
    assert identity["configured_kv_dtype"] == "auto"
    assert identity["resolved_kv_dtype"] == "bfloat16"


def test_server_identity_rejects_wrong_model_path(tmp_path: Path) -> None:
    info = _server_info(tmp_path / "wrong")
    with pytest.raises(RuntimeError, match="model_path"):
        validate_server_identity(
            info,
            expected_model="Qwen3-Coder-30B-A3B-Instruct",
            expected_model_path=tmp_path / "expected",
            expected_weight_dtype="bfloat16",
            expected_kv_dtype="bfloat16",
        )


def test_server_identity_rejects_auto_resolving_to_float16(tmp_path: Path) -> None:
    info = _server_info(tmp_path / "model")
    info["dtype"] = "float16"
    with pytest.raises(RuntimeError, match="resolved KV dtype"):
        validate_server_identity(
            info,
            expected_model="Qwen3-Coder-30B-A3B-Instruct",
            expected_model_path=tmp_path / "model",
            expected_weight_dtype="float16",
            expected_kv_dtype="bfloat16",
        )


def test_capacity_contract_records_physical_budget(tmp_path: Path) -> None:
    contract = capacity_contract(
        _server_info(tmp_path / "model"),
        kv_bytes_per_token=98_304,
        hbm_safety_margin_bytes=1_073_741_824,
    )
    assert contract["max_total_num_tokens"] == 800_000
    assert contract["kv_pool_bytes"] == 800_000 * 98_304
    assert contract["host_pool_gib"] == 96.0
    assert contract["page_size"] == 1


def test_hybrid_reactive_capacity_does_not_invent_scalar_kv_bytes(tmp_path: Path) -> None:
    info = _server_info(tmp_path / "model")
    info.update({
        "version": "0.5.20",
        "enable_hierarchical_cache": True,
        "hicache_write_policy": "write_back",
        "enable_beliefkv": False,
        "enable_beliefkv_admission": False,
        "beliefkv_admission_prefetch": False,
        "tp_size": 1,
    })
    identity = validate_native_reactive_v0520(
        info,
        expected_model="Qwen3-Coder-30B-A3B-Instruct",
        expected_model_path=tmp_path / "model",
        expected_weight_dtype="bfloat16",
        expected_kv_dtype="bfloat16",
    )
    assert identity["sglang_version"] == "0.5.20"
    capacity = capacity_contract(
        info, kv_bytes_per_token=None, hbm_safety_margin_bytes=1_024,
    )
    assert capacity["kv_pool_bytes"] is None
    assert capacity["kv_bytes_per_token"] is None
    assert capacity["max_total_num_tokens"] == 800_000
    for key, value in (
        ("enable_hierarchical_cache", False),
        ("enable_beliefkv_admission", True),
        ("beliefkv_admission_prefetch", True),
        ("tp_size", 2),
        ("tensor_parallel_size", 2),
        ("version", "0.5.2rc1"),
        ("hicache_size", 0),
    ):
        invalid = {**info, key: value}
        with pytest.raises(RuntimeError, match="native reactive"):
            validate_native_reactive_v0520(
                invalid,
                expected_model="Qwen3-Coder-30B-A3B-Instruct",
                expected_model_path=tmp_path / "model",
                expected_weight_dtype="bfloat16",
                expected_kv_dtype="bfloat16",
            )


def test_fetch_server_info_rejects_non_ready_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = io.BytesIO(json.dumps({"status": "loading"}).encode("utf-8"))
    monkeypatch.setattr(
        "beliefkv.experiments.server_contract.urllib.request.urlopen",
        lambda *_args, **_kwargs: payload,
    )
    with pytest.raises(RuntimeError, match="not ready"):
        fetch_server_info("http://127.0.0.1:18000/v1")
