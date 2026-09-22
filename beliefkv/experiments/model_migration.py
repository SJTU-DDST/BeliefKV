"""Static model geometry and target-profile audit for SGLang v0.5.20."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from beliefkv.experiments.server_contract import normalize_dtype


TARGET_SGLANG_VERSION = "0.5.20"
_FULL = {"full_attention", "attention"}
_STATE = {"linear_attention", "mamba", "mamba2"}


def _positive_int(config: dict[str, Any], name: str) -> int:
    value = config.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _object(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def inspect_model_config(config: dict[str, Any]) -> dict[str, Any]:
    """Compute BF16 full-attention KV only; hybrid physical state is not estimated."""
    if not isinstance(config, dict):
        raise ValueError("model config must be a JSON object")
    text = config.get("text_config")
    if text is not None and not isinstance(text, dict):
        raise ValueError("text_config must be a JSON object")
    model = text if text is not None else config
    layers = _positive_int(model, "num_hidden_layers")
    heads = _positive_int(model, "num_key_value_heads")
    dim = _positive_int(model, "head_dim")
    interval = (
        _positive_int(model, "full_attention_interval")
        if "full_attention_interval" in model
        else None
    )
    types = model.get("layer_types")
    if types is not None:
        if (
            not isinstance(types, list)
            or len(types) != layers
            or any(not isinstance(item, str) or item not in _FULL | _STATE for item in types)
        ):
            raise ValueError("layer_types must list a known type for every layer")
        full_ids = [i for i, kind in enumerate(types) if kind in _FULL]
        if interval is not None:
            expected = list(range(interval - 1, layers, interval))
            if full_ids != expected:
                raise ValueError("layer_types contradict full_attention_interval")
        state_counts = {
            "linear_attention": types.count("linear_attention"),
            "mamba": types.count("mamba") + types.count("mamba2"),
        }
    elif interval is not None:
        full_ids = list(range(interval - 1, layers, interval))
        # The interval specifies full layers, not the physical representation
        # of the other layers. Do not silently assign them an attention KV size.
        state_counts = {"linear_attention": None, "mamba": None}
    else:
        if any("mamba" in key or "linear_" in key for key in model) or any(
            marker in str(model.get("model_type", "")).lower()
            for marker in ("qwen3_5", "qwen3_next", "mamba")
        ):
            raise ValueError("hybrid model lacks layer_types/full_attention_interval")
        full_ids = list(range(layers))
        state_counts = {"linear_attention": 0, "mamba": 0}

    if not full_ids:
        raise ValueError("model has no full-attention layers for a BF16 KV estimate")
    state_layers = layers - len(full_ids)
    hybrid = state_layers > 0
    return {
        "config_section": "text_config" if text is not None else "root",
        "num_hidden_layers": layers,
        "num_key_value_heads": heads,
        "head_dim": dim,
        "full_attention_interval": interval,
        "full_attention_layer_indices": full_ids,
        "full_attention_layers": len(full_ids),
        "state_layers": state_layers,
        "state_layer_counts": state_counts,
        "hybrid_linear_or_mamba": hybrid,
        "bf16_full_attention_kv_bytes_per_token": len(full_ids) * 2 * heads * dim * 2,
        "full_attention_kv_formula": "full_attention_layers * 2(K,V) * num_key_value_heads * head_dim * 2(BF16 bytes)",
        "physical_state_capacity_bytes": None,
        "physical_state_capacity_verified": False,
        "unsupported_for_legacy_page_index": hybrid,
    }


def check_target_profile(
    geometry: dict[str, Any],
    profile: dict[str, Any] | None,
    *,
    model_directory: str | Path | None = None,
) -> dict[str, Any]:
    """Check static profile geometry only, never certify runtime hooks or pool size."""
    reasons: list[str] = []
    if geometry["unsupported_for_legacy_page_index"]:
        reasons.append("hybrid linear/Mamba state cannot use the legacy page index")
    if profile is None:
        reasons.append("SGLang v0.5.20 target profile was not provided")
    elif not isinstance(profile, dict):
        reasons.append("target profile must be a JSON object")
    else:
        runtime = profile.get("runtime")
        model = profile.get("model")
        capacity = profile.get("capacity")
        if not all(isinstance(part, dict) for part in (runtime, model, capacity)):
            reasons.append("target profile requires runtime, model and capacity objects")
        else:
            if runtime.get("sglang_version") != TARGET_SGLANG_VERSION:
                reasons.append("target runtime must be SGLang v0.5.20")
            if runtime.get("page_size") != 1:
                reasons.append("legacy page index requires page_size=1")
            kv_dtype = normalize_dtype(model.get("kv_cache_dtype"))
            cli_dtype = normalize_dtype(runtime.get("kv_cache_cli_dtype"))
            weight_dtype = normalize_dtype(model.get("weight_dtype"))
            if kv_dtype != "bfloat16" or not (
                cli_dtype == "bfloat16"
                or cli_dtype == "auto" and weight_dtype == "bfloat16"
            ):
                reasons.append("profile must resolve KV cache dtype to BF16")
            if model_directory is not None:
                path = model.get("path")
                if not isinstance(path, str) or Path(path).expanduser().resolve() != Path(
                    model_directory
                ).expanduser().resolve():
                    reasons.append("profile model.path differs from inspected model directory")
            if not geometry["hybrid_linear_or_mamba"]:
                expected = geometry["bf16_full_attention_kv_bytes_per_token"]
                if capacity.get("kv_bytes_per_token") != expected:
                    reasons.append("capacity.kv_bytes_per_token differs from model BF16 KV")
                tokens = capacity.get("max_total_tokens")
                pool = capacity.get("kv_pool_bytes")
                if (
                    isinstance(tokens, bool)
                    or not isinstance(tokens, int)
                    or tokens <= 0
                    or isinstance(pool, bool)
                    or not isinstance(pool, int)
                    or pool != tokens * expected
                ):
                    reasons.append("capacity KV token count/pool bytes are inconsistent")
    return {
        "sglang_version": TARGET_SGLANG_VERSION,
        "profile_compatible": not reasons,
        "runtime_hook_verified": False,
        "physical_pool_capacity_verified": False,
        "reasons": reasons,
    }


def inspect_migration(
    model_config_path: str | Path,
    *,
    target_profile_path: str | Path | None = None,
) -> dict[str, Any]:
    config_path = Path(model_config_path).expanduser().resolve()
    if config_path.is_dir():
        config_path /= "config.json"
    geometry = inspect_model_config(_object(config_path))
    profile_path = (
        Path(target_profile_path).expanduser().resolve()
        if target_profile_path is not None
        else None
    )
    target = check_target_profile(
        geometry,
        _object(profile_path) if profile_path is not None else None,
        model_directory=config_path.parent,
    )
    return {
        "schema_version": 1,
        "model_config_path": str(config_path),
        "target_profile_path": str(profile_path) if profile_path is not None else None,
        "geometry": geometry,
        "unsupported_for_legacy_page_index": geometry["unsupported_for_legacy_page_index"],
        "target_profile": target,
    }
