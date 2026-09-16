from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from scripts.validate_runtime_profile import _dirty_worktree_allowed

from beliefkv.experiments.runtime_profile import (
    load_runtime_profile,
    runtime_launch_environment,
    validate_beliefkv_service_bindings,
    validate_server_against_runtime_profile,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROFILE = (
    REPOSITORY_ROOT
    / "configs/p6/h200_bf16_v1/frozen_runtime_profile.json"
)
V2_PROFILE = (
    REPOSITORY_ROOT
    / "configs/p6/h200_bf16_v2/frozen_runtime_profile.json"
)
V4_PROFILE = (
    REPOSITORY_ROOT
    / "configs/p6/h200_bf16_v4/frozen_runtime_profile.json"
)
V5_PROFILE = (
    REPOSITORY_ROOT
    / "configs/p6/h200_bf16_v5/frozen_runtime_profile.json"
)
V5_RESTORE_GATE_PROFILE = (
    REPOSITORY_ROOT
    / "configs/p6/h200_bf16_v5_restore_gate/frozen_runtime_profile.json"
)
PERF_PROFILE = (
    REPOSITORY_ROOT
    / "configs/p6/h200_bf16_perf_v1/frozen_runtime_profile.json"
)
V6_PROFILE = (
    REPOSITORY_ROOT
    / "configs/p6/h200_bf16_v6/frozen_runtime_profile.json"
)
V7_PROFILE = (
    REPOSITORY_ROOT
    / "configs/p6/h200_bf16_v7/frozen_runtime_profile.json"
)
HIGH_PRESSURE_V2_PLAN = (
    REPOSITORY_ROOT
    / "configs/p6/predictive_joint_h200_high_pressure_v2/ab_plan.json"
)


def test_dirty_worktree_override_is_explicit(monkeypatch) -> None:
    monkeypatch.delenv("BELIEFKV_ALLOW_DIRTY_WORKTREE", raising=False)
    assert not _dirty_worktree_allowed()
    monkeypatch.setenv("BELIEFKV_ALLOW_DIRTY_WORKTREE", "1")
    assert _dirty_worktree_allowed()
    monkeypatch.setenv("BELIEFKV_ALLOW_DIRTY_WORKTREE", "true")
    assert not _dirty_worktree_allowed()


def _profile() -> dict[str, object]:
    profile, digest = load_runtime_profile(
        PROFILE,
        repository_root=REPOSITORY_ROOT,
    )
    assert len(digest) == 64
    return profile


def _server_info(profile: dict[str, object]) -> dict[str, object]:
    model = profile["model"]
    runtime = profile["runtime"]
    capacity = profile["capacity"]
    return {
        "status": "ready",
        "served_model_name": model["served_name"],
        "model_path": profile["_model_path"],
        "dtype": model["weight_dtype"],
        "kv_cache_dtype": runtime["kv_cache_cli_dtype"],
        "version": runtime["sglang_version"],
        "tp_size": runtime["tensor_parallel_size"],
        "max_total_num_tokens": capacity["max_total_tokens"],
        "context_length": model["context_length"],
        "max_running_requests": runtime["max_running_requests"],
        "page_size": runtime["page_size"],
        "chunked_prefill_size": runtime["chunked_prefill_size"],
        "max_prefill_tokens": runtime.get("max_prefill_tokens", 16384),
        "cuda_graph_max_bs": runtime["cuda_graph_max_bs"],
        "mem_fraction_static": runtime["mem_fraction_static"],
        "hicache_size": runtime["hicache_size_gib"],
        "hicache_write_policy": runtime["hicache_write_policy"],
        "hicache_io_backend": runtime["hicache_io_backend"],
        "hicache_mem_layout": runtime["hicache_mem_layout"],
        "internal_states": [{"memory_usage": {}}],
    }


def test_h200_profile_is_internally_consistent() -> None:
    profile = _profile()
    environment = runtime_launch_environment(profile)

    assert environment["MAX_TOTAL_TOKENS"] == "871700"
    assert environment["WEIGHT_DTYPE"] == "bfloat16"
    assert environment["KV_CACHE_DTYPE"] == "auto"
    assert environment["HICACHE_SIZE_GB"] == "96.0"
    assert environment["TENSOR_PARALLEL_SIZE"] == "1"


def test_h200_v2_profile_reserves_moe_workspace() -> None:
    profile, digest = load_runtime_profile(
        V2_PROFILE,
        repository_root=REPOSITORY_ROOT,
    )
    environment = runtime_launch_environment(profile)

    assert len(digest) == 64
    assert environment["MAX_TOTAL_TOKENS"] == "850000"
    assert profile["capacity"]["kv_pool_bytes"] == 850000 * 98304
    assert profile["capacity"]["hbm_safety_margin_bytes"] == 2 * 1024**3
    assert profile["artifacts"]["gpu_service"]["online_eligible"] is False
    assert profile["artifacts"]["transfer_service"][
        "online_eligible_for_supported_conditions"
    ] is False


def test_h200_v4_profile_enables_batched_prefill_quantum() -> None:
    profile, digest = load_runtime_profile(
        V4_PROFILE,
        repository_root=REPOSITORY_ROOT,
    )
    environment = runtime_launch_environment(profile)

    assert len(digest) == 64
    assert environment["MAX_TOTAL_TOKENS"] == "850000"
    assert environment["MAX_RUNNING_REQUESTS"] == "32"
    assert environment["CHUNKED_PREFILL_SIZE"] == "16384"
    assert environment["MAX_PREFILL_TOKENS"] == "16384"


def test_h200_v5_restore_gate_only_reduces_running_slots() -> None:
    formal, _ = load_runtime_profile(
        V5_PROFILE,
        repository_root=REPOSITORY_ROOT,
    )
    gate, _ = load_runtime_profile(
        V5_RESTORE_GATE_PROFILE,
        repository_root=REPOSITORY_ROOT,
    )

    assert gate["profile_id"] == "h200_bf16_v5_restore_gate"
    assert gate["runtime"]["max_running_requests"] == 2

    gate["profile_id"] = formal["profile_id"]
    gate["runtime"]["max_running_requests"] = formal["runtime"][
        "max_running_requests"
    ]
    for key in tuple(formal):
        if str(key).startswith("_"):
            formal.pop(key)
            gate.pop(key)
    assert gate == formal


def test_h200_performance_profile_has_complete_artifact_contract() -> None:
    profile, _ = load_runtime_profile(
        PERF_PROFILE,
        repository_root=REPOSITORY_ROOT,
    )

    gpu_service = profile["artifacts"]["gpu_service"]
    transfer_service = profile["artifacts"]["transfer_service"]
    assert gpu_service["hardware_key"]
    assert gpu_service["sha256"]
    assert gpu_service["evaluation_sha256"]
    assert transfer_service["hardware_key"]
    assert transfer_service["sha256"]


def test_h200_v6_profile_binds_shadow_service_artifacts() -> None:
    profile, _ = load_runtime_profile(
        V6_PROFILE,
        repository_root=REPOSITORY_ROOT,
    )
    gpu_service = profile["artifacts"]["gpu_service"]
    transfer_service = profile["artifacts"]["transfer_service"]
    config = {
        "gpu_service_model_path": gpu_service["path"],
        "gpu_service_hardware_key": gpu_service["hardware_key"],
        "transfer_service_model_path": transfer_service["path"],
        "transfer_service_hardware_key": transfer_service["hardware_key"],
    }

    result = validate_beliefkv_service_bindings(config, profile)

    assert result["passed"] is True
    assert all(row["passed"] for row in result["bindings"])


def test_h200_v7_profile_only_advances_the_ownership_patch() -> None:
    v6, _ = load_runtime_profile(
        V6_PROFILE,
        repository_root=REPOSITORY_ROOT,
    )
    v7, _ = load_runtime_profile(
        V7_PROFILE,
        repository_root=REPOSITORY_ROOT,
    )

    assert v7["profile_id"] == "h200_bf16_v7"
    assert v7["runtime"] == v6["runtime"]
    assert v7["capacity"] == v6["capacity"]
    assert v7["model"] == v6["model"]
    assert v7["artifacts"] == v6["artifacts"]
    assert v7["source_contract"]["canonical_sglang_patch"].endswith(
        "sglang-0.5.2rc1-beliefkv-perf-ownership.patch"
    )


def test_high_pressure_v2_uses_fixed_40_root_prefix() -> None:
    plan = json.loads(HIGH_PRESSURE_V2_PLAN.read_text(encoding="utf-8"))

    assert plan["frozen"] is True
    assert plan["runtime_profile"].endswith(
        "h200_bf16_v7/frozen_runtime_profile.json"
    )
    assert plan["workload"]["roots"] == 40
    assert plan["workload"]["selection"] == "frozen_manifest_prefix_40"
    assert plan["workload"]["pressure_acceptance"] == {
        "all_roots_eager": True,
        "max_running_requests": 32,
        "required_hbm_ratio": 0.8,
    }
    assert plan["activation_deadline_seconds"] is None


@pytest.mark.parametrize(
    ("field", "wrong"),
    (
        ("gpu_service_hardware_key", None),
        ("transfer_service_hardware_key", "wrong-key"),
    ),
)
def test_h200_v6_profile_rejects_service_key_drift(
    field: str, wrong: object
) -> None:
    profile, _ = load_runtime_profile(
        V6_PROFILE,
        repository_root=REPOSITORY_ROOT,
    )
    config = {
        "gpu_service_model_path": profile["artifacts"]["gpu_service"]["path"],
        "gpu_service_hardware_key": profile["artifacts"]["gpu_service"]["hardware_key"],
        "transfer_service_model_path": profile["artifacts"]["transfer_service"]["path"],
        "transfer_service_hardware_key": profile["artifacts"]["transfer_service"]["hardware_key"],
    }
    config[field] = wrong

    with pytest.raises(RuntimeError, match="service artifact binding mismatch"):
        validate_beliefkv_service_bindings(config, profile)


def test_runtime_contract_accepts_exact_profile() -> None:
    profile = _profile()

    result = validate_server_against_runtime_profile(
        _server_info(profile),
        profile,
    )

    assert result["passed"] is True
    assert all(row["passed"] for row in result["checks"])


@pytest.mark.parametrize(
    ("field", "wrong"),
    (
        ("max_total_num_tokens", 871_699),
        ("max_running_requests", 15),
        ("hicache_mem_layout", "page_first"),
        ("kv_cache_dtype", "float16"),
        ("max_prefill_tokens", 4096),
    ),
)
def test_runtime_contract_rejects_profile_drift(field: str, wrong: object) -> None:
    profile = _profile()
    server_info = _server_info(profile)
    server_info[field] = wrong

    with pytest.raises(RuntimeError, match="mismatch"):
        validate_server_against_runtime_profile(server_info, profile)


def test_profile_rejects_capacity_mismatch(tmp_path: Path) -> None:
    profile = deepcopy(_profile())
    for key in tuple(profile):
        if str(key).startswith("_"):
            profile.pop(key)
    profile["capacity"]["kv_pool_bytes"] += 1
    candidate = tmp_path / "profile.json"
    import json

    candidate.write_text(json.dumps(profile), encoding="utf-8")

    with pytest.raises(RuntimeError, match="kv_pool_bytes"):
        load_runtime_profile(candidate, repository_root=REPOSITORY_ROOT)


def test_formal_launcher_requires_profile() -> None:
    result = subprocess.run(
        (str(REPOSITORY_ROOT / "scripts/launch_deepagents_swebench_server.sh"),),
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "--runtime-profile" in result.stderr


def test_formal_launcher_rejects_immutable_override(tmp_path: Path) -> None:
    server = tmp_path / "server"
    server.mkdir()
    (server / "beliefkv_config.json").write_text("{}\n", encoding="utf-8")
    result = subprocess.run(
        (
            str(REPOSITORY_ROOT / "scripts/launch_deepagents_swebench_server.sh"),
            "--runtime-profile",
            str(PROFILE),
            str(server),
            "--max-total-tokens",
            "1",
        ),
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "owns immutable argument" in result.stderr


def test_formal_launcher_rejects_prefill_override(tmp_path: Path) -> None:
    server = tmp_path / "server"
    server.mkdir()
    (server / "beliefkv_config.json").write_text("{}\n", encoding="utf-8")
    result = subprocess.run(
        (
            str(REPOSITORY_ROOT / "scripts/launch_deepagents_swebench_server.sh"),
            "--runtime-profile",
            str(V4_PROFILE),
            str(server),
            "--max-prefill-tokens=4096",
        ),
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "owns immutable argument" in result.stderr


def test_formal_launcher_rejects_occupied_port(tmp_path: Path) -> None:
    import os
    import socket

    server = tmp_path / "server"
    server.mkdir()
    (server / "beliefkv_config.json").write_text("{}\n", encoding="utf-8")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        result = subprocess.run(
            (
                str(REPOSITORY_ROOT / "scripts/launch_deepagents_swebench_server.sh"),
                "--runtime-profile",
                str(PROFILE),
                str(server),
            ),
            cwd=REPOSITORY_ROOT,
            env={**os.environ, "PORT": str(port)},
            text=True,
            capture_output=True,
        )

    assert result.returncode == 2
    assert "already occupied" in result.stderr
