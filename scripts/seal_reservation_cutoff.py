#!/usr/bin/env python3
"""Archive an administrative cutoff without inventing a terminal summary."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.compare_native_policy_runs import paired_workflow_jct
from scripts.summarize_semantic_h2d_ab import verify_frozen_execution_source


def sealed_json(path):
    raw = path.read_bytes()
    return json.loads(raw), {
        "path": str(path.resolve()), "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--execution-root", type=Path, required=True)
    parser.add_argument("--native-reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("preserve the previous cutoff archive")
    cutoff, cutoff_source = sealed_json(args.run_root / "server_reservation_cutoff.json")
    plan, plan_source = sealed_json(args.run_root / "ab_plan.json")
    if not cutoff["administratively_interrupted"] or plan["order"] != ["predictive_h2d"]:
        raise ValueError("this archive requires an actually interrupted predictive collection")
    stats = cutoff["collection_at_cutoff"]["arms"]["predictive_h2d"]
    unfinished = set(stats["no_terminal_result"])
    client = args.run_root / "predictive_h2d" / f"client_{plan['root_count']}"
    results, sources = [], []
    for instance in plan["workload_instance_ids_in_manifest_order"]:
        if instance in unfinished:
            continue
        row, source = sealed_json(client / "workflows" / instance / "result.json")
        results.append(row)
        sources.append(source)
    if Counter(row["outcome"] for row in results) != Counter(stats["outcomes"]):
        raise ValueError("the archived outcomes disagree with the actual cutoff")
    native_plan, native_plan_source = sealed_json(args.native_reference / "ab_plan.json")
    native, native_source = sealed_json(
        args.native_reference / "native" / f"client_{plan['root_count']}" / "summary.json",
    )
    paired = paired_workflow_jct({"workflows": results}, native)
    tail_commands = {}
    tail_sources = []
    for instance in sorted(unfinished):
        path = client / "workflows" / instance / "sandbox_audit.jsonl"
        if not path.exists():
            continue
        raw = path.read_bytes()
        tail_sources.append({
            "path": str(path.resolve()), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
        commands = [
            row for line in raw.splitlines()
            if (row := json.loads(line)).get("event") == "sandbox_execute"
        ]
        grouped = {}
        for row in commands:
            group = grouped.setdefault(row["command_sha256"], {
                "completed_call_count": 0, "completed_duration_seconds": 0.,
                "exit_codes": Counter(), "empty_output_count": 0,
            })
            group["completed_call_count"] += 1
            group["completed_duration_seconds"] += row["duration_ms"] / 1000.
            group["exit_codes"][str(row["exit_code"])] += 1
            group["empty_output_count"] += int(row["output_chars"] == 0)
        tail_commands[instance] = grouped
    report = {
        "schema_version": 1, "sealed_at_local": datetime.now().astimezone().isoformat(),
        "scope": "administratively censored development collection; not a full-run result",
        "run_root": str(args.run_root.resolve()),
        "code_commit": plan["code_commit"], "patch_sha256": plan["sglang_patch_sha256"],
        "execution_verification": verify_frozen_execution_source(
            args.execution_root, plan["code_commit"],
        ),
        "sources": {
            "cutoff": cutoff_source, "plan": plan_source, "results": sources,
            "native_plan": native_plan_source, "native_summary": native_source,
            "unfinished_sandbox_audits": tail_sources,
        },
        "actual_cutoff": cutoff,
        "planned_workflows": plan["root_count"], "archived_outcomes": dict(stats["outcomes"]),
        "administratively_censored_instances": sorted(unfinished),
        "paired_completed_diagnostic": paired,
        "unfinished_completed_tool_commands": tail_commands,
        "native_reference_code": native_plan["code_commit"],
        "native_reference_patch": native_plan["sglang_patch_sha256"],
        "same_version": (
            plan["code_commit"] == native_plan["code_commit"]
            and plan["sglang_patch_sha256"] == native_plan["sglang_patch_sha256"]
        ),
        "limitations": [
            "The actual cutoff records incomplete workflows separately; no terminal summary is fabricated.",
            "Completed-subset JCT excludes administrative censoring and is not an all-arrival mean.",
            "The historical native has different execution source and realized agent trajectories.",
            "Completed tool-command durations exclude any in-flight command interrupted by shutdown.",
            "Repeated command hashes identify exact submitted commands, not proof of unnecessary work.",
            "Independent source-file hashes are not an atomic snapshot of the whole system.",
            "No full-run throughput, quality-controlled benefit or causal acceleration is established.",
        ],
        "analyzer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "archived_outcomes": report["archived_outcomes"],
        "administratively_censored_instances": sorted(unfinished),
        "paired_completed_count": paired["paired_completed_workflows"],
        "paired_completed_duration_seconds": paired["duration_seconds"],
        "same_version": report["same_version"],
        "longest_repeated_tool_commands": {
            instance: dict(sorted(
                groups.items(), key=lambda pair: -pair[1]["completed_duration_seconds"],
            )[:5])
            for instance, groups in tail_commands.items()
        },
    }, indent=2))


if __name__ == "__main__":
    main()
