from __future__ import annotations

import json
from pathlib import Path

import pytest

from beliefkv.experiments.model_migration import (
    check_target_profile,
    inspect_migration,
    inspect_model_config,
)
from scripts.inspect_model_migration import main


def _old() -> dict:
    return {
        "model_type": "qwen3_moe",
        "num_hidden_layers": 48,
        "num_key_value_heads": 4,
        "head_dim": 128,
    }


def _hybrid() -> dict:
    return {
        "model_type": "qwen3_5_moe",
        "text_config": {
            "model_type": "qwen3_5_moe_text",
            "num_hidden_layers": 40,
            "num_key_value_heads": 2,
            "head_dim": 256,
            "full_attention_interval": 4,
            "layer_types": ["linear_attention"] * 3 + ["full_attention"],
        },
    }


def _profile(path: Path, kv: int = 98_304) -> dict:
    return {
        "runtime": {"sglang_version": "0.5.20", "page_size": 1, "kv_cache_cli_dtype": "auto"},
        "model": {"path": str(path), "kv_cache_dtype": "bfloat16", "weight_dtype": "bfloat16"},
        "capacity": {"kv_bytes_per_token": kv, "max_total_tokens": 100, "kv_pool_bytes": 100 * kv},
    }


def test_old_qwen3_moe_geometry_and_target(tmp_path: Path) -> None:
    geometry = inspect_model_config(_old())
    assert geometry["full_attention_layers"] == 48
    assert geometry["bf16_full_attention_kv_bytes_per_token"] == 98_304
    assert geometry["unsupported_for_legacy_page_index"] is False
    target = check_target_profile(geometry, _profile(tmp_path), model_directory=tmp_path)
    assert target["profile_compatible"] is True
    assert target["runtime_hook_verified"] is False


def test_qwen35_hybrid_never_uses_attention_kv_as_physical_pool(tmp_path: Path) -> None:
    config = _hybrid()
    config["text_config"]["layer_types"] *= 10
    geometry = inspect_model_config(config)
    assert geometry["config_section"] == "text_config"
    assert geometry["full_attention_layers"] == 10
    assert geometry["state_layer_counts"]["linear_attention"] == 30
    assert geometry["bf16_full_attention_kv_bytes_per_token"] == 20_480
    assert geometry["physical_state_capacity_bytes"] is None
    assert geometry["unsupported_for_legacy_page_index"] is True
    target = check_target_profile(geometry, _profile(tmp_path, 20_480))
    assert target["profile_compatible"] is False
    assert "hybrid" in target["reasons"][0]


def test_interval_only_fails_closed_on_unclassified_states() -> None:
    config = _hybrid()
    del config["text_config"]["layer_types"]
    result = inspect_model_config(config)
    assert result["full_attention_layers"] == 10
    assert result["state_layers"] == 30
    assert result["state_layer_counts"]["linear_attention"] is None
    assert result["unsupported_for_legacy_page_index"] is True


def test_explicit_mamba_state_and_root_interval() -> None:
    geometry = inspect_model_config({
        "num_hidden_layers": 4,
        "num_key_value_heads": 2,
        "head_dim": 128,
        "full_attention_interval": 2,
        "layer_types": ["mamba", "attention", "mamba2", "full_attention"],
    })
    assert geometry["full_attention_layer_indices"] == [1, 3]
    assert geometry["state_layer_counts"]["mamba"] == 2
    assert geometry["bf16_full_attention_kv_bytes_per_token"] == 2_048
    assert geometry["unsupported_for_legacy_page_index"] is True


@pytest.mark.parametrize(
    "change",
    [
        {"num_hidden_layers": True},
        {"num_key_value_heads": 0},
        {"head_dim": "256"},
        {"layer_types": ["linear_attention"] * 40},
        {"layer_types": ["unknown"] * 40},
        {"full_attention_interval": 0},
        {"full_attention_interval": 41, "layer_types": None},
    ],
)
def test_invalid_hybrid_geometry_rejected(change: dict) -> None:
    config = _hybrid()
    config["text_config"]["layer_types"] *= 10
    config["text_config"].update(change)
    with pytest.raises(ValueError):
        inspect_model_config(config)


def test_hybrid_missing_layout_rejected() -> None:
    with pytest.raises(ValueError, match="lacks layer_types"):
        inspect_model_config({**_old(), "model_type": "qwen3_next"})


@pytest.mark.parametrize(
    "update",
    [
        ("runtime", "sglang_version", "0.5.2rc1"),
        ("runtime", "page_size", 16),
        ("runtime", "kv_cache_cli_dtype", "fp8_e4m3"),
        ("model", "weight_dtype", "float16"),
        ("capacity", "kv_bytes_per_token", 20480),
        ("capacity", "kv_pool_bytes", 1),
    ],
)
def test_target_profile_mismatches_rejected(tmp_path: Path, update: tuple) -> None:
    profile = _profile(tmp_path)
    section, field, value = update
    profile[section][field] = value
    assert not check_target_profile(
        inspect_model_config(_old()), profile, model_directory=tmp_path
    )["profile_compatible"]


def test_cli_writes_json_then_exits_closed(tmp_path: Path) -> None:
    config = _hybrid()
    config["text_config"]["layer_types"] *= 10
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text(json.dumps(config), encoding="utf-8")
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(_profile(model, 20_480)), encoding="utf-8")
    output_dir = tmp_path / "audit"
    assert main([
        "--model-config", str(model),
        "--output-dir", str(output_dir),
        "--target-profile", str(profile_path),
    ]) == 1
    report = json.loads((output_dir / "model_migration.json").read_text())
    assert report["unsupported_for_legacy_page_index"] is True
    assert report["target_profile"]["profile_compatible"] is False
    assert report["geometry"]["physical_state_capacity_bytes"] is None
    assert inspect_migration(model)["target_profile"]["profile_compatible"] is False


def test_cli_accepts_full_attention_profile(tmp_path: Path) -> None:
    model = tmp_path / "old-model"
    model.mkdir()
    (model / "config.json").write_text(json.dumps(_old()), encoding="utf-8")
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(_profile(model)), encoding="utf-8")
    output_dir = tmp_path / "report"
    assert main([
        "--model-config", str(model / "config.json"),
        "--target-profile", str(profile_path),
        "--output-dir", str(output_dir),
    ]) == 0
    report = json.loads((output_dir / "model_migration.json").read_text())
    assert report["target_profile"]["profile_compatible"] is True
    assert report["target_profile"]["runtime_hook_verified"] is False
    assert report["geometry"]["physical_state_capacity_verified"] is False
