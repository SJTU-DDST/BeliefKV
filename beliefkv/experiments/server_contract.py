from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import urllib.request


_DTYPE_ALIASES = {
    "bf16": "bfloat16",
    "torch.bfloat16": "bfloat16",
    "fp16": "float16",
    "half": "float16",
    "torch.float16": "float16",
    "fp32": "float32",
    "torch.float32": "float32",
}


def server_root(base_url: str) -> str:
    root = base_url.rstrip("/")
    return root[:-3] if root.endswith("/v1") else root


def fetch_server_info(base_url: str, *, timeout_s: float = 10.0) -> dict[str, Any]:
    with urllib.request.urlopen(
        f"{server_root(base_url)}/get_server_info",
        timeout=timeout_s,
    ) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("SGLang /get_server_info did not return a JSON object")
    if payload.get("status") not in (None, "ready"):
        raise RuntimeError(f"SGLang server is not ready: {payload.get('status')!r}")
    return payload


def normalize_dtype(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return _DTYPE_ALIASES.get(normalized, normalized)


def resolved_kv_dtype(server_info: dict[str, Any]) -> str:
    configured = normalize_dtype(server_info.get("kv_cache_dtype"))
    if configured in ("", "auto"):
        return normalize_dtype(server_info.get("dtype"))
    return configured


def _canonical_path(value: object) -> str:
    return str(Path(str(value)).expanduser().resolve())


def validate_server_identity(
    server_info: dict[str, Any],
    *,
    expected_model: str,
    expected_model_path: str | Path,
    expected_weight_dtype: str,
    expected_kv_dtype: str,
) -> dict[str, object]:
    actual_model = str(server_info.get("served_model_name") or "")
    actual_path = str(server_info.get("model_path") or "")
    actual_weight_dtype = normalize_dtype(server_info.get("dtype"))
    actual_kv_dtype = resolved_kv_dtype(server_info)
    expected_path = _canonical_path(expected_model_path)
    errors: list[str] = []
    if actual_model != expected_model:
        errors.append(f"served_model_name={actual_model!r}, expected={expected_model!r}")
    if not actual_path or _canonical_path(actual_path) != expected_path:
        errors.append(f"model_path={actual_path!r}, expected={expected_path!r}")
    if actual_weight_dtype != normalize_dtype(expected_weight_dtype):
        errors.append(
            f"weight dtype={actual_weight_dtype!r}, expected="
            f"{normalize_dtype(expected_weight_dtype)!r}"
        )
    if actual_kv_dtype != normalize_dtype(expected_kv_dtype):
        errors.append(
            f"resolved KV dtype={actual_kv_dtype!r}, expected="
            f"{normalize_dtype(expected_kv_dtype)!r}"
        )
    if errors:
        raise RuntimeError("SGLang server identity mismatch: " + "; ".join(errors))
    return {
        "served_model_name": actual_model,
        "model_path": expected_path,
        "weight_dtype": actual_weight_dtype,
        "configured_kv_dtype": normalize_dtype(server_info.get("kv_cache_dtype")),
        "resolved_kv_dtype": actual_kv_dtype,
        "sglang_version": server_info.get("version"),
    }


def capacity_contract(
    server_info: dict[str, Any],
    *,
    kv_bytes_per_token: int | None,
    hbm_safety_margin_bytes: int,
) -> dict[str, object]:
    if (
        kv_bytes_per_token is not None and kv_bytes_per_token <= 0
        or hbm_safety_margin_bytes < 0
    ):
        raise ValueError("capacity constants must be non-negative")
    try:
        pool_tokens = int(server_info["max_total_num_tokens"])
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(
            "SGLang /get_server_info omitted a valid max_total_num_tokens"
        ) from error
    if pool_tokens <= 0:
        raise RuntimeError("SGLang reported a non-positive KV pool capacity")
    internal_states = server_info.get("internal_states") or ()
    memory_usage = (
        internal_states[0].get("memory_usage", {})
        if internal_states and isinstance(internal_states[0], dict)
        else {}
    )
    return {
        "max_total_num_tokens": pool_tokens,
        "kv_bytes_per_token": kv_bytes_per_token,
        "kv_pool_bytes": (
            pool_tokens * kv_bytes_per_token
            if kv_bytes_per_token is not None else None
        ),
        "capacity_accounting": (
            "native_pool_tokens_only" if kv_bytes_per_token is None
            else "legacy_scalar_kv_bytes"
        ),
        "hbm_safety_margin_bytes": hbm_safety_margin_bytes,
        "context_length": int(server_info.get("context_length") or 0),
        "max_running_requests": int(server_info.get("max_running_requests") or 0),
        "page_size": int(server_info.get("page_size") or 0),
        "prefill_chunk_size": int(server_info.get("chunked_prefill_size") or 0),
        "max_prefill_tokens": int(server_info.get("max_prefill_tokens") or 0),
        "host_pool_gib": float(server_info.get("hicache_size") or 0.0),
        "attention_backend": server_info.get("attention_backend"),
        "sampling_backend": server_info.get("sampling_backend"),
        "reported_memory_usage_gib": memory_usage,
    }


def validate_native_pool_census(
    census: dict[str, Any],
    *,
    scheduler_pid: int,
    pool_tokens: int,
    host_budget_gb: int,
    full_bytes_per_token: int,
    gpu_total_bytes: int,
) -> dict[str, int]:
    """Validate default separately allocated FULL/MAMBA device/Host pools."""
    if (
        census.get("schema_version") != 1
        or census.get("source") != "native_sglang_v0520"
        or census.get("scheduler_pid") != scheduler_pid
    ):
        raise RuntimeError("native pool census does not belong to this scheduler")
    raw = census.get("capacity")
    if not isinstance(raw, dict) or raw.get("pool_layout") != "static_separate_full_mamba":
        raise RuntimeError("native FULL/MAMBA pool census is unavailable")
    names = (
        "device_full_tokens", "device_mamba_slots", "device_full_bytes",
        "device_mamba_bytes", "device_total_bytes",
        "host_full_tokens", "host_mamba_slots", "host_full_bytes",
        "host_mamba_bytes", "host_total_bytes",
    )
    if any(type(raw.get(name)) is not int or raw[name] <= 0 for name in names):
        raise RuntimeError("native pool census has missing or invalid geometry")
    values = {name: raw[name] for name in names}
    if (
        pool_tokens <= 0 or host_budget_gb <= 0 or full_bytes_per_token <= 0
        or gpu_total_bytes <= 0
        or values["device_full_tokens"] < pool_tokens
        or values["device_full_bytes"] < pool_tokens * full_bytes_per_token
        or values["device_full_bytes"] > (values["device_full_tokens"] + 4096) * full_bytes_per_token
        or values["device_full_bytes"] + values["device_mamba_bytes"]
        != values["device_total_bytes"]
        or values["device_total_bytes"] >= gpu_total_bytes
        or values["host_full_bytes"] + values["host_mamba_bytes"]
        != values["host_total_bytes"]
        or abs(values["host_total_bytes"] - host_budget_gb * 1_000_000_000)
        > host_budget_gb * 10_000_000
    ):
        raise RuntimeError("native pool census disagrees with runtime capacity")
    return values


def validate_native_reactive_v0520(
    server_info: dict[str, Any],
    *,
    expected_model: str,
    expected_model_path: str | Path,
    expected_weight_dtype: str,
    expected_kv_dtype: str,
) -> dict[str, object]:
    """Freeze native Host restore, not an unverified BeliefKV action path."""
    identity = validate_server_identity(
        server_info,
        expected_model=expected_model,
        expected_model_path=expected_model_path,
        expected_weight_dtype=expected_weight_dtype,
        expected_kv_dtype=expected_kv_dtype,
    )
    size = server_info.get("hicache_size")
    tp = server_info.get("tp_size", server_info.get("tensor_parallel_size"))
    alternate_tp = server_info.get("tensor_parallel_size")
    if (
        identity["sglang_version"] != "0.5.20"
        or server_info.get("enable_hierarchical_cache") is not True
        or type(size) not in (int, float) or size <= 0
        or server_info.get("hicache_write_policy") not in (
            "write_back", "write_through",
        )
        or server_info.get("enable_beliefkv") is True
        or server_info.get("enable_beliefkv_admission") is True
        or server_info.get("beliefkv_admission_prefetch") is True
        or type(tp) is not int or tp != 1
        or alternate_tp is not None and alternate_tp != tp
    ):
        raise RuntimeError(
            "native reactive collection requires v0.5.20 single-rank HiCache "
            "with BeliefKV scheduling and predictive physical actions disabled"
        )
    return identity
