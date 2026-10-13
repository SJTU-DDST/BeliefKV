#!/usr/bin/env python3
"""Analyze a finished development run; reuse a qualified native reference."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
from statistics import mean
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.analyze_critical_queue_roles import (
    group_summary, online_snapshots, overlap_seconds, read_server,
)
from scripts.analyze_workflow_jct_sources import analyze_workflow, read_json, records


def queue_summary(rows, workflow_count):
    measured = [row for row in rows if row["queue_seconds"] is not None]
    return {
        **group_summary(measured, workflow_count),
        "all_requests": len(rows),
        "unknown_queue_anchors": len(rows) - len(measured),
        "all_output_tokens": sum(row["output_tokens"] for row in rows),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--native-attribution", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rows", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.rows.exists():
        raise ValueError("preserve existing analysis")
    inputs = []
    plan = read_json(args.run_root / "ab_plan.json", inputs)
    comparison = read_json(args.run_root / "native_reference_comparison.json", inputs)
    reference = read_json(args.native_attribution, inputs)
    assert Path(reference["run"]).resolve() == Path(plan["native_reference_run"]).resolve()
    assert comparison["same_task_set"] and comparison["same_physical_pool_capacity"]
    native = {row["instance_id"]: row["native"] for row in reference["workflows"]}
    arm = args.run_root / "predictive_h2d"
    client = arm / "client_156"
    summary = read_json(client / "summary.json", inputs)
    manifest = read_json(client / "manifest.json", inputs)
    assert len(summary["workflows"]) == plan["root_count"] == 156
    assert {row["instance_id"] for row in summary["workflows"]} == set(native)
    print(f"Reading latest run service anchors: {args.run_root.name}", flush=True)
    server = read_server(arm, inputs)
    print(f"Read {len(server)} server request identities", flush=True)
    requests, workflows = [], []
    notices = Counter()
    ordinal = {key: index for index, key in enumerate(manifest["instance_ids"])}
    wave_size = manifest["config"]["workflow_arrival_batch_size"]
    for workflow in summary["workflows"]:
        instance = workflow["instance_id"]
        path = client / "workflows" / instance / "runtime_events.deepagents.jsonl"
        parts = analyze_workflow(path, inputs, server)
        events = list(records(path, inputs))
        root, live_requests, joins, critical = online_snapshots(events, server)
        notices["successful_completion_tools"] += sum(
            row["kind"] == "tool_end"
            and row["attributes"].get("tool_name") == "announce_completion_intent"
            and row["attributes"].get("status") == "success"
            for row in events
        )
        notices["recorded_stage_notices"] += sum(
            row["kind"] == "structured_action"
            and row["attributes"].get("child_completion_signal_kind") == "stage"
            for row in events
        )
        assert abs(
            sum(parts["parts_seconds"].get(key, 0.) for key in
                ("root_join_wait", "root_llm", "root_tool", "root_other"))
            - parts["trace_jct_seconds"]
        ) < 1e-5, instance
        workflows.append({
            "instance_id": instance, "outcome": workflow["outcome"],
            "system_jct_eligible": workflow.get("system_jct_eligible"),
            "duration_seconds": workflow["duration_seconds"],
            "summary_minus_trace_seconds": workflow["duration_seconds"] - parts["trace_jct_seconds"],
            "wave": ordinal[instance] // wave_size + 1, **parts,
        })
        for key, request in live_requests.items():
            observed = server[key]
            assert "output_tokens" in observed, (instance, key)
            service = observed.get("first_service_mono_ms")
            arrival = (
                observed["queue_registered_ts_ms"] - observed["service_clock_offset_ms"]
                if "service_clock_offset_ms" in observed else None
            )
            measured = (
                service is not None and arrival is not None
                and request["client_submit_ms"] <= arrival <= service <= request["client_result_ms"]
            )
            requests.append({
                "instance_id": instance, "outcome": workflow["outcome"], **request,
                "queue_seconds": (service - arrival) / 1000. if measured else None,
                "critical_queue_seconds": overlap_seconds(
                    arrival, service, critical.get(request["invocation_id"], ()),
                ) if measured else None,
                "root_queue_seconds": (
                    (service - arrival) / 1000. - overlap_seconds(
                        arrival, service, [(item["begin"], item["end"]) for item in joins],
                    ) if request["invocation_id"] == root else 0.
                ) if measured else None,
                "prompt_tokens": observed["prompt_tokens"],
                "output_tokens": observed["output_tokens"],
            })
    observed_run = comparison["runs"]["predictive_h2d"]
    assert dict(Counter(row["outcome"] for row in workflows)) == observed_run["outcomes"]
    assert sum(row["output_tokens"] for row in requests) == observed_run["output_tokens"]
    assert sum(not row["internal"] for row in requests) == summary["llm_request_count"]
    overall = queue_summary(requests, len(workflows))
    if overall["unknown_queue_anchors"] == 0:
        for observed, part in (
            ("critical_queue_mean_per_workflow_seconds", "last_child_llm_queue"),
            ("root_queue_mean_per_workflow_seconds", "root_llm_queue"),
        ):
            assert abs(
                overall[observed] - mean(row["parts_seconds"].get(part, 0.) for row in workflows)
            ) < 1e-5
    groups = {}
    for anchor in ("submit", "service"):
        buckets = defaultdict(list)
        for row in requests:
            phase = "announced_report" if row[f"notice_at_{anchor}"] else "no_report_notice"
            buckets[f"{row[f'role_at_{anchor}']}:{phase}"].append(row)
        groups[anchor] = {
            key: queue_summary(rows, len(workflows)) for key, rows in sorted(buckets.items())
        }
    paired = [row for row in workflows if row["outcome"] == "completed"]
    paired_comparison = comparison["paired_completed_jct"]["predictive_h2d_vs_native"]
    assert len(paired) == paired_comparison["paired_completed_workflows"]
    assert abs(
        mean(row["duration_seconds"] for row in paired)
        - paired_comparison["duration_seconds"]["left"]["mean"]
    ) < 1e-5
    assert abs(
        mean(native[row["instance_id"]]["jct_seconds"] for row in paired)
        - paired_comparison["duration_seconds"]["right"]["mean"]
    ) < 1e-5
    parts = sorted({key for row in paired for key in row["parts_seconds"]})
    paired_parts = {
        part: {
            "latest_mean_seconds": mean(row["parts_seconds"].get(part, 0.) for row in paired),
            "native_mean_seconds": mean(
                native[row["instance_id"]]["parts_seconds"].get(part, 0.) for row in paired
            ),
        } for part in parts
    }
    for values in paired_parts.values():
        values["latest_minus_native_seconds"] = values["latest_mean_seconds"] - values["native_mean_seconds"]
    args.rows.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.rows, "wt", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(requests[0]))
        writer.writeheader()
        writer.writerows(requests)
    report = {
        "schema_version": 1, "created_at": datetime.now().astimezone().isoformat(),
        "run_root": str(args.run_root), "code_commit": plan["code_commit"],
        "scope": "Latest finished development trace; historical native is a reference, not a fresh paired run.",
        "outcomes": dict(Counter(row["outcome"] for row in workflows)),
        "comparison": {key: observed_run[key] for key in
                       ("completed_per_hour", "collection_duration_seconds", "jct_mean_seconds",
                        "jct_p95_seconds", "output_tokens", "full_eviction_recompute_tokens")},
        "all_terminal_queue": overall, "role_groups": groups,
        "resume_source": {
            key: queue_summary([row for row in requests if row["resume_source"] == key], len(workflows))
            for key in sorted({row["resume_source"] for row in requests})
        },
        "notice_trace": dict(notices),
        "paired_completed_only": {
            "workflows": len(paired), "excluded_outcomes": [
                {"instance_id": row["instance_id"], "outcome": row["outcome"]}
                for row in workflows if row["outcome"] != "completed"
            ],
            "parts": paired_parts,
            "semantics": "Incomplete outcomes remain in the full report; this cohort does not prove complete-task success.",
        },
        "native_reference": {
            "run_root": reference["run"], "qualified_attribution": str(args.native_attribution),
            "raw_reference_rescanned": False,
        },
        "validation": {
            "all_submitted_workflows_retained": True,
            "all_terminal_client_requests_retained": True,
            "output_tokens_match_latest_comparison": True,
            "noninternal_request_count_matches_latest_summary": True,
            "all_root_time_partitions_reconcile": True,
            "paired_mean_durations_match_latest_comparison": True,
            "unknown_queue_anchors": overall["unknown_queue_anchors"],
        },
        "workflows": workflows, "inputs": inputs,
        "rows": {"path": str(args.rows), "requests": len(requests),
                 "sha256": hashlib.sha256(args.rows.read_bytes()).hexdigest()},
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "semantics": [
            "JOIN/LLM/tool/other partition root trace time; last-child details are already inside JOIN.",
            "Roles use client-observed history, not proof of runtime control delivery or actual rank.",
            "Last-returning child is retrospective attribution, never a feature for online decisions.",
            "Queue ends at first worker entry, not CUDA launch; concurrent request waits are not additive JCT.",
            "Different realized work and versions prevent causal attribution to prediction or scheduling.",
            "Unknown service anchors retain the request and its output; no future information fills gaps.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "output": str(args.output), "outcomes": report["outcomes"],
        "all_terminal_queue": overall, "paired_completed_parts": paired_parts,
    }, indent=2))


if __name__ == "__main__":
    main()
