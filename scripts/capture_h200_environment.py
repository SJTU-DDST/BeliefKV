#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import resource
import subprocess
import sys
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from beliefkv.experiments.server_contract import (
    capacity_contract,
    fetch_server_info,
    validate_server_identity,
)
from beliefkv.experiments.source_provenance import git_source_state, sha256_file


def _run(*command: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(
        command,
        cwd=cwd,
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def _gpu_state() -> dict[str, object]:
    fields = (
        "index,name,uuid,pci.bus_id,memory.total,memory.used,memory.free,"
        "driver_version"
    )
    values = [
        item.strip()
        for item in _run(
            "nvidia-smi",
            f"--query-gpu={fields}",
            "--format=csv,noheader,nounits",
        ).split(",")
    ]
    if len(values) != 8:
        raise RuntimeError(f"unexpected nvidia-smi payload: {values!r}")
    return {
        "index": int(values[0]),
        "name": values[1],
        "uuid": values[2],
        "pci_bus_id": values[3],
        "memory_total_mib": int(values[4]),
        "memory_used_mib": int(values[5]),
        "memory_free_mib": int(values[6]),
        "driver_version": values[7],
    }


def _attention_backends(server_log: Path) -> dict[str, str | None]:
    text = server_log.read_text(encoding="utf-8", errors="replace")
    matches = re.findall(
        r"decode_backend=([^,]+), prefill_backend=([^\.]+)",
        text,
    )
    if not matches:
        return {"decode": None, "prefill": None}
    decode, prefill = matches[-1]
    return {"decode": decode.strip(), "prefill": prefill.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Freeze the H200/BF16 BeliefKV hardware and pool contract."
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:18000/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--server-log", type=Path, required=True)
    parser.add_argument("--kv-bytes-per-token", type=int, default=98_304)
    parser.add_argument("--hbm-safety-margin-bytes", type=int, default=1_073_741_824)
    parser.add_argument(
        "--stable-free-hbm-mib",
        type=int,
        help="H1 post-initialization nvidia-smi measurement; defaults to current.",
    )
    parser.add_argument("--max-planned-context-tokens", type=int, default=196_608)
    parser.add_argument(
        "--sglang-root",
        type=Path,
        default=REPOSITORY_ROOT / "third_party/sglang",
    )
    parser.add_argument(
        "--sglang-patch",
        type=Path,
        default=REPOSITORY_ROOT / "patches/sglang-0.5.2rc1-beliefkv.patch",
    )
    parser.add_argument("--runtime-profile", type=Path, required=True)
    parser.add_argument(
        "--profile-id",
        required=True,
        help="Versioned profile identifier written into the environment manifest.",
    )
    args = parser.parse_args()

    server_info = fetch_server_info(args.base_url, timeout_s=30.0)
    identity = validate_server_identity(
        server_info,
        expected_model=args.model,
        expected_model_path=args.model_path,
        expected_weight_dtype="bfloat16",
        expected_kv_dtype="bfloat16",
    )
    capacity = capacity_contract(
        server_info,
        kv_bytes_per_token=args.kv_bytes_per_token,
        hbm_safety_margin_bytes=args.hbm_safety_margin_bytes,
    )
    gpu = _gpu_state()
    stable_free_mib = (
        gpu["memory_free_mib"]
        if args.stable_free_hbm_mib is None
        else args.stable_free_hbm_mib
    )
    stable_free_bytes = stable_free_mib * 1024 * 1024
    max_transfer_bytes = (
        args.max_planned_context_tokens * args.kv_bytes_per_token
    )
    host_pool_bytes = int(float(capacity["host_pool_gib"]) * 1024**3)
    gates = {
        "hbm_safety_margin_satisfied": (
            stable_free_bytes >= args.hbm_safety_margin_bytes
        ),
        "host_transfer_plus_restore_satisfied": (
            host_pool_bytes >= 2 * max_transfer_bytes
        ),
        "context_limit_satisfied": (
            int(capacity["context_length"]) > args.max_planned_context_tokens
        ),
    }
    if not all(gates.values()):
        raise RuntimeError(f"environment freeze gates failed: {gates}")

    model_path = args.model_path.expanduser().resolve()
    revision_files = (
        "config.json",
        "tokenizer_config.json",
        "tokenizer.json",
        "model.safetensors.index.json",
    )
    model_hashes = {
        name: sha256_file(model_path / name)
        for name in revision_files
        if (model_path / name).is_file()
    }
    try:
        import sglang
        import torch

        software = {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "torch_cuda": torch.version.cuda,
            "sglang": sglang.__version__,
        }
    except ImportError as error:
        raise RuntimeError("calibration environment is incomplete") from error

    destination = args.output.expanduser().resolve()
    excluded_paths: list[str] = []
    try:
        excluded_paths.append(str(destination.relative_to(REPOSITORY_ROOT)))
    except ValueError:
        pass
    beliefkv_state = git_source_state(
        REPOSITORY_ROOT,
        exclude_paths=excluded_paths,
    )
    if beliefkv_state["dirty"]:
        raise RuntimeError(
            "BeliefKV source tree is dirty outside the environment manifest: "
            f"tracked={beliefkv_state['tracked_change_paths']}, "
            f"untracked={beliefkv_state['untracked_paths']}"
        )
    sglang_state = git_source_state(
        args.sglang_root.resolve(),
        canonical_patch=args.sglang_patch,
    )
    runtime_profile = args.runtime_profile.expanduser().resolve()
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "profile_id": args.profile_id,
        "runtime_profile": {
            "path": str(runtime_profile),
            "sha256": sha256_file(runtime_profile),
        },
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "hostname": platform.node(),
        "platform": platform.platform(),
        "gpu": gpu,
        "numa": {
            "hardware": _run("numactl", "--hardware"),
            "gpu_topology": _run("nvidia-smi", "topo", "-m"),
            "memlock_limit_bytes": list(resource.getrlimit(resource.RLIMIT_MEMLOCK)),
        },
        "model": {
            **identity,
            "revision_file_sha256": model_hashes,
            "quantization": server_info.get("quantization"),
            "native_context_length": int(capacity["context_length"]),
        },
        "capacity": {
            **capacity,
            "stable_free_hbm_bytes": stable_free_bytes,
            "stable_free_hbm_mib": stable_free_mib,
            "stable_free_hbm_source": (
                "current_nvidia_smi"
                if args.stable_free_hbm_mib is None
                else "h1_post_init_nvidia_smi"
            ),
            "post_workload_free_hbm_mib": gpu["memory_free_mib"],
            "max_planned_context_tokens": args.max_planned_context_tokens,
            "max_planned_transfer_bytes": max_transfer_bytes,
            "restore_reserve_bytes": max_transfer_bytes,
            "host_pool_bytes": host_pool_bytes,
            "gates": gates,
        },
        "runtime": {
            "attention_backends": _attention_backends(args.server_log),
            "hicache_write_policy": server_info.get("hicache_write_policy"),
            "hicache_io_backend": server_info.get("hicache_io_backend"),
            "hicache_mem_layout": server_info.get("hicache_mem_layout"),
            "pinned_host_required": True,
            "cuda_graph_max_bs": server_info.get("cuda_graph_max_bs"),
            "mem_fraction_static": server_info.get("mem_fraction_static"),
        },
        "software": {
            **software,
            "beliefkv": beliefkv_state,
            "sglang_source": sglang_state,
        },
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    print(json.dumps({"output": str(destination), "gates": gates}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
