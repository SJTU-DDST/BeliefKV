#!/usr/bin/env python3
"""Inspect frozen operator paths and existing telemetry without starting CUDA."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.analyze_prefill_turnover import analyze_samples
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords


def inspect_arm(arm: Path) -> dict:
    sealed = SealedRecords()
    phases, graphs = defaultdict(list), defaultdict(Counter)
    prefills, probes, waits = [], [], []
    first, last = None, None
    for row in sealed(arm / "server/runtime_audit.jsonl"):
        event = row.get("event")
        if event in ("prefill_turnover_sample", "prefill_turnover_incomplete"):
            probes.append(row)
        elif event == "gpu_restore_dependency_wait":
            waits.append(row)
        elif event == "gpu_service_sample":
            if not any(
                sample.get("workflow_id") for sample in row.get("request_samples", ())
            ):
                continue
            phase = row.get("phase")
            if phase not in ("prefill", "decode"):
                continue
            phases[phase].append(row["service_elapsed_ms"])
            graphs[phase][str(row.get("cuda_graph_replayed")).lower()] += 1
            first = min(first, row["service_start_ts_ms"]) if first else row["service_start_ts_ms"]
            last = max(last, row["complete_ts_ms"]) if last else row["complete_ts_ms"]
            if phase == "prefill":
                prefills.append(row)
    if first is None or last is None:
        raise ValueError(f"no workflow-attributed native service: {arm}")
    turnover = analyze_samples(probes, prefills, waits, begin=first, end=last + 0.001)
    info_path = arm / "server/server_info.json"
    info = json.loads(info_path.read_bytes())
    log_path = arm / "server.log"
    log = log_path.read_bytes()
    selected_logs = [
        line.decode(errors="replace") for line in log.splitlines()
        if any(marker in line for marker in (
            b"Linear attention kernel backend:",
            b"Using fused GDN decode QKVZ/BA",
            b"Falling back from fused GDN decode",
            b"Using default MoE kernel config.",
            b"Using MoE kernel config from",
        ))
    ]
    return {
        "arm": str(arm),
        "input_jsonl_prefixes": sealed.prefixes,
        "window": {"first_service_ts_ms": first, "last_complete_ts_ms": last},
        "server_info_sha256": hashlib.sha256(info_path.read_bytes()).hexdigest(),
        "server_configuration": {
            key: info.get(key) for key in (
                "version", "attention_backend", "linear_attn_backend",
                "linear_attn_decode_backend", "linear_attn_prefill_backend",
                "enable_torch_compile", "disable_overlap_schedule",
                "cuda_graph_config", "moe_runner_backend", "bf16_gemm_backend",
            )
        },
        "server_log_prefix_bytes": len(log),
        "server_log_prefix_sha256": hashlib.sha256(log).hexdigest(),
        "selected_startup_logs": selected_logs,
        "workflow_service_by_phase": {
            phase: {
                "worker_interval_ms": distribution(values),
                "cuda_graph_replayed": dict(graphs[phase]),
            }
            for phase, values in phases.items()
        },
        "prefill_turnover": {
            key: turnover[key] for key in (
                "available", "probes_in_sealed_prefix", "unknown_counts",
                "all_paired", "short_single_request",
            )
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--full", type=Path, required=True)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous observations; choose a new output")
    engine_files = (
        "python/sglang/srt/models/qwen3_5.py",
        "python/sglang/srt/models/qwen4_exp.py",
        "python/sglang/srt/layers/attention/linear/gdn_backend.py",
        "python/sglang/srt/layers/attention/linear/kernels/gdn_triton.py",
        "python/sglang/srt/layers/attention/linear/kernels/gdn_flashinfer.py",
        "python/sglang/srt/layers/moe/moe_runner/triton_utils/fused_moe_triton_config.py",
    )
    result = {
        "observed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("torch", "triton", "flashinfer-python", "sglang")
        },
        "engine": str(args.engine.resolve()),
        "engine_source_sha256": {
            name: hashlib.sha256((args.engine / name).read_bytes()).hexdigest()
            for name in engine_files
        },
        "arms": {
            "completed_native": inspect_arm(args.native.resolve()),
            "collecting_full": inspect_arm(args.full.resolve()),
        },
        "scope": [
            "CPU-only offline analysis; no GPU profiler, model or CUDA initialization.",
            "Native is complete and Full is a collecting prefix, not matched complete work.",
            "Warmup service without a workflow identity is excluded.",
            "Graph replay proves a configured region was replayed, not full-model fusion.",
            "Worker intervals include CPU dispatch, waits and overlap; they are not CUDA kernel time.",
            "Sampled thread CPU is not exclusive GPU time or removable JCT.",
            "A missing tuning file does not prove the default kernel is slower.",
            "No attention/MoE/kernel-share or performance acceptance is inferred.",
        ],
        "performance_accepted": False,
    }
    assert "torch" not in sys.modules
    result["cuda_not_imported"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "output": str(args.output),
        "arms": {
            name: {
                "service": arm["workflow_service_by_phase"],
                "short_prefill": arm["prefill_turnover"]["short_single_request"],
            }
            for name, arm in result["arms"].items()
        },
    }, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
