#!/usr/bin/env python3
"""Matched native workload and predictive H2D evidence, with explicit unknowns."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
import hashlib
import os
from pathlib import Path
import shutil
from statistics import mean, median
import subprocess


def records(path: Path):
    if path.exists():
        with path.open() as stream:
            for line in stream:
                if line.strip():
                    yield json.loads(line)


def cleanup_workspaces(arm: Path) -> dict:
    clients = list(arm.glob("client_*/summary.json"))
    if len(clients) != 1:
        raise ValueError("cleanup requires one finished workload summary")
    summary = json.loads(clients[0].read_text())
    removed, retained = [], []
    for workflow in summary["workflows"]:
        base = clients[0].parent / "workflows" / workflow["instance_id"]
        workspace = base / "workspace"
        if not workspace.exists():
            continue
        if (
            workflow.get("outcome") != "completed"
            or workflow.get("sandbox_cleanup_status") != "completed"
            or not (base / "model.patch").is_file()
            or (workflow.get("artifact_collection") or {}).get("errors")
            or workspace.is_symlink()
            or not workspace.resolve().is_relative_to(
                (clients[0].parent / "workflows").resolve()
            )
        ):
            retained.append(str(workspace))
            continue
        shutil.rmtree(workspace)
        removed.append(str(workspace))
    return {"removed_workspaces": removed, "retained_workspaces": retained}


def summarize(arm: Path) -> dict | None:
    clients = list(arm.glob("client_*/summary.json"))
    if not clients:
        return None
    if len(clients) != 1:
        raise ValueError("expected one workload summary")
    summary = json.loads(clients[0].read_text())
    native_path = arm / "server/native_telemetry_status.json"
    native = json.loads(native_path.read_text()) if native_path.exists() else {}
    capacity = json.loads((arm / "server/native_capacity_census.json").read_text())["capacity"]
    last_state, forecast_costs, forecast_ages = {}, [], []
    for row in records(arm / "opportunities/admission_opportunities.jsonl"):
        if row["event"] == "admission_runtime_state":
            last_state = row
        elif row["event"] == "semantic_child_forecast":
            forecast_costs.append(row["inference_ms"])
            forecast_ages.append(row["observation_age_ms"])
    output_tokens, input_tokens = 0, 0
    submits = {}
    for row in records(arm / "server/runtime_events.sglang.jsonl"):
        attrs = row.get("attributes") or {}
        if row["kind"] == "llm_result":
            output_tokens += attrs.get("output_tokens") or 0
        elif row["kind"] == "llm_submit":
            input_tokens += attrs.get("prompt_tokens") or 0
            submits[attrs.get("request_id")] = row["ts_ms"]
    acks = {
        row["command_id"]: row
        for row in records(arm / "server/physical_action_ack.jsonl")
        if row.get("action") == "PREFETCH_GPU"
    }
    action_uses = list(records(arm / "server/physical_action_use.jsonl"))
    uses = [
        row for row in action_uses
        if row["event"] == "beliefkv_prefetch_first_service"
    ]
    first_services = {row["command_id"]: row for row in uses}
    mamba_verified_commands = set()
    for row in action_uses:
        if (
            row["event"] != "beliefkv_prefetch_mamba_forward_completed"
            or row.get("mamba_reuse") != "verified_single_request_cow_forward_completed"
        ):
            continue
        ack = acks.get(row["command_id"])
        first = first_services.get(row["command_id"])
        if (
            ack is not None and first is not None
            and ack.get("node_ids") == [row.get("node_id")]
            and all(row.get(key) == first.get(key) for key in (
                "request_id", "context_id", "context_epoch",
            ))
        ):
            mamba_verified_commands.add(row["command_id"])
    mamba_verified_bytes = sum(
        dict(acks[command_id].get("pool_bytes") or {}).get("mamba", 0)
        for command_id in mamba_verified_commands
    )
    full_reused, full_nonreuse, full_unknown = 0, 0, 0
    potential_residency, mamba_unverified = 0., 0
    for use in uses:
        ack = acks.get(use["command_id"], {})
        pool_bytes = dict(ack.get("pool_bytes") or {})
        amount = pool_bytes.get("kv", 0)
        if use["full_node_reused"] is True and set(use["reused_full_node_ids"]) == set(ack.get("node_ids") or ()):
            full_reused += amount
        elif use["full_node_reused"] is False:
            full_nonreuse += amount
        else:
            full_unknown += amount
        mamba_unverified += pool_bytes.get("mamba", 0)
        duration = max(0., use["first_service_ts_ms"] - use["ack_ts_ms"]) / 1000
        potential_residency += sum(pool_bytes.values()) * duration
    gpu = []
    with (clients[0].parent / "gpu_samples.csv").open() as stream:
        for row in csv.DictReader(stream):
            gpu.append(float(row["gpu_utilization_percent"]))
    outcomes = Counter(row["outcome"] for row in summary["workflows"])
    jct = [row["duration_seconds"] for row in summary["workflows"]
           if row["outcome"] == "completed"]
    duration = summary["duration_seconds"]
    return {
        "run": str(arm.resolve()), "duration_seconds": duration,
        "workflow_count": summary["workflow_count"], "outcomes": dict(outcomes),
        "completed_workflows_per_hour": summary["completed_workflows"] * 3600 / duration,
        "completed_jct_p50_seconds": median(jct) if jct else None,
        "completed_jct_mean_seconds": mean(jct) if jct else None,
        "completion_correctness": "not_independently_graded; do not call this correct-task throughput",
        "workflow_ids": sorted(row["instance_id"] for row in summary["workflows"]),
        "llm_request_count": summary["llm_request_count"],
        "tool_call_count": summary["tool_call_count"],
        "completed_request_output_tokens": output_tokens,
        "completed_request_output_tokens_per_second": output_tokens / duration,
        "submitted_input_tokens": input_tokens,
        "gpu_utilization_mean_percent": mean(gpu) if gpu else None,
        "native_pool_capacity": capacity,
        "host_pool_evidence": native.get("host_pool_evidence"),
        "cache_evidence": (native.get("request_cache_evidence") or {}).get("all"),
        "host_eviction_attribution": native.get("host_block_eviction_attribution"),
        "telemetry_status": {
            name: native.get(name) for name in
            ("dropped_records", "failed_records", "writer_error")
        },
        "runtime_state": last_state,
        "forecast_inference_mean_ms": mean(forecast_costs) if forecast_costs else None,
        "forecast_observation_age_p50_ms": median(forecast_ages) if forecast_ages else None,
        "predictive_h2d_acks": len(acks),
        "predictive_h2d_ack_bytes": sum(row["num_bytes"] for row in acks.values()),
        "ack_without_first_service_count": len(
            set(acks) - {row["command_id"] for row in uses}
        ),
        "first_service_records": len(uses),
        "full_first_service_outcomes": {
            name: sum(row["full_node_reused"] is value for row in uses)
            for name, value in (("reused", True), ("not_reused", False), ("unknown", None))
        },
        "verified_full_reused_bytes": full_reused,
        "verified_full_nonreuse_bytes": full_nonreuse,
        "full_reuse_unverified_bytes": full_unknown,
        "verified_mamba_forward_count": len(mamba_verified_commands),
        "verified_mamba_forward_bytes": mamba_verified_bytes,
        "mamba_first_service_unverified_bytes": mamba_unverified - mamba_verified_bytes,
        "ack_to_first_service_byte_seconds_upper_bound": potential_residency,
        "interpretation": (
            "ACKs are not benefits. Residency is an upper bound if pages were evicted "
            "before first service. Mamba is not counted as wasted merely because "
            "node-level reuse is unverified. H2D exposed synchronous waiting has "
            "not been separately identified by this aggregate report."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--cleanup-arm", type=Path)
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--root-count", type=int, default=36)
    parser.add_argument("--arm-order", default="reactive predictive_h2d")
    parser.add_argument("--semantic-artifact", type=Path)
    parser.add_argument("--activation-wall-clock-seconds", type=float, default=14400.)
    parser.add_argument("--workload-manifest", type=Path)
    parser.add_argument("--prepare-host", type=int, choices=(0, 1), default=0)
    parser.add_argument("--h2d-seed", type=Path)
    args = parser.parse_args()
    if args.cleanup_arm:
        print(json.dumps(cleanup_workspaces(args.cleanup_arm), indent=2))
        return
    if args.run_root is None:
        raise ValueError("--run-root is required")
    if args.initialize:
        root = Path(__file__).resolve().parents[1]
        artifact = (
            args.semantic_artifact
            or root / "experiments/models/child_semantic_work_frozen_phase_20261001_v1/semantic_event_calibrated.json"
        ).resolve()
        manifest = (
            args.workload_manifest
            or root / "configs/migration/qwen35_native_reactive_overlapped_128root_workload_2026-09-23.json"
        ).resolve()
        workloads = json.loads(manifest.read_text())["workloads"][:args.root_count]
        patch = root / "patches/sglang-v0.5.20-beliefkv-staging.patch"
        plan = {
            "scope": (
                "single-arm development mechanism observation; no throughput comparison"
                if args.arm_order.split() == ["predictive_h2d"]
                else "single-pair development benefit validation, not final paper test"
            ),
            "code_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True,
            ).strip(),
            "root_count": args.root_count, "server_running": 48,
            "workflow_arrival_batch_size": 0,
            "workflow_arrival_batch_interval_ms": 0,
            "activation_wall_clock_seconds": args.activation_wall_clock_seconds,
            "recursion_limit": 2048, "finalization_reserve_steps": 32,
            "native_reactive_guard_profile": True,
            "completion_gate_enabled": False,
            "fanout_profile": os.environ.get("FANOUT_PROFILE", "native_in_graph_1to4"),
            "context_tokens": 131072, "max_completion_tokens": 8192,
            "sampling_seed": 21, "host_numa_node": 1,
            "mem_fraction_static": .94,
            "host_gb": 200, "host_split": "matches_actual_device_pool_bytes",
            "mamba_full_memory_ratio": .9, "native_write_policy": "write_back",
            "semantic_artifact": str(artifact),
            "semantic_artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "workload_manifest": str(manifest),
            "workload_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "workload_instance_ids_in_manifest_order": [
                row["instance_id"] for row in workloads
            ],
            "sglang_patch_sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
            "priority_in_both_arms": True,
            "prepare_host_in_both_arms": bool(args.prepare_host),
            "shared_pressure_parent_parking": bool(args.prepare_host),
            "prepare_policy": "live_wait_join_safe_input;host_no_reclaim;real_allocator_pressure",
            "order": args.arm_order.split(),
        }
        if args.h2d_seed:
            plan["h2d_seed_artifact"] = str(args.h2d_seed.resolve())
            plan["h2d_seed_sha256"] = hashlib.sha256(args.h2d_seed.read_bytes()).hexdigest()
        (args.run_root / "ab_plan.json").write_text(
            json.dumps(plan, indent=2) + "\n", encoding="utf-8",
        )
        return
    arms = {
        name: summarize(args.run_root / name)
        for name in ("reactive", "predictive_h2d")
    }
    missing = [name for name, value in arms.items() if value is None]
    if missing and not args.allow_incomplete:
        raise ValueError(f"missing terminal arms: {missing}")
    report = {"status": "partial" if missing else "complete", "arms": arms}
    plan_path = args.run_root / "ab_plan.json"
    expected_prepare = (
        json.loads(plan_path.read_text()).get("prepare_host_in_both_arms", False)
        if plan_path.exists() else False
    )
    for name, arm in arms.items():
        if arm is None:
            continue
        state = arm["runtime_state"]
        if not state or state["physical_disabled"]:
            raise ValueError(f"{name}: missing runtime evidence or disabled physical ledger")
        if state["prepare_host"] != expected_prepare or not state["final_stage_priority"]:
            raise ValueError(f"{name}: mismatched PREPARE/priority configuration")
        expected = name == "predictive_h2d"
        if state["final_stage_prefetch"] != expected or state["semantic_worker_configured"] != expected:
            raise ValueError(f"{name}: wrong predictive/model configuration")
        if expected and (
            state["counts"].get("semantic_worker_disabled")
            or not state["counts"].get("semantic_worker_ready")
        ):
            raise ValueError(f"{name}: semantic worker unavailable")
        if not expected and arm["predictive_h2d_acks"]:
            raise ValueError("reactive baseline contains predictive H2D")
    if not missing:
        r, p = arms.values()
        if r["workflow_ids"] != p["workflow_ids"]:
            raise ValueError("A/B workloads differ")
        if r["native_pool_capacity"] != p["native_pool_capacity"]:
            raise ValueError("A/B physical pool capacities differ")
        report["completed_throughput_relative_change"] = (
            p["completed_workflows_per_hour"] / r["completed_workflows_per_hour"] - 1
            if r["completed_workflows_per_hour"] else None
        )
        client_r = next((args.run_root / "reactive").glob("client_*/summary.json"))
        client_p = next((args.run_root / "predictive_h2d").glob("client_*/summary.json"))
        durations = [
            {row["instance_id"]: row["duration_seconds"]
             for row in json.loads(path.read_text())["workflows"]
             if row["outcome"] == "completed"}
            for path in (client_r, client_p)
        ]
        paired = sorted(set(durations[0]) & set(durations[1]))
        report["paired_completed_workflows"] = len(paired)
        report["paired_mean_jct_seconds"] = {
            name: mean(values[key] for key in paired) if paired else None
            for name, values in zip(("reactive", "predictive_h2d"), durations)
        }
    (args.run_root / "comparison.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps({name: (
        {key: value[key] for key in (
            "completed_workflows_per_hour", "predictive_h2d_acks",
            "verified_full_reused_bytes", "forecast_inference_mean_ms",
        )} if value else None
    ) for name, value in arms.items()}, indent=2))


if __name__ == "__main__":
    main()
