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

from beliefkv.experiments.arrival_schedule import build_workflow_arrivals

def records(path: Path):
    if path.exists():
        with path.open() as stream:
            for line in stream:
                if line.strip():
                    yield json.loads(line)


def cleanup_workspaces(arm: Path, *, include_clean_planned_children: bool = False) -> dict:
    clients = list(arm.glob("client_*/summary.json")) + list(arm.glob("workloads/summary.json"))
    if len(clients) != 1:
        raise ValueError("cleanup requires one finished workload summary")
    summary = json.loads(clients[0].read_text())
    removed, retained = [], []
    for workflow in summary["workflows"]:
        base = clients[0].parent / "workflows" / workflow["instance_id"]
        workspaces = [base / "workspace"]
        if include_clean_planned_children:
            workspaces.extend(sorted(base.glob("planned_children/*/workspace")))
        workspaces = [workspace for workspace in workspaces if workspace.exists()]
        if not workspaces:
            continue
        if (
            workflow.get("outcome") != "completed"
            or workflow.get("sandbox_cleanup_status") != "completed"
            or not (base / "model.patch").is_file()
            or (workflow.get("artifact_collection") or {}).get("errors")
        ):
            retained.extend(str(workspace) for workspace in workspaces)
            continue
        for workspace in workspaces:
            if (
                workspace.is_symlink()
                or not workspace.resolve().is_relative_to(base.resolve())
                or not workspace.resolve().is_relative_to(
                    (clients[0].parent / "workflows").resolve()
                )
            ):
                retained.append(str(workspace))
                continue
            if workspace != base / "workspace":
                # Keep any child edits that were not archived in the root patch.
                status = subprocess.run(
                    ["git", "-C", str(workspace), "status", "--porcelain=v1",
                     "--untracked-files=all"],
                    capture_output=True, text=True, timeout=30, check=False,
                )
                if (
                    not (base / "child_reports.json").is_file()
                    or status.returncode != 0 or status.stdout.strip()
                ):
                    retained.append(str(workspace))
                    continue
            shutil.rmtree(workspace)
            removed.append(str(workspace))
    return {"removed_workspaces": removed, "retained_workspaces": retained}


def workload_balance(reactive: dict, predictive: dict, *, baseline: str = "reactive") -> dict:
    """Describe realized work without conditioning the headline JCT on it."""
    quantities = (
        "llm_request_count", "tool_call_count", "submitted_input_tokens",
        "completed_request_output_tokens",
    )
    return {
        "scope": "realized live demand; not a correction for counterfactual work",
        "same_logical_trajectory_verified": False,
        "matched_seed_guarantees_identical_trajectory": False,
        "baseline": baseline,
        f"relative_changes_predictive_vs_{baseline}": {
            key: predictive[key] / reactive[key] - 1 if reactive[key] else None
            for key in quantities
        },
        "performance_claim": (
            "single live pair only; trace drift and run-level variation must be "
            "reported; no isolated KV-policy throughput claim"
        ),
    }


def workflow_trajectory_audit(arm: Path) -> dict:
    client = next(arm.glob("client_*/summary.json"))
    terminal = json.loads(client.read_text())["workflows"]
    values = {
        row["instance_id"]: {
            "outcome": row["outcome"], "duration_seconds": row["duration_seconds"],
            "llm_calls": 0, "tool_calls": 0, "join_rounds": 0, "children": 0,
            "prompt_tokens": 0, "output_tokens": 0, "internal_requests": 0,
            "request_sequence": [], "missing_prompt_fingerprints": 0,
        } for row in terminal
    }
    workflow_tasks = {}
    for path in sorted((client.parent / "workflows").glob("*/runtime_events.deepagents.jsonl")):
        task = path.parent.name
        if task not in values:
            continue
        value = values[task]
        by_rid = {}
        for row in records(path):
            workflow_tasks[row["workflow_id"]] = task
            attrs = row.get("attributes") or {}
            if row["kind"] == "llm_submit":
                if attrs.get("runtime_internal"):
                    value["internal_requests"] += 1
                else:
                    value["llm_calls"] += 1
                    fingerprint = attrs.get("prompt_semantic_sha256")
                    value["missing_prompt_fingerprints"] += fingerprint is None
                    value["request_sequence"].append({
                        "prompt_semantic_sha256": fingerprint,
                        "model_result": None,
                    })
                    if attrs.get("request_id"):
                        by_rid[attrs["request_id"]] = len(value["request_sequence"]) - 1
            elif row["kind"] == "llm_result" and not attrs.get("runtime_internal"):
                index = by_rid.get(attrs.get("request_id"))
                if index is not None:
                    value["request_sequence"][index]["model_result"] = {
                        "finish_reason": attrs.get("finish_reason"),
                        "output_chars": attrs.get("output_chars"),
                        "tool_call_count": attrs.get("tool_call_count"),
                        "structured_action_names": attrs.get("structured_action_names"),
                    }
            elif row["kind"] == "tool_start":
                value["tool_calls"] += 1
            elif row["kind"] == "join_create":
                value["join_rounds"] += 1
                value["children"] += len(row.get("member_invocation_ids") or ())
    for row in records(arm / "server/runtime_events.sglang.jsonl"):
        task = workflow_tasks.get(row.get("workflow_id"))
        if task is None:
            continue
        attrs = row.get("attributes") or {}
        if row["kind"] == "llm_submit":
            values[task]["prompt_tokens"] += attrs.get("prompt_tokens") or 0
        elif row["kind"] == "llm_result":
            values[task]["output_tokens"] += attrs.get("output_tokens") or 0
    return values


def paired_trajectory_report(
    reactive: dict, predictive: dict, *, baseline: str = "reactive"
) -> dict:
    rows = []
    for task in sorted(set(reactive) | set(predictive)):
        r, p = reactive.get(task), predictive.get(task)
        same = None
        divergence = None
        if r is not None and p is not None:
            rseq, pseq = r["request_sequence"], p["request_sequence"]
            if not r["missing_prompt_fingerprints"] and not p["missing_prompt_fingerprints"] and rseq and pseq:
                same = rseq == pseq
                for index in range(max(len(rseq), len(pseq))):
                    if index >= min(len(rseq), len(pseq)) or rseq[index] != pseq[index]:
                        divergence = index + 1
                        break
        rows.append({
            "task": task, baseline: {k: v for k, v in (r or {}).items() if k != "request_sequence"},
            "predictive": {k: v for k, v in (p or {}).items() if k != "request_sequence"},
            "observed_request_sequence_equal": same,
            "first_observed_request_divergence_ordinal": divergence,
        })
    return {
        "scope": "all workflows retained; observed order and fingerprints, not token-exact execution proof",
        "baseline": baseline,
        "workflows": rows,
        "observed_request_sequence_equal_count": sum(row["observed_request_sequence_equal"] is True for row in rows),
        "observed_request_sequence_different_count": sum(row["observed_request_sequence_equal"] is False for row in rows),
        "observed_request_sequence_unknown_count": sum(row["observed_request_sequence_equal"] is None for row in rows),
        "interpretation": (
            "Submission order can differ without a content change. Equality of "
            "prompt/result metadata is not equality of all generated token IDs. "
            "Do not drop divergent workflows or claim causal speedup on a "
            "post-treatment subset."
        ),
    }


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
            or row.get("mamba_reuse") not in (
                "verified_single_request_cow_forward_completed",
                "verified_per_request_cow_forward_completed",
            )
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
        "context_prefix_reuse_evidence": native.get("context_prefix_reuse_evidence"),
        "telemetry_status": {
            name: native.get(name) for name in
            ("dropped_records", "failed_records", "writer_error")
        },
        "runtime_state": last_state,
        "forecast_inference_mean_ms": mean(forecast_costs) if forecast_costs else None,
        "forecast_observation_age_p50_ms": median(forecast_ages) if forecast_ages else None,
        "predictive_h2d_acks": len(acks),
        "predictive_h2d_ack_bytes": sum(row["num_bytes"] for row in acks.values()),
        "controlled_h2d_by_source": {
            source or "unknown": {
                "acks": sum(row.get("source") == source for row in acks.values()),
                "bytes": sum(
                    row["num_bytes"] for row in acks.values()
                    if row.get("source") == source
                ),
                "full_bytes": sum(
                    dict(row.get("pool_bytes") or {}).get("kv", 0)
                    for row in acks.values() if row.get("source") == source
                ),
                "verified_full_reused_bytes": sum(
                    dict(acks[use["command_id"]].get("pool_bytes") or {}).get("kv", 0)
                    for use in uses if use["command_id"] in acks
                    and acks[use["command_id"]].get("source") == source
                    and use["full_node_reused"] is True
                    and set(use["reused_full_node_ids"]) == set(acks[use["command_id"]].get("node_ids") or ())
                ),
            }
            for source in ("join_ticket", "tool_wait", "admission", "execution_handoff", None)
        },
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
    parser.add_argument("--allow-degraded-runtime", action="store_true",
                        help="Export diagnostics for a disabled physical lane; never a valid A/B comparison.")
    parser.add_argument("--cleanup-arm", type=Path)
    parser.add_argument("--cleanup-clean-planned-children", action="store_true")
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--verify-frozen-plan", action="store_true")
    parser.add_argument("--root-count", type=int, default=36)
    parser.add_argument("--arm-order", default="reactive predictive_h2d")
    parser.add_argument("--arrival-batch-size", type=int, default=0)
    parser.add_argument("--arrival-batch-interval-ms", type=int, default=0)
    parser.add_argument("--semantic-artifact", type=Path)
    parser.add_argument("--activation-wall-clock-seconds", type=float, default=14400.)
    parser.add_argument("--workload-manifest", type=Path)
    parser.add_argument("--prepare-host", type=int, choices=(0, 1), default=0)
    parser.add_argument("--host-split", default="80:20")
    parser.add_argument("--h2d-seed", type=Path)
    parser.add_argument("--tool-timing-artifact", type=Path)
    parser.add_argument("--enable-tool-timing", type=int, choices=(0, 1), default=0)
    parser.add_argument("--prefetch-lead-ms", type=int, default=1000)
    parser.add_argument("--semantic-work-statistic", choices=("upper", "center"), default="upper")
    parser.add_argument("--eos-protocol-window-ms", type=int, default=50)
    parser.add_argument("--transfer-service-seed", type=Path)
    parser.add_argument("--sampling-seed", type=int, default=21)
    parser.add_argument("--repetition-id", type=int, default=0)
    args = parser.parse_args()
    if args.verify_frozen_plan:
        root = Path(__file__).resolve().parents[1]
        plan = json.loads((args.run_root / "ab_plan.json").read_text())
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True,
        ).strip()
        if revision != plan["code_commit"]:
            raise ValueError("code commit changed between policy arms")
        subprocess.run(["git", "diff", "--exit-code", "HEAD", "--"], cwd=root, check=True)
        paths = [
            (plan["workload_manifest"], plan["workload_sha256"]),
            (plan["semantic_artifact"], plan["semantic_artifact_sha256"]),
            (root / "patches/sglang-v0.5.20-beliefkv-staging.patch", plan["sglang_patch_sha256"]),
        ]
        for field, digest in (
            ("h2d_seed_artifact", "h2d_seed_sha256"),
            ("tool_timing_artifact", "tool_timing_sha256"),
            ("transfer_service_seed", "transfer_service_seed_sha256"),
        ):
            if plan.get(digest):
                paths.append((plan[field], plan[digest]))
        for path, digest in paths:
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                raise ValueError(f"frozen artifact changed: {path}")
        return
    if args.cleanup_arm:
        print(json.dumps(cleanup_workspaces(
            args.cleanup_arm,
            include_clean_planned_children=args.cleanup_clean_planned_children,
        ), indent=2))
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
        available = json.loads(manifest.read_text())["workloads"]
        if not 0 < args.root_count <= len(available):
            raise ValueError("requested root count exceeds the workload manifest")
        if args.sampling_seed < 0 or args.repetition_id < 0:
            raise ValueError("sampling seed and repetition ID must be nonnegative")
        workloads = available[:args.root_count]
        if len({row["instance_id"] for row in workloads}) != args.root_count:
            raise ValueError("duplicate workflows in arrival table")
        order = args.arm_order.split()
        if order not in (
            ["reactive", "predictive_h2d"], ["predictive_h2d", "reactive"],
            ["native", "predictive_h2d"], ["predictive_h2d", "native"],
            ["predictive_h2d"],
        ):
            raise ValueError("unsupported policy arm order")
        if (args.arrival_batch_size == 0) != (args.arrival_batch_interval_ms == 0):
            raise ValueError("arrival batch size and interval must be enabled together")
        arrivals = build_workflow_arrivals(
            args.root_count,
            mode="batched" if args.arrival_batch_size else "simultaneous",
            batch_size=args.arrival_batch_size or args.root_count,
            batch_interval_seconds=args.arrival_batch_interval_ms / 1000,
        )
        native_pair = "native" in order
        patch = root / "patches/sglang-v0.5.20-beliefkv-staging.patch"
        plan = {
            "prefetch_lead_ms": args.prefetch_lead_ms,
            "semantic_work_statistic": args.semantic_work_statistic,
            "eos_protocol_window_ms": args.eos_protocol_window_ms,
            "tool_timing_enabled": bool(args.enable_tool_timing),
            "tool_timing_artifact": str(args.tool_timing_artifact.resolve()) if args.tool_timing_artifact else None,
            "tool_timing_sha256": (
                hashlib.sha256(args.tool_timing_artifact.read_bytes()).hexdigest()
                if args.tool_timing_artifact else None
            ),
            "transfer_service_seed": str(args.transfer_service_seed.resolve()) if args.transfer_service_seed else None,
            "transfer_service_seed_sha256": (
                hashlib.sha256(args.transfer_service_seed.read_bytes()).hexdigest()
                if args.transfer_service_seed and args.transfer_service_seed.is_file() else None
            ),
            "tool_prepare_in_both_arms": bool(args.enable_tool_timing and args.prepare_host),
            "tool_prefetch_predictive_only": bool(args.enable_tool_timing),
            "scope": (
                "single-arm development mechanism observation; no throughput comparison"
                if args.arm_order.split() == ["predictive_h2d"]
                else "single-pair live pressure exploration; trajectory-sensitive, not final paper test"
            ),
            "repetition_id": args.repetition_id,
            "trajectory_control": "live_agent_uncontrolled_realized_trajectory",
            "same_seed_is_not_same_trajectory": True,
            "formal_paired_repetition_target": 4,
            "formal_repetition_order": (
                ["native predictive_h2d", "predictive_h2d native"] if native_pair
                else ["reactive predictive_h2d", "predictive_h2d reactive"]
            ),
            "code_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True,
            ).strip(),
            "root_count": args.root_count, "server_running": 48,
            "workflow_arrival_batch_size": args.arrival_batch_size,
            "workflow_arrival_batch_interval_ms": args.arrival_batch_interval_ms,
            "arrival_schedule": [
                {"instance_id": row["instance_id"],
                 "offset_seconds": arrival.scheduled_offset_seconds}
                for row, arrival in zip(workloads, arrivals)
            ],
            "activation_wall_clock_seconds": args.activation_wall_clock_seconds,
            "recursion_limit": 2048, "finalization_reserve_steps": 32,
            "native_reactive_guard_profile": True,
            "completion_gate_enabled": False,
            "child_final_report_shadow": os.environ.get("CHILD_FINAL_REPORT_SHADOW", "1") == "1",
            "fanout_profile": os.environ.get("FANOUT_PROFILE", "native_in_graph_2to4"),
            "in_graph_initial_tool_choice": "task",
            "in_graph_initial_parallel_tool_calls": True,
            "in_graph_initial_fanout_generation_bounds": (
                [2, 4]
                if os.environ.get("FANOUT_PROFILE", "native_in_graph_2to4") == "native_in_graph_2to4"
                else None
            ),
            "in_graph_named_task_constraint": (
                "BeliefKV-only native task repetition; first 2to4 generation bounded "
                "to 2-4; no response rejection; complete prompt tool schema unchanged"
            ),
            "requested_children_per_round": (
                [2, 4] if os.environ.get("FANOUT_PROFILE", "native_in_graph_2to4") == "native_in_graph_2to4"
                else [1, 4]
            ),
            "join_prediction_semantics": (
                "complete ALL membership; prefetch from the last observed unfinished child; "
                "no joint calibration claim from marginal child intervals"
            ),
            "prediction_regime_status": (
                "frozen heads under a changed root/fanout regime; diagnostic, "
                "not assumed calibrated or a source of offline net-benefit targets"
            ),
            "context_tokens": 131072, "max_completion_tokens": 8192,
            "sampling_seed": args.sampling_seed, "temperature": 0., "host_numa_node": 1,
            "mem_fraction_static": .94,
            "host_gb": 200, "host_split": args.host_split,
            "host_split_semantics": (
                "matches_actual_device_pool_bytes" if args.host_split == "auto"
                else "explicit_full_mamba_percentages"
            ),
            "resident_first_in_both_arms": True,
            "execution_handoff_predictive_only": True,
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
            "shared_path_profiling": "aggregated CPU inclusive/exclusive scopes; no CUDA synchronization",
            "restore_wait_profiling": (
                "one in 16 load-consuming prefills; asynchronous CUDA events "
                "around per-layer dependencies in both arms; includes event overhead"
            ),
            "native_baseline_disables": (
                ["BeliefKV control channel", "admission ordering", "predictors",
                 "prepare_host", "resident_first", "final/restore priority", "execution_handoff"]
                if native_pair else []
            ),
            "policy_configuration": {
                name: {
                    "control": name != "native",
                    "resident_first": name != "native",
                    "final_stage_priority": name != "native",
                    "prepare_host": bool(args.prepare_host) and name != "native",
                    "predictive_h2d": name == "predictive_h2d",
                    "execution_handoff": name == "predictive_h2d",
                } for name in order
            },
            "order": order,
            "measurement_windows_seconds": [[0, 3600], [3600, 7200], [7200, None]],
            "headline_metric": "all-arrival completed workflows per collection hour",
        }
        if native_pair:
            for key in (
                "resident_first_in_both_arms", "priority_in_both_arms",
                "prepare_host_in_both_arms", "tool_prepare_in_both_arms",
            ):
                plan[key] = False
        if args.h2d_seed:
            plan["h2d_seed_artifact"] = str(args.h2d_seed.resolve())
            plan["h2d_seed_sha256"] = hashlib.sha256(args.h2d_seed.read_bytes()).hexdigest()
        (args.run_root / "ab_plan.json").write_text(
            json.dumps(plan, indent=2) + "\n", encoding="utf-8",
        )
        return
    plan_path = args.run_root / "ab_plan.json"
    plan = json.loads(plan_path.read_text()) if plan_path.exists() else {}
    order = plan.get("order", ["reactive", "predictive_h2d"])
    arms = {
        name: summarize(args.run_root / name)
        for name in order
    }
    missing = [name for name, value in arms.items() if value is None]
    if missing and not args.allow_incomplete:
        raise ValueError(f"missing terminal arms: {missing}")
    report = {"status": "partial" if missing else "complete", "arms": arms}
    degraded = []
    expected_prepare = plan.get("prepare_host_in_both_arms", False)
    for name, arm in arms.items():
        if arm is None:
            continue
        state = arm["runtime_state"]
        if name == "native":
            if state or arm["predictive_h2d_acks"]:
                raise ValueError("native baseline contains BeliefKV control or predictive H2D")
            continue
        if not state:
            raise ValueError(f"{name}: missing runtime evidence or disabled physical ledger")
        if state["physical_disabled"]:
            if not args.allow_degraded_runtime:
                raise ValueError(f"{name}: missing runtime evidence or disabled physical ledger")
            degraded.append(name)
        arm_prepare = plan.get("policy_configuration", {}).get(name, {}).get(
            "prepare_host", expected_prepare
        )
        if state["prepare_host"] != arm_prepare or not state["final_stage_priority"]:
            raise ValueError(f"{name}: mismatched PREPARE/priority configuration")
        for setting in ("semantic_work_statistic", "eos_protocol_window_ms"):
            if setting in plan and state.get(setting) != plan[setting]:
                raise ValueError(f"{name}: mismatched {setting}")
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
    report["comparison_eligible"] = len(order) == 2 and not missing and not degraded
    report["degraded_runtime_arms"] = degraded
    if degraded:
        report["status"] = "degraded_diagnostic"
        report["performance_claim"] = (
            "Physical actions disabled during an arm; retain native/agent evidence "
            "but do not claim a working shared-residency baseline or predictive speedup."
        )
    if not missing and len(order) == 2:
        baseline = "native" if "native" in order else "reactive"
        r, p = arms[baseline], arms["predictive_h2d"]
        report["baseline"] = baseline
        if r["workflow_ids"] != p["workflow_ids"]:
            raise ValueError("A/B workloads differ")
        if r["native_pool_capacity"] != p["native_pool_capacity"]:
            raise ValueError("A/B physical pool capacities differ")
        report["completed_throughput_relative_change"] = (
            p["completed_workflows_per_hour"] / r["completed_workflows_per_hour"] - 1
            if r["completed_workflows_per_hour"] else None
        )
        client_r = next((args.run_root / baseline).glob("client_*/summary.json"))
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
            for name, values in zip((baseline, "predictive_h2d"), durations)
        }
        report["workload_balance"] = workload_balance(r, p, baseline=baseline)
        trajectory = paired_trajectory_report(
            workflow_trajectory_audit(args.run_root / baseline),
            workflow_trajectory_audit(args.run_root / "predictive_h2d"),
            baseline=baseline,
        )
        trajectory_path = args.run_root / "workflow_trajectory_comparison.json"
        trajectory_path.write_text(json.dumps(trajectory, indent=2) + "\n")
        report["workflow_trajectory_comparison"] = {
            "path": str(trajectory_path),
            **{key: value for key, value in trajectory.items() if key != "workflows"},
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
