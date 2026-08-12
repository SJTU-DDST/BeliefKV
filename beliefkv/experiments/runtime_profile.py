from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.server_contract import (
    capacity_contract,
    validate_server_identity,
)


_REQUIRED_SECTIONS = (
    "model",
    "runtime",
    "capacity",
    "source_contract",
    "artifacts",
)


def _required(mapping: dict[str, Any], key: str, *, section: str) -> Any:
    if key not in mapping:
        raise RuntimeError(f"runtime profile omitted {section}.{key}")
    return mapping[key]


def _resolve_repository_path(value: object, repository_root: Path) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = repository_root / path
    return path.resolve()


def load_runtime_profile(
    profile_path: str | Path,
    *,
    repository_root: str | Path,
) -> tuple[dict[str, Any], str]:
    path = Path(profile_path).expanduser().resolve()
    root = Path(repository_root).expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("runtime profile must be a JSON object")
    if payload.get("schema_version") != 1:
        raise RuntimeError("unsupported runtime profile schema_version")
    for section in _REQUIRED_SECTIONS:
        if not isinstance(payload.get(section), dict):
            raise RuntimeError(f"runtime profile omitted object section {section!r}")

    model = payload["model"]
    runtime = payload["runtime"]
    capacity = payload["capacity"]
    source = payload["source_contract"]
    for key in ("path", "served_name", "weight_dtype", "kv_cache_dtype", "context_length"):
        _required(model, key, section="model")
    for key in (
        "sglang_version",
        "sglang_commit",
        "kv_cache_cli_dtype",
        "tensor_parallel_size",
        "page_size",
        "chunked_prefill_size",
        "max_running_requests",
        "cuda_graph_max_bs",
        "mem_fraction_static",
        "hicache_size_gib",
        "hicache_write_policy",
        "hicache_io_backend",
        "hicache_mem_layout",
    ):
        _required(runtime, key, section="runtime")
    for key in (
        "max_total_tokens",
        "kv_bytes_per_token",
        "kv_pool_bytes",
        "hbm_safety_margin_bytes",
        "host_pool_bytes",
    ):
        _required(capacity, key, section="capacity")
    for key in (
        "canonical_sglang_patch",
        "canonical_sglang_patch_sha256",
        "expected_sglang_tree",
    ):
        _required(source, key, section="source_contract")

    integer_fields = (
        (model, "context_length"),
        (runtime, "tensor_parallel_size"),
        (runtime, "page_size"),
        (runtime, "chunked_prefill_size"),
        (runtime, "max_running_requests"),
        (runtime, "cuda_graph_max_bs"),
        (capacity, "max_total_tokens"),
        (capacity, "kv_bytes_per_token"),
        (capacity, "kv_pool_bytes"),
        (capacity, "hbm_safety_margin_bytes"),
        (capacity, "host_pool_bytes"),
    )
    if "max_prefill_tokens" in runtime:
        integer_fields = (*integer_fields, (runtime, "max_prefill_tokens"))
    for mapping, key in integer_fields:
        if int(mapping[key]) <= 0:
            raise RuntimeError(f"runtime profile requires positive {key}")
    if int(runtime["chunked_prefill_size"]) > int(
        runtime.get("max_prefill_tokens", 16384)
    ):
        raise RuntimeError(
            "runtime.chunked_prefill_size cannot exceed max_prefill_tokens"
        )

    expected_pool_bytes = int(capacity["max_total_tokens"]) * int(
        capacity["kv_bytes_per_token"]
    )
    if int(capacity["kv_pool_bytes"]) != expected_pool_bytes:
        raise RuntimeError(
            "capacity.kv_pool_bytes does not match max_total_tokens * "
            "kv_bytes_per_token"
        )
    expected_host_bytes = int(float(runtime["hicache_size_gib"]) * (1024**3))
    if int(capacity["host_pool_bytes"]) != expected_host_bytes:
        raise RuntimeError(
            "capacity.host_pool_bytes does not match runtime.hicache_size_gib"
        )
    mem_fraction = float(runtime["mem_fraction_static"])
    if not 0.0 < mem_fraction < 1.0:
        raise RuntimeError("runtime.mem_fraction_static must be in (0, 1)")

    normalized = deepcopy(payload)
    normalized["_profile_path"] = str(path)
    normalized["_profile_sha256"] = sha256_file(path)
    normalized["_repository_root"] = str(root)
    normalized["_model_path"] = str(Path(str(model["path"])).expanduser().resolve())
    normalized["_environment_path"] = str(
        _resolve_repository_path(payload["artifacts"]["environment"], root)
    )
    patch_path = _resolve_repository_path(source["canonical_sglang_patch"], root)
    normalized["_canonical_sglang_patch"] = str(patch_path)
    if not patch_path.is_file():
        raise RuntimeError(f"canonical SGLang patch is missing: {patch_path}")
    actual_patch_sha = sha256_file(patch_path)
    if actual_patch_sha != str(source["canonical_sglang_patch_sha256"]):
        raise RuntimeError(
            "canonical SGLang patch SHA-256 does not match the runtime profile"
        )
    return normalized, normalized["_profile_sha256"]


def runtime_launch_environment(profile: dict[str, Any]) -> dict[str, str]:
    model = profile["model"]
    runtime = profile["runtime"]
    capacity = profile["capacity"]
    return {
        "MODEL_PATH": str(profile["_model_path"]),
        "SERVED_MODEL_NAME": str(model["served_name"]),
        "WEIGHT_DTYPE": str(model["weight_dtype"]),
        "KV_CACHE_DTYPE": str(runtime["kv_cache_cli_dtype"]),
        "PAGE_SIZE": str(int(runtime["page_size"])),
        "CONTEXT_LENGTH": str(int(model["context_length"])),
        "MAX_TOTAL_TOKENS": str(int(capacity["max_total_tokens"])),
        "MEM_FRACTION_STATIC": str(float(runtime["mem_fraction_static"])),
        "MAX_RUNNING_REQUESTS": str(int(runtime["max_running_requests"])),
        "CHUNKED_PREFILL_SIZE": str(int(runtime["chunked_prefill_size"])),
        "MAX_PREFILL_TOKENS": str(int(runtime.get("max_prefill_tokens", 16384))),
        "CUDA_GRAPH_MAX_BS": str(int(runtime["cuda_graph_max_bs"])),
        "HICACHE_SIZE_GB": str(float(runtime["hicache_size_gib"])),
        "HICACHE_WRITE_POLICY": str(runtime["hicache_write_policy"]),
        "HICACHE_IO_BACKEND": str(runtime["hicache_io_backend"]),
        "HICACHE_MEM_LAYOUT": str(runtime["hicache_mem_layout"]),
        "TENSOR_PARALLEL_SIZE": str(int(runtime["tensor_parallel_size"])),
    }


def _actual(server_info: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = server_info.get(key)
        if value is not None:
            return value
    return None


def _compare_exact(
    checks: list[dict[str, Any]],
    *,
    name: str,
    actual: Any,
    expected: Any,
    kind: str = "text",
) -> None:
    if kind == "int":
        try:
            passed = int(actual) == int(expected)
        except (TypeError, ValueError):
            passed = False
    elif kind == "float":
        try:
            passed = math.isclose(
                float(actual), float(expected), rel_tol=0.0, abs_tol=1e-9
            )
        except (TypeError, ValueError):
            passed = False
    else:
        passed = str(actual) == str(expected)
    checks.append(
        {
            "name": name,
            "actual": actual,
            "expected": expected,
            "passed": passed,
        }
    )


def validate_server_against_runtime_profile(
    server_info: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    model = profile["model"]
    runtime = profile["runtime"]
    capacity = profile["capacity"]
    identity = validate_server_identity(
        server_info,
        expected_model=str(model["served_name"]),
        expected_model_path=str(profile["_model_path"]),
        expected_weight_dtype=str(model["weight_dtype"]),
        expected_kv_dtype=str(model["kv_cache_dtype"]),
    )
    physical = capacity_contract(
        server_info,
        kv_bytes_per_token=int(capacity["kv_bytes_per_token"]),
        hbm_safety_margin_bytes=int(capacity["hbm_safety_margin_bytes"]),
    )
    checks: list[dict[str, Any]] = []
    _compare_exact(
        checks,
        name="sglang_version",
        actual=server_info.get("version"),
        expected=runtime["sglang_version"],
    )
    _compare_exact(
        checks,
        name="tensor_parallel_size",
        actual=_actual(server_info, "tp_size", "tensor_parallel_size"),
        expected=runtime["tensor_parallel_size"],
        kind="int",
    )
    _compare_exact(
        checks,
        name="max_total_tokens",
        actual=physical["max_total_num_tokens"],
        expected=capacity["max_total_tokens"],
        kind="int",
    )
    _compare_exact(
        checks,
        name="context_length",
        actual=physical["context_length"],
        expected=model["context_length"],
        kind="int",
    )
    _compare_exact(
        checks,
        name="max_running_requests",
        actual=physical["max_running_requests"],
        expected=runtime["max_running_requests"],
        kind="int",
    )
    _compare_exact(
        checks,
        name="page_size",
        actual=physical["page_size"],
        expected=runtime["page_size"],
        kind="int",
    )
    _compare_exact(
        checks,
        name="chunked_prefill_size",
        actual=physical["prefill_chunk_size"],
        expected=runtime["chunked_prefill_size"],
        kind="int",
    )
    _compare_exact(
        checks,
        name="max_prefill_tokens",
        actual=physical["max_prefill_tokens"],
        expected=runtime.get("max_prefill_tokens", 16384),
        kind="int",
    )
    _compare_exact(
        checks,
        name="cuda_graph_max_bs",
        actual=server_info.get("cuda_graph_max_bs"),
        expected=runtime["cuda_graph_max_bs"],
        kind="int",
    )
    _compare_exact(
        checks,
        name="mem_fraction_static",
        actual=server_info.get("mem_fraction_static"),
        expected=runtime["mem_fraction_static"],
        kind="float",
    )
    _compare_exact(
        checks,
        name="hicache_size_gib",
        actual=physical["host_pool_gib"],
        expected=runtime["hicache_size_gib"],
        kind="float",
    )
    for field in ("hicache_write_policy", "hicache_io_backend", "hicache_mem_layout"):
        _compare_exact(
            checks,
            name=field,
            actual=server_info.get(field),
            expected=runtime[field],
        )
    failed = [str(row["name"]) for row in checks if not row["passed"]]
    if failed:
        raise RuntimeError(
            "SGLang runtime profile mismatch: " + ", ".join(sorted(failed))
        )
    return {
        "identity": identity,
        "capacity": physical,
        "checks": checks,
        "passed": True,
    }
