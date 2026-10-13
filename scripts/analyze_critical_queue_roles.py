#!/usr/bin/env python3
"""Describe queueing by client-observed JOIN/report roles without causal claims."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import defaultdict
import csv
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.analyze_workflow_jct_sources import first_services, read_json, records


def quantile(values, probability):
    if not values:
        return None
    ordered = sorted(values)
    index = probability * (len(ordered) - 1)
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def online_snapshots(events, services):
    roots = {
        row["invocation_id"] for row in events
        if row["kind"] == "invocation_create" and row["relation_type"] == "root"
    }
    assert len(roots) == 1
    root = next(iter(roots))
    requests, joins, notices, returns, resume = {}, {}, {}, {}, {}
    boundaries = defaultdict(list)
    for row in events:
        if row["kind"] == "llm_submit":
            key = row["attributes"]["request_id"]
            assert key not in requests
            requests[key] = {
                "request_id": key, "invocation_id": row["invocation_id"],
                "client_submit_ms": row["ts_ms"],
                "internal": bool(row["attributes"].get("runtime_internal")),
                "context_epoch": row["context_epoch"],
            }
        elif row["kind"] in ("tool_start", "return"):
            boundaries[row["invocation_id"]].append((
                row["ts_ms"], row["sequence"], row["kind"],
            ))
    timeline = [(row["ts_ms"], row["sequence"], "event", row) for row in events]
    for key in requests:
        if "first_service_mono_ms" in services.get(key, {}):
            timeline.append((
                services[key]["first_service_mono_ms"], float("inf"), "service", key,
            ))

    def snapshot(request, timestamp):
        invocation = request["invocation_id"]
        active = [
            item for item in joins.values()
            if "begin" in item and "end" not in item
            and invocation in item["members"] - returns.keys()
        ]
        if request["internal"]:
            role = "internal"
        elif invocation == root:
            role = "root"
        elif any(len(item["members"] - returns.keys()) == 1 for item in active):
            role = "sole_join_child"
        elif active:
            role = "parallel_join_child"
        else:
            role = "other_child"
        notice = notices.get(invocation)
        announced = bool(
            notice is not None and notice["request_id"] == request["request_id"]
            and notice["context_epoch"] == request["context_epoch"]
            and 0 <= timestamp - notice["ts_ms"] < 120_000.
        )
        return role, announced

    for timestamp, _, kind, item in sorted(timeline, key=lambda row: row[:2]):
        if kind == "service":
            request = requests[item]
            request["role_at_service"], request["notice_at_service"] = snapshot(request, timestamp)
            continue
        event = item
        invocation, event_kind = event["invocation_id"], event["kind"]
        attributes = event.get("attributes") or {}
        if event_kind == "join_create":
            joins[event["join_id"]] = {"members": set(event["member_invocation_ids"])}
        elif event_kind == "join_wait":
            joins[event["join_id"]].update(begin=timestamp, waiter=invocation)
        elif event_kind == "join_satisfied":
            join = joins[event["join_id"]]
            join["end"] = timestamp
            if "waiter" in join:
                resume[join["waiter"]] = "join"
        elif event_kind == "return":
            returns[invocation] = timestamp
            notices.pop(invocation, None)
        elif event_kind == "tool_start":
            notices.pop(invocation, None)
        elif event_kind == "tool_end":
            if resume.get(invocation) != "join":
                resume[invocation] = "tool"
        elif (
            event_kind == "structured_action"
            and attributes.get("beliefkv_child_completion_intent") is True
            and attributes.get("child_completion_signal_kind") == "stage"
        ):
            budget = attributes.get("estimated_final_report_tokens")
            join = joins.get(event["join_id"])
            if (
                type(budget) is int and 1 <= budget <= 4096 and join is not None
                and "end" not in join and invocation in join["members"] - returns.keys()
            ):
                notices[invocation] = {
                    "ts_ms": timestamp, "context_epoch": event["context_epoch"],
                    "context_id": event["context_id"], "request_id": None,
                }
        elif event_kind == "llm_submit":
            request = requests[attributes["request_id"]]
            notice = notices.get(invocation)
            if notice is not None and not request["internal"]:
                if (
                    notice["request_id"] is None
                    and event["context_id"] == notice["context_id"]
                    and event["context_epoch"] == notice["context_epoch"] + 1
                ):
                    notice.update(
                        request_id=request["request_id"],
                        context_epoch=event["context_epoch"],
                    )
                elif (
                    event["context_id"] != notice["context_id"]
                    or event["context_epoch"] != notice["context_epoch"]
                    or request["request_id"] != notice["request_id"]
                ):
                    notices.pop(invocation, None)
            request["role_at_submit"], request["notice_at_submit"] = snapshot(request, timestamp)
            request["resume_source"] = (
                resume.pop(invocation, "none") if not request["internal"] else "internal"
            )
        elif event_kind == "llm_result":
            request = requests[attributes["request_id"]]
            assert request["invocation_id"] == invocation
            request["client_result_ms"] = timestamp
            request["result_sequence"] = event["sequence"]
    root_joins = [item for item in joins.values() if item.get("waiter") == root]
    critical_intervals = defaultdict(list)
    for item in root_joins:
        assert "end" in item and all(member in returns for member in item["members"])
        child = max(item["members"], key=returns.__getitem__)
        critical_intervals[child].append((item["begin"], item["end"]))
    for rows in boundaries.values():
        rows.sort()
    for request in requests.values():
        assert "client_result_ms" in request
        rows = boundaries[request["invocation_id"]]
        offset = bisect_right(rows, (
            request["client_result_ms"], request["result_sequence"], "\uffff",
        ))
        request["next_boundary"] = rows[offset][2] if offset < len(rows) else "none"
    return root, requests, root_joins, critical_intervals


def read_server(arm, inputs):
    server = defaultdict(dict)
    for row in records(arm / "server/runtime_events.sglang.jsonl", inputs):
        attributes = row.get("attributes") or {}
        key = attributes.get("request_id")
        if key is None:
            continue
        if row["kind"] == "llm_submit":
            server[key].update(
                prompt_tokens=attributes.get("prompt_tokens") or 0,
                queue_registered_ts_ms=(
                    attributes.get("native_ingress_timing_ms") or {}
                ).get("native_queue_registered_ts_ms") or row["ts_ms"],
            )
        elif row["kind"] == "llm_result":
            server[key]["output_tokens"] = attributes.get("output_tokens") or 0
    for key, values in first_services(arm / "server/runtime_audit.jsonl", inputs).items():
        server[key].update(values)
    return server


def overlap_seconds(begin, end, intervals):
    return sum(max(0., min(end, upper) - max(begin, lower)) for lower, upper in intervals) / 1000.


def group_summary(rows, workflows):
    queues = [row["queue_seconds"] for row in rows]
    critical = sum(row["critical_queue_seconds"] for row in rows)
    return {
        "requests": len(rows),
        "queue_sum_seconds": sum(queues),
        "queue_mean_seconds": mean(queues) if queues else None,
        "queue_p50_seconds": median(queues) if queues else None,
        "queue_p90_seconds": quantile(queues, .9),
        "queue_p95_seconds": quantile(queues, .95),
        "queue_ge_10s_requests": sum(value >= 10. for value in queues),
        "queue_ge_10s_sum_seconds": sum(value for value in queues if value >= 10.),
        "critical_queue_sum_seconds": critical,
        "critical_queue_ge_10s_sum_seconds": sum(
            row["critical_queue_seconds"] for row in rows if row["queue_seconds"] >= 10.
        ),
        "critical_queue_mean_per_workflow_seconds": critical / workflows,
        "root_queue_mean_per_workflow_seconds": sum(
            row["root_queue_seconds"] for row in rows
        ) / workflows,
        "output_tokens": sum(row["output_tokens"] for row in rows),
        "later_tool_requests": sum(row["next_boundary"] == "tool_start" for row in rows),
    }


def analyze_arm(run, name, baseline, inputs):
    arm = run / name
    client = arm / "client_156"
    summary = read_json(client / "summary.json", inputs)
    server = read_server(arm, inputs)
    output, unknown = [], []
    completion_tools = recorded_stages = 0
    assert len(summary["workflows"]) == baseline["arms"][name]["workflows"] == 156
    for workflow in summary["workflows"]:
        assert workflow["outcome"] == "completed"
        instance = workflow["instance_id"]
        events = list(records(
            client / "workflows" / instance / "runtime_events.deepagents.jsonl", inputs,
        ))
        completion_tools += sum(
            row["kind"] == "tool_end"
            and row["attributes"].get("tool_name") == "announce_completion_intent"
            and row["attributes"].get("status") == "success"
            for row in events
        )
        recorded_stages += sum(
            row["kind"] == "structured_action"
            and row["attributes"].get("child_completion_signal_kind") == "stage"
            for row in events
        )
        root, requests, joins, critical = online_snapshots(events, server)
        for key, request in requests.items():
            native = server.get(key, {})
            assert "output_tokens" in native
            service = native.get("first_service_mono_ms")
            arrival = (
                native["queue_registered_ts_ms"] - native["service_clock_offset_ms"]
                if "service_clock_offset_ms" in native else None
            )
            if not (
                service is not None and arrival is not None
                and request["client_submit_ms"] <= arrival <= service <= request["client_result_ms"]
            ):
                unknown.append({"instance_id": instance, **request})
                continue
            critical_wait = overlap_seconds(
                arrival, service, critical.get(request["invocation_id"], ()),
            )
            root_wait = (
                (service - arrival) / 1000. - overlap_seconds(
                    arrival, service, [(item["begin"], item["end"]) for item in joins],
                )
                if request["invocation_id"] == root else 0.
            )
            output.append({
                "arm": name, "instance_id": instance, **request,
                "queue_seconds": (service - arrival) / 1000.,
                "critical_queue_seconds": critical_wait, "root_queue_seconds": root_wait,
                "prompt_tokens": native["prompt_tokens"], "output_tokens": native["output_tokens"],
            })
    assert not unknown, f"{name}: {len(unknown)} requests have unmeasured queue anchors"
    expected = baseline["arms"][name]
    assert len(output) == expected["work_totals"]["all_requests"]
    assert sum(row["output_tokens"] for row in output) == expected["work_totals"]["all_output_tokens"]
    overall = group_summary(output, len(summary["workflows"]))
    for observed, reference in (
        ("critical_queue_mean_per_workflow_seconds", "last_child_llm_queue"),
        ("root_queue_mean_per_workflow_seconds", "root_llm_queue"),
    ):
        assert abs(overall[observed] - expected["parts_mean_seconds"][reference]) < 1e-5
    groups = {}
    unavailable_notice_channel = completion_tools > 0 and recorded_stages == 0
    for row in output:
        row["notice_trace_available"] = not unavailable_notice_channel
        if unavailable_notice_channel:
            for anchor in ("submit", "service"):
                if row[f"role_at_{anchor}"].endswith("child"):
                    row[f"notice_at_{anchor}"] = None
    for anchor in ("submit", "service"):
        buckets = defaultdict(list)
        for row in output:
            phase = (
                "stage_trace_unavailable"
                if unavailable_notice_channel and row[f"role_at_{anchor}"].endswith("child")
                else "announced_report" if row[f"notice_at_{anchor}"] else "no_report_notice"
            )
            buckets[f"{row[f'role_at_{anchor}']}:{phase}"].append(row)
        groups[anchor] = {
            key: group_summary(rows, len(summary["workflows"]))
            for key, rows in sorted(buckets.items())
        }
    return output, {
        "workflows": len(summary["workflows"]), "overall": overall, "groups": groups,
        "notice_trace": {
            "successful_completion_tools": completion_tools,
            "recorded_stage_notices": recorded_stages,
            "stage_channel_unavailable": unavailable_notice_channel,
            "semantics": (
                "Successful tool calls alone do not recover the unrecorded token estimate "
                "or exact valid stage binding; missing stage records remain unknown."
            ),
        },
        "resume_source": {
            key: group_summary([row for row in output if row["resume_source"] == key],
                               len(summary["workflows"]))
            for key in sorted({row["resume_source"] for row in output})
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--attribution", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rows", type=Path, required=True)
    args = parser.parse_args()
    inputs = []
    baseline = read_json(args.attribution, inputs)
    assert Path(baseline["run"]).resolve() == args.run.resolve()
    rows, arms = [], {}
    for name in ("native", "predictive_h2d"):
        observed, arms[name] = analyze_arm(args.run, name, baseline, inputs)
        rows.extend(observed)
        print(f"{name}: {len(observed)} requests reconciled", flush=True)
    original_inputs = {row["path"]: row for row in baseline["inputs"]}
    for observed in inputs[1:]:
        expected = original_inputs[observed["path"]]
        assert observed["sha256"] == expected["sha256"] and observed["bytes"] == expected["bytes"]
    args.rows.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.rows, "wt", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    report = {
        "schema_version": 1, "created_at": datetime.now().astimezone().isoformat(),
        "run": str(args.run), "arms": arms, "inputs": inputs,
        "rows": {"path": str(args.rows), "requests": len(rows),
                 "sha256": hashlib.sha256(args.rows.read_bytes()).hexdigest()},
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "validation": {
            "all_156_workflows_per_arm_retained": True,
            "all_request_counts_and_output_tokens_match_v270": True,
            "root_and_critical_queue_means_reconcile_v270": True,
            "all_raw_inputs_match_v270_hashes": True,
            "unknown_queue_anchors": 0,
        },
        "semantics": [
            "Roles use client-observed event history at submit or first run_batch entry. "
            "They do not establish when control delivery reached the runtime or its actual rank.",
            "A report notice binds only its next noninternal epoch/request, expires after "
            "120 seconds, and is invalidated by later tools/RETURN. Announced is not verified final.",
            "A completion tool executed successfully without recorded stage events is an "
            "observability gap, not evidence that the child did not announce. Its report "
            "stage groups are marked stage_trace_unavailable, never reconstructed from future data.",
            "Sole child is identified from already-returned siblings, not future RETURN times.",
            "critical_queue_seconds uses the retrospectively last-returning member only "
            "for descriptive JOIN-path attribution. This label is never an online input.",
            "Queue is native registration through first worker run_batch entry, not kernel start. "
            "Per-request sums can overlap across agents and are not removable JCT.",
            "later_tool_requests is a retrospective outcome, not a ranking feature.",
            "No unfavorable workflow, output-divergent request or long wait is discarded.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "output": str(args.output), "rows": report["rows"],
        "overall": {name: value["overall"] for name, value in arms.items()},
    }, indent=2))


if __name__ == "__main__":
    main()
