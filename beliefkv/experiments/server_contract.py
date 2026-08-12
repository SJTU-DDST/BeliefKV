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
    kv_bytes_per_token: int,
    hbm_safety_margin_bytes: int,
) -> dict[str, object]:
    if kv_bytes_per_token <= 0 or hbm_safety_margin_bytes < 0:
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
        "kv_pool_bytes": pool_tokens * kv_bytes_per_token,
        "hbm_safety_margin_bytes": hbm_safety_margin_bytes,
        "context_length": int(server_info.get("context_length") or 0),
        "max_running_requests": int(server_info.get("max_running_requests") or 0),
        "page_size": int(server_info.get("page_size") or 0),
        "prefill_chunk_size": int(server_info.get("chunked_prefill_size") or 0),
        "host_pool_gib": float(server_info.get("hicache_size") or 0.0),
        "attention_backend": server_info.get("attention_backend"),
        "sampling_backend": server_info.get("sampling_backend"),
        "reported_memory_usage_gib": memory_usage,
    }
