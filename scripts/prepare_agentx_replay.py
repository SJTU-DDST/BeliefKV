#!/usr/bin/env python3
"""Prepare complete published AgentX sessions for direct baseline replay."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.agentx_replay import build_agentx_plan, workflow_from_agentx
from beliefkv.experiments.causal_replay import evaluate_causal_plan
from beliefkv.experiments.replay_timing import AGENTX_TIMING, ReplayTimingPolicy
from scripts.audit_agentx_workload import author_module, chain_inventory, HARNESS_COMMIT


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def author_helpers(checkout):
    module, evidence = author_module(checkout)
    relative = "src/aiperf/timing/replay_dependencies.py"
    payload = subprocess.check_output([
        "git", "-C", str(checkout), "show", HARNESS_COMMIT + ":" + relative,
    ])
    if (checkout / relative).read_bytes() != payload:
        raise ValueError("author interval reference has local changes")
    parsed = ast.parse(payload)
    parsed.body = [
        item for item in parsed.body
        if isinstance(item, (ast.ClassDef, ast.FunctionDef))
        and item.name in (
            "ReplayTurnKey", "RecordedTurnInterval", "infer_cross_stream_predecessors",
        )
        or isinstance(item, ast.ImportFrom) and item.module == "__future__"
    ]
    exec(compile(parsed, str(checkout / relative), "exec"), module.__dict__)
    evidence.append({"path": relative, "sha256": hashlib.sha256(payload).hexdigest()})

    def partition(pairs):
        models = [
            (index, (
                module.WekaStreamingRequest if row["type"] == "s"
                else module.WekaNormalRequest
            ).model_validate(row))
            for index, row in pairs
        ]
        main, workers, _ = chain_inventory(module, models)
        return [index for index, _ in main], [
            {"indices": row["outer_indices"], "auxiliary": row["auxiliary"]}
            for row in workers
        ]

    def predecessors(calls):
        positions, keys, intervals = Counter(), {}, []
        for iid, call in calls:
            key = module.ReplayTurnKey(iid, positions[iid])
            positions[iid] += 1
            keys[key] = call.request_id
            intervals.append(module.RecordedTurnInterval(
                key, iid, call.recorded_start_s * 1000., call.recorded_api_s * 1000.,
            ))
        return {
            keys[key]: tuple(keys[pred] for pred in required)
            for key, required in module.infer_cross_stream_predecessors(intervals).items()
        }

    return partition, predecessors, evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population-report", type=Path, required=True)
    parser.add_argument("--source-full", type=Path, required=True)
    parser.add_argument("--author-checkout", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--roots", type=int, default=18)
    parser.add_argument("--timing-preset", choices=("agentx", "raw"), default="agentx")
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("Preserve earlier replay preparation.")
    population = json.loads(args.population_report.read_bytes())
    if (
        not 1 <= args.roots <= 393
        or population.get("complete_relative_to_pinned_public_release") is not True
        or sha(args.source_full) != population["full_source_sha256"]
    ):
        raise ValueError("pinned whole-public-row population required")
    eligible = {
        row["id"]: row for row in population["rows"]
        if row["fits_local_context_without_removing_requests"]
        and row["structural_audit"]["parallel_join_groups"]
        and not row["structural_audit"]["defects"]
    }
    partition, predecessors, code = author_helpers(args.author_checkout)
    candidates, rejected, catalog = [], [], []
    with args.source_full.open() as source:
        for line in source:
            raw = json.loads(line)
            if raw["id"] not in eligible:
                continue
            digest = hashlib.sha256(json.dumps(
                raw, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode()).hexdigest()
            if digest != eligible[raw["id"]]["whole_public_row_sha256"]:
                raise ValueError("whole AgentX row differs from population qualification")
            workflow = workflow_from_agentx(raw, partition, predecessors)
            plan = build_agentx_plan((workflow,))
            # Every timer is an external interval: original GPU service remains
            # an unresolved operation. Selection never uses a measured arm.
            maximum = max((node.delay_ms / 1000. for node in plan.nodes), default=0.)
            catalog.append({
                "id": raw["id"], "maximum_external_timer_seconds": maximum,
                "requests": len(plan.request_usage),
                "output_tokens": sum(size[1] for size in plan.request_usage.values()),
                "joins": sum(
                    node.event is not None and node.event.kind.value == "join_create"
                    for node in plan.nodes
                ),
            })
            candidates.append((workflow, maximum))
    candidates.sort(key=lambda item: hashlib.sha256(
        ("21:" + item[0].source_trace_id).encode(),
    ).hexdigest())
    args.output_dir.mkdir(parents=True)
    with (args.output_dir / "candidate_qualification.json").open("x") as stream:
        json.dump({
            "requested_roots": args.roots, "eligible_whole_sessions": len(candidates),
            "external_timer_limit_seconds": None,
            "long_gaps_are_runtime_idle_guarded_not_session_rejections": True,
            "catalog": catalog, "rejected_whole_sessions": rejected,
            "requests_removed_from_retained_sessions": 0,
            "source_full_sha256": population["full_source_sha256"],
            "gpu_ready": False,
        }, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if len(candidates) < args.roots:
        raise ValueError(
            f"only {len(candidates)} whole eligible sessions; requested {args.roots}; "
            "do not silently lower workflow count or truncate sessions"
        )
    selected = candidates[:args.roots]
    workflows = tuple(workflow for workflow, _ in selected)
    plan = build_agentx_plan(workflows)
    fixture = evaluate_causal_plan(plan, dict.fromkeys(plan.request_usage, 0.))
    contents = {
        "timing.json": {
            "schema_version": 1,
            "policy": asdict(AGENTX_TIMING if args.timing_preset == "agentx" else ReplayTimingPolicy()),
            "reference_preset": args.timing_preset,
            "per_turn_delay_clamping": False,
            "original_timestamps_and_delays_preserved_in_plan": True,
        },
        "workflows.json": {"workflows": [asdict(workflow) for workflow in workflows]},
        "plan.json": {
            "nodes": [{
                "node_id": node.node_id, "workflow_id": node.workflow_id,
                "dependencies": list(node.dependencies), "delay_ms": node.delay_ms,
                "event": node.event.to_dict() if node.event else None,
                "request_id": node.request_id,
            } for node in plan.nodes],
            "request_usage": dict(plan.request_usage),
            "native_completion_boundaries_verified": False,
            "omitted_diagnostic_events": 0,
        },
    }
    for name, value in contents.items():
        with (args.output_dir / name).open("x") as stream:
            json.dump(value, stream, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "workload_kind": "agentx_whole_public_trace_structural_adaptation",
        "population_report": str(args.population_report.resolve()),
        "population_report_sha256": sha(args.population_report),
        "source_full_sha256": sha(args.source_full), "author_helpers": code,
        "seed": 21, "roots": len(workflows), "summary": plan.summary(),
        "whole_session_external_gap_limit_seconds": None,
        "runtime_timing": contents["timing.json"],
        "whole_sessions_rejected": rejected,
        "selected_ids": [workflow.source_trace_id for workflow in workflows],
        "maximum_anonymous_gap_seconds": max(gap for _, gap in selected),
        "zero_model_service_dependency_fixture_ms": fixture["workflow_elapsed_ms"],
        "original_api_durations_used_as_model_timers": False,
        "native_source_capture_required": False,
        "published_workflow_jct_evaluable": True,
        "requests_removed_from_retained_sessions": 0,
        "future_graph_visible_only_to_private_driver": True,
        "context_lifetime_policy": {
            "classification_source": "explicit_source_context_lifetime_only",
            "role_status_last_call_and_future_reuse_are_not_lifetime_labels": True,
            "unknown_return_retires_context": False,
            "persistent_or_unknown_cache_is_capacity_evictable": True,
            "workflow_end_closes_remaining_contexts": True,
            "agent_counts": dict(Counter(
                agent.context_lifetime for workflow in workflows for agent in workflow.agents
            )),
        },
        "tool_identity_available": False,
        "anonymous_external_phases_are_not_verified_original_tools": True,
        "source_chain_overflows_flattened_like_author_harness": True,
        "original_eos_and_original_output_content_available": False,
        "project_disjointness_verified": False,
        "original_task_correctness_verified": False,
        "gpu_ready": False, "performance_accepted": False,
        "remaining": [
            "synthetic_token_and_prefix_fidelity_adapter",
            "native_http_runtime_and_stopping_qualification",
            "requested_baseline_adapters",
        ],
        "artifacts": {name: sha(args.output_dir / name) for name in contents},
        "analysis_source_sha256": {
            name: sha(ROOT / name) for name in (
                "scripts/prepare_agentx_replay.py",
                "beliefkv/experiments/agentx_replay.py",
                "scripts/audit_agentx_workload.py",
            )
        },
    }
    with (args.output_dir / "preparation.json").open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        key: report[key] for key in (
            "roots", "summary", "maximum_anonymous_gap_seconds",
            "requests_removed_from_retained_sessions", "gpu_ready",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
