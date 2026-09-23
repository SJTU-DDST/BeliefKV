#!/usr/bin/env python3
"""Pin or verify default native Qwen3.5 separate FULL/MAMBA HBM pools."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MIN_POST_START_FREE_MIB = 4096
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.model_migration import inspect_model_config
from beliefkv.experiments.server_contract import (
    fetch_server_info,
    validate_native_pool_census,
    validate_native_reactive_v0520,
)


def capture(
    *,
    base_url: str,
    telemetry_dir: Path,
    model_path: Path,
    gpu: int,
    mem_fraction_static: float,
    expected_full_host_share: float | None = None,
) -> dict[str, object]:
    info = fetch_server_info(base_url)
    identity = validate_native_reactive_v0520(
        info,
        expected_model="Qwen3.5-35B-A3B",
        expected_model_path=model_path,
        expected_weight_dtype="bfloat16",
        expected_kv_dtype="bfloat16",
    )
    if (
        info.get("enable_unified_memory") is not False
        or info.get("mem_fraction_static") != mem_fraction_static
    ):
        raise RuntimeError("native HBM allocator mode or static memory fraction changed")
    ready = json.loads(
        (telemetry_dir / "native_telemetry_ready.json").read_text(encoding="utf-8")
    )
    census = json.loads(
        (telemetry_dir / "native_capacity_census.json").read_text(encoding="utf-8")
    )
    scheduler_pid = ready.get("scheduler_pid")
    if type(scheduler_pid) is not int or scheduler_pid <= 0:
        raise RuntimeError("native scheduler readiness is missing a PID")
    if ready.get("source") != "native_sglang_v0520":
        raise RuntimeError("unexpected native scheduler readiness source")
    proc = subprocess.run(
        (
            "nvidia-smi", "-i", str(gpu),
            "--query-gpu=uuid,memory.total,memory.free",
            "--format=csv,noheader,nounits",
        ),
        check=True, capture_output=True, text=True,
    )
    uuid, mib, free_mib = (part.strip() for part in proc.stdout.strip().split(","))
    if int(free_mib) < MIN_POST_START_FREE_MIB:
        raise RuntimeError(
            f"GPU has only {free_mib} MiB free after startup; "
            f"minimum is {MIN_POST_START_FREE_MIB} MiB"
        )
    geometry = inspect_model_config(
        json.loads((model_path / "config.json").read_text(encoding="utf-8"))
    )
    pool_tokens = int(info.get("max_total_num_tokens") or 0)
    host_budget = info.get("hicache_size")
    if type(host_budget) not in (int, float) or int(host_budget) != host_budget:
        raise RuntimeError("Host HiCache budget is not an integer GB value")
    pools = validate_native_pool_census(
        census,
        scheduler_pid=scheduler_pid,
        pool_tokens=pool_tokens,
        host_budget_gb=int(host_budget),
        full_bytes_per_token=geometry["bf16_full_attention_kv_bytes_per_token"],
        gpu_total_bytes=int(mib) * 1024**2,
    )
    full_host_share = pools["host_full_bytes"] / pools["host_total_bytes"]
    mamba_host_share = pools["host_mamba_bytes"] / pools["host_total_bytes"]
    if expected_full_host_share is not None and abs(
        full_host_share - expected_full_host_share
    ) > 0.005:
        raise RuntimeError(
            "observed FULL Host pool share does not match the preregistered split: "
            f"observed={full_host_share:.4f}, expected={expected_full_host_share:.4f}"
        )
    return {
        "schema_version": 1,
        "calibration_kind": "native_qwen35_full_mamba_static_capacity",
        "server_identity": identity,
        "gpu_uuid": uuid,
        "gpu_total_mib": int(mib),
        "minimum_post_start_free_mib": MIN_POST_START_FREE_MIB,
        "max_total_num_tokens": pool_tokens,
        "max_running_requests": int(info.get("max_running_requests") or 0),
        "mem_fraction_static": mem_fraction_static,
        "host_budget_gb": int(host_budget),
        "host_pool_split": {
            "full_share": full_host_share,
            "mamba_share": mamba_host_share,
            "requested_full_share": expected_full_host_share,
        },
        "full_bytes_per_token": geometry["bf16_full_attention_kv_bytes_per_token"],
        "pools": pools,
        "device_ceiling_semantics": "static separate FULL and MAMBA allocations; bytes additive",
        "dynamic_pressure_and_service_calibrated": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--telemetry-dir", type=Path, required=True)
    parser.add_argument(
        "--model-path", type=Path,
        default=Path("/srv/ai/models/Qwen/Qwen3.5-35B-A3B"),
    )
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--mem-fraction-static", type=float, default=0.94)
    parser.add_argument("--expected-full-host-share", type=float)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path)
    group.add_argument("--verify", type=Path)
    args = parser.parse_args()
    observed = capture(
        base_url=args.base_url, telemetry_dir=args.telemetry_dir,
        model_path=args.model_path, gpu=args.gpu,
        mem_fraction_static=args.mem_fraction_static,
        expected_full_host_share=args.expected_full_host_share,
    )
    if args.verify is not None:
        expected = json.loads(args.verify.read_text(encoding="utf-8"))
        if expected != observed:
            raise SystemExit("native HBM/Host capacity differs from frozen calibration")
        print(f"Verified native FULL/MAMBA pool against {args.verify}")
    else:
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(observed, output, indent=2, sort_keys=True)
            output.write("\n")
        print(f"Pinned native FULL/MAMBA pool: {args.output}")


if __name__ == "__main__":
    main()
