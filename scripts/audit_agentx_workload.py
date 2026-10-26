#!/usr/bin/env python3
"""Audit a pinned AgentX corpus with author chain detection, without GPU use."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import types

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.agentx_trace import explicit_join_groups


HARNESS_COMMIT = "e4bb2f835ffedbbe6b76252ea5c93caec68c5faf"
DATA_SHA256 = "e39cd2ff3eba21d4a3664be51da743ac3d2149a1933898cafc7bfeac8147eeef"


def author_module(checkout):
    commit = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True,
    ).strip()
    if commit != HARNESS_COMMIT:
        raise ValueError("author checkout differs from the pinned reviewed source")
    module = types.ModuleType("_agentx_author_inventory_reference")
    sys.modules[module.__name__] = module
    module.AIPerfBaseModel = BaseModel
    evidence = []
    for relative in (
        "src/aiperf/dataset/loader/weka_trace_models.py",
        "src/aiperf/dataset/loader/weka_agent_chains.py",
        "src/aiperf/dataset/loader/weka_trace.py",
    ):
        path = checkout / relative
        payload = subprocess.check_output(
            ["git", "-C", str(checkout), "show", "HEAD:" + relative],
        )
        if path.read_bytes() != payload:
            raise ValueError("author inventory reference has local source changes")
        evidence.append({
            "path": relative, "sha256": hashlib.sha256(payload).hexdigest(),
        })
        parsed = ast.parse(payload)
        if relative.endswith("/weka_trace.py"):
            nodes = [
                item for item in parsed.body
                if isinstance(item, ast.FunctionDef)
                and item.name in ("_split_off_preamble", "_hash_list_lcp")
                or isinstance(item, ast.Assign) and any(
                    isinstance(target, ast.Name)
                    and target.id == "_TITLE_GEN_MAX_OUTPUT_TOKENS"
                    for target in item.targets
                )
            ]
        else:
            nodes = [
                item for item in parsed.body
                if not isinstance(item, ast.ImportFrom)
                or not item.module.startswith("aiperf.")
            ]
        parsed.body = nodes
        exec(compile(parsed, str(path), "exec"), module.__dict__)
    return module, evidence


def chain_inventory(module, pairs):
    preamble, rest = module._split_off_preamble(pairs)
    result = module.detect_agent_chains(rest)
    if not result.chains:
        return [], [], 0
    main = list(result.chains[result.main_index].requests)
    main_peak = max(item.input_length for _, item in main)
    model = main[0][1].model
    workers = []
    for index in result.worker_indices:
        chain = result.chains[index]
        requests = [request for _, request in chain.requests]
        auxiliary = module.is_aux_chain(
            requests, main_peak, max_requests=1, isl_ratio=.1,
            isl_floor=16384, main_model=model, cross_model=True,
        ) or module.is_reduction_chain(
            requests, osl_max=4000, ratio=20., isl_floor=16384,
        )
        workers.append({
            "chain_index": index, "requests": len(requests),
            "outer_indices": [pos for pos, _ in chain.requests],
            "auxiliary": auxiliary,
            "fork_parent_chain": chain.fork.parent_chain if chain.fork else None,
            "fork_prefix_blocks": chain.fork.depth if chain.fork else 0,
            "evidence": "author_offline_hash_and_interval_inference",
        })
    return sorted(main + preamble, key=lambda item: item[0]), workers, result.seams_merged


def audit_row(raw, module):
    trace = module.WekaTrace.model_validate(raw)
    top = [
        (index, request) for index, request in enumerate(trace.requests)
        if request.type in ("n", "s")
    ]
    main, workers, seams = chain_inventory(module, top)
    joins, unjoined = explicit_join_groups(
        raw, main_outer_indices={pos for pos, _ in main},
    )
    calls = [request for _, request in top]
    inner_workers = []
    for child in trace.requests:
        if child.type != "subagent":
            continue
        normalized = [
            (index, request.model_copy(update={
                "t": child.t + request.t if request.t + 1e-6 < child.t else request.t,
            }))
            for index, request in enumerate(child.requests)
        ]
        _, detected, _ = chain_inventory(module, normalized)
        inner_workers.extend({"explicit_parent": child.agent_id, **row} for row in detected)
        calls.extend(child.requests)
    gaps = [
        max(0., current.t - previous.t - previous.api_time)
        for (_, previous), (_, current) in zip(main, main[1:])
    ]
    defects = []
    if any(not child["model_served_completed"] for join in joins for child in join["members"]):
        defects.append("joined_child_not_completed_or_unserved")
    if any(
        child["recorded_calls_exceed_declared_end"]
        for child in [*unjoined, *(item for group in joins for item in group["members"])]
    ):
        defects.append("recorded_child_calls_exceed_declared_terminal")
    if any(child["reason"] == "no_preceding_parent_request" for child in unjoined):
        defects.append("orphan_explicit_child")
    if any(request.input_length + request.output_length > 262144 for request in calls):
        defects.append("input_plus_output_exceeds_local_context")
    return {
        "id": trace.id, "requests": len(calls), "main_requests": len(main),
        "input_tokens": sum(item.input_length for item in calls),
        "output_tokens": sum(item.output_length for item in calls),
        "maximum_input_tokens": max((item.input_length for item in calls), default=0),
        "explicit_children": sum(item.type == "subagent" for item in trace.requests),
        "explicit_join_groups": joins,
        "parallel_join_groups": sum(join["successful_served_members"] >= 2 for join in joins),
        "unjoined_children": unjoined, "inferred_top_level_workers": workers,
        "inferred_inner_workers": inner_workers, "compaction_seams_merged": seams,
        "parent_start_gap_minus_api_seconds_max": max(gaps, default=0.),
        "parent_start_gap_minus_api_seconds_median": statistics.median(gaps) if gaps else None,
        "defects": defects,
        "project_identity_available": False,
        "tool_identity_available": any(
            bool(item.input_types or item.output_types or item.stop) for item in calls
        ),
        "original_tools_executed": False,
        "independent_task_correctness_verified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--author-checkout", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-traces", type=Path, required=True)
    parser.add_argument("--roots", type=int, default=18)
    args = parser.parse_args()
    if args.output.exists() or args.candidate_traces.exists():
        raise FileExistsError("Preserve previous population evidence.")
    if not 1 <= args.roots <= 393:
        raise ValueError("bounded candidate population required")
    with args.input.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != DATA_SHA256:
        raise ValueError("AgentX corpus differs from the pinned LFS object")
    module, code = author_module(args.author_checkout)
    rows = []
    with args.input.open() as source:
        for line in source:
            rows.append(audit_row(json.loads(line), module))
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("duplicate root trace identity")
    corpus_totals = {
        name: sum(row[name] for row in rows)
        for name in ("requests", "input_tokens", "output_tokens", "explicit_children")
    }
    if corpus_totals != {
        "requests": 68266, "input_tokens": 6891228864,
        "output_tokens": 58728807, "explicit_children": 1697,
    }:
        raise ValueError("inventory omitted or changed requests from the pinned public corpus")
    eligible = [row for row in rows if row["parallel_join_groups"] and not row["defects"]]
    ordered = sorted(eligible, key=lambda row: hashlib.sha256(
        ("21:" + row["id"]).encode(),
    ).hexdigest())
    selected = ordered[:args.roots]
    identities = {row["id"] for row in selected}
    with args.candidate_traces.open("x") as destination, args.input.open() as source:
        for line in source:
            if json.loads(line)["id"] in identities:
                destination.write(line)
    with args.candidate_traces.open("rb") as stream:
        selected_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "dataset": "semianalysisai/cc-traces-weka-062126-256k",
        "revision": "8fecd2fc56694469f758f0afbbb6335ad3043740",
        "source": str(args.input.resolve()), "sha256": digest,
        "author_harness_commit": HARNESS_COMMIT, "author_source": code,
        "reference_execution": (
            "unmodified author chain functions; schema BaseModel replaces only "
            "harness routing infrastructure; full harness replay not qualified"
        ),
        "analysis_source_sha256": {
            relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            for relative in (
                "scripts/audit_agentx_workload.py",
                "beliefkv/experiments/agentx_trace.py",
            )
        },
        "sessions": len(rows),
        "corpus_totals": corpus_totals,
        "original_unfiltered_workflow_completeness_verified": False,
        "upstream_filter": (
            "published 256k variant removes individual oversized requests; "
            "partially retained child groups can remain"
        ),
        "sessions_with_explicit_children": sum(bool(row["explicit_children"]) for row in rows),
        "sessions_with_parallel_join_groups": sum(bool(row["parallel_join_groups"]) for row in rows),
        "eligible_candidates": len(eligible),
        "selected_ids": [row["id"] for row in selected],
        "selection": "seed21 hash order among complete parallel-marker candidates; no performance results",
        "candidate_file": str(args.candidate_traces.resolve()),
        "candidate_sha256": selected_sha,
        "candidate_totals": {
            name: sum(row[name] for row in selected)
            for name in ("requests", "input_tokens", "output_tokens", "explicit_children", "parallel_join_groups")
        },
        "defect_counts": dict(Counter(reason for row in rows for reason in row["defects"])),
        "rows": rows, "gpu_used": False, "performance_accepted": False,
        "gpu_ready": False, "active_default_changed": False,
        "unresolved": [
            "causal_replay_adapter_and_exact_prefix_fidelity",
            "recorded_external_gap_policy_and_human_idle_audit",
            "project_identity_anonymized_cannot_prove_18_distinct_original_projects",
            "original_application_and_tool_semantics_unavailable",
            "all_requested_baseline_runtime_adapters_not_yet_complete",
            "upstream_request_filtering_prevents_claiming_complete_original_task_jct",
        ],
    }
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        key: report[key] for key in (
            "sessions", "sessions_with_explicit_children",
            "sessions_with_parallel_join_groups", "eligible_candidates",
            "candidate_totals", "gpu_ready", "unresolved",
        )
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
