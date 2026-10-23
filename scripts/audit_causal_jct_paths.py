#!/usr/bin/env python3
"""Account for observed workflow JCT along the last-completing dependency chain."""

from __future__ import annotations

import argparse
from collections import Counter
from graphlib import TopologicalSorter
import json
import math
from pathlib import Path
import statistics
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEventKind as Kind
from scripts.audit_join_reentry_service import identity, join_reentries
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_tool_reentry_service import tool_reentries
from scripts.run_causal_comparison import receipt_work
from scripts.run_causal_gpu_replay import source_plan
from beliefkv.experiments.source_provenance import sha256_file


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def unique(rows, field):
    indexed = {}
    for row in rows:
        key = row[field]
        if not isinstance(key, str) or not key or key in indexed:
            raise ValueError(f"missing or duplicate {field}")
        indexed[key] = row
    return indexed


def first_launches(rows, requests, *, expected_records):
    launches, domain = {}, None
    count = 0
    for row in rows:
        if row.get("event") != "gpu_service_sample":
            continue
        count += 1
        clock = row.get("monotonic_clock_domain")
        start, end = row.get("launch_monotonic_ms"), row.get("complete_monotonic_ms")
        if (
            not finite(start) or not finite(end) or not 0 <= start <= end
            or not isinstance(clock, str) or not clock
            or domain is not None and clock != domain
            or row.get("sample_id") != f"native-service-{count:09d}"
        ):
            raise ValueError("native launch evidence has an invalid clock or missing sample")
        domain = clock
        for sample in row.get("request_samples") or ():
            request = requests.get(sample.get("request_id"))
            if request is None:
                continue
            receipt = request["native_receipt"]
            if identity(sample) != tuple(receipt["identity"]) or clock != receipt["clock_domain"]:
                raise ValueError("native launch changed request identity or clock")
            rid = sample["request_id"]
            launches[rid] = min(launches.get(rid, start), start)
    if count != expected_records or launches.keys() != requests.keys():
        raise ValueError("native first-launch coverage is incomplete")
    return launches


def request_cohorts(events):
    cohorts = {
        row["attributes"]["request_id"]: (
            "context_summary" if row["attributes"].get("runtime_internal") else "other"
        )
        for row in events if row["kind"] == Kind.LLM_SUBMIT.value
    }
    tools, _, _ = tool_reentries(events)
    joins, _ = join_reentries(events)
    for rows, cohort in ((tools, "post_tool"), (joins, "post_join")):
        for row in rows:
            if row.get("request_id"):
                cohorts[row["request_id"]] = cohort
    return cohorts


def account(plan, events, request_rows, launches, *, expected_jct_ms, cohorts=None):
    events, requests = unique(events, "event_id"), unique(request_rows, "request_id")
    nodes = {node.node_id: node for node in plan.nodes}
    expected_events = {node.event.event_id for node in nodes.values() if node.event}
    if events.keys() != expected_events or requests.keys() != plan.request_usage.keys():
        raise ValueError("observed event or request population differs from the source graph")
    if launches.keys() != requests.keys():
        raise ValueError("first native launches do not cover all requests")
    cohorts = cohorts or {}
    submit = {
        node.event.attributes["request_id"]: node.event
        for node in nodes.values() if node.event and node.event.kind is Kind.LLM_SUBMIT
    }
    times, splits, predecessors = {}, {}, {}
    domain = None
    for key, node in nodes.items():
        if node.event:
            row = events[node.event.event_id]
            if (
                row["kind"] != node.event.kind.value
                or row["workflow_id"] != node.workflow_id
                or identity(row) != identity(node.event.to_dict())
            ):
                raise ValueError("observed event changed source identity")
            when = row["ts_ms"]
        else:
            row = requests[node.request_id]
            receipt = row["native_receipt"]
            clock = receipt.get("clock_domain")
            when, submitted, complete = (
                row["native_complete_monotonic_ms"], row["submitted_monotonic_ms"],
                row["http_complete_monotonic_ms"],
            )
            if (
                row.get("exact_tokens_verified") is not True
                or (row["prompt_tokens"], row["output_tokens"]) != plan.request_usage[node.request_id]
                or tuple(receipt["identity"]) != identity(submit[node.request_id].to_dict())
                or receipt.get("request_id") != node.request_id
                or receipt.get("complete_monotonic_ms") != when
                or not isinstance(clock, str) or not clock
                or domain is not None and clock != domain
                or not all(finite(value) for value in (submitted, when, complete))
                or not submitted <= launches[node.request_id] <= when <= complete
            ):
                raise ValueError("request work, identity, clock or chronology is invalid")
            domain = clock
        if not finite(when) or when < 0:
            raise ValueError("observed node has an invalid timestamp")
        times[key] = when
    for key in TopologicalSorter(plan.dependency_graph()).static_order():
        node = nodes[key]
        if not node.dependencies:
            if node.event is None or node.event.kind is not Kind.WORKFLOW_START:
                raise ValueError("a causal path starts outside workflow arrival")
            splits[key] = {}
            continue
        if any(nodes[previous].workflow_id != node.workflow_id for previous in node.dependencies):
            raise ValueError("cross-workflow causal dependency")
        previous = max(node.dependencies, key=times.__getitem__)
        begin, end = times[previous], times[key]
        if begin > end:
            raise ValueError("a node completed before its dependency")
        predecessors[key] = previous
        if node.request_id:
            row = requests[node.request_id]
            submitted, launch = row["submitted_monotonic_ms"], launches[node.request_id]
            if begin > submitted:
                raise ValueError("native request submitted before its causal predecessor")
            splits[key] = {
                "request_dispatch": submitted - begin,
                "request_submit_to_first_launch": launch - submitted,
                "request_first_launch_to_native_complete": end - launch,
            }
        else:
            splits[key] = {"event:" + node.event.kind.value: end - begin}
    workflows = {}
    for key, node in nodes.items():
        if node.event is None or node.event.kind is not Kind.WORKFLOW_END:
            continue
        parts, cohort_parts, cohort_counts = Counter(), {}, Counter()
        path_requests, visited = [], set()
        while True:
            if key in visited:
                raise ValueError("cycle in observed dependency path")
            visited.add(key)
            parts.update(splits[key])
            request = nodes[key].request_id
            if request:
                path_requests.append(request)
                cohort = cohorts.get(request, "other")
                cohort_parts.setdefault(cohort, Counter()).update(splits[key])
                cohort_counts[cohort] += 1
            if key not in predecessors:
                break
            key = predecessors[key]
        if nodes[key].workflow_id != node.workflow_id:
            raise ValueError("critical path reaches a different workflow")
        elapsed = times[node.node_id] - times[key]
        if (
            not math.isclose(sum(parts.values()), elapsed, rel_tol=1e-12, abs_tol=1e-5)
            or not math.isclose(
                elapsed, expected_jct_ms[node.workflow_id], rel_tol=1e-12, abs_tol=1e-5,
            )
        ):
            raise ValueError("observed path does not reconstruct measured workflow JCT")
        workflows[node.workflow_id] = {
            "jct_ms": elapsed, "components_ms": dict(parts),
            "request_components_ms_by_cohort": {
                cohort: dict(values) for cohort, values in cohort_parts.items()
            },
            "critical_path_request_counts_by_cohort": dict(cohort_counts),
            "critical_path_requests": list(reversed(path_requests)),
            "critical_path_nodes": len(visited),
        }
    if workflows.keys() != expected_jct_ms.keys():
        raise ValueError("workflow population differs from measured JCT")
    totals = Counter()
    for row in workflows.values():
        totals.update(row["components_ms"])
    return {
        "mean_jct_s": statistics.mean(row["jct_ms"] for row in workflows.values()) / 1000.,
        "mean_components_s": {name: value / len(workflows) / 1000. for name, value in totals.items()},
        "workflows": workflows,
    }


def audit_arm(plan, arm):
    reader = SealedRecords()
    summary_path = arm / "replay/summary.json"
    summary = json.loads(summary_path.read_bytes())
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    requests = unique(reader(arm / "replay/native_requests.jsonl"), "request_id")
    status_path = arm / "server/native_telemetry_status.json"
    status = json.loads(status_path.read_bytes())
    if (
        status.get("writer_error") is not None
        or status.get("dropped_records") != 0 or status.get("failed_records") != 0
        or status.get("writer_queue_depth") != 0 or status.get("pending_batch_count") != 0
    ):
        raise ValueError("native telemetry is incomplete or its writer failed")
    expected = status["background_writer_timing"]["categories"]["audit:gpu_service_sample"]["records_seen"]
    launches = first_launches(
        reader(arm / "server/runtime_audit.jsonl"), requests, expected_records=expected,
    )
    result = account(
        plan, events, requests.values(), launches,
        expected_jct_ms=summary["workflow_elapsed_ms"], cohorts=request_cohorts(events),
    )
    return {
        **result, "arm": str(arm.resolve()), "native_launch_samples": expected,
        "all_normal_tails_retained": True,
        "source_sha256": {
            str(path): sha256_file(path) for path in (summary_path, status_path)
        },
        "input_jsonl_prefixes": reader.prefixes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True, help="NAME=ARM_PATH")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve existing path evidence")
    arms, work, graph, source_evidence = {}, None, None, None
    for value in args.arm:
        name, raw_path = value.split("=", 1)
        arm = Path(raw_path).resolve()
        if not name or name in arms:
            raise ValueError("arm labels must be unique")
        summary = json.loads((arm / "replay/summary.json").read_bytes())
        source = Path(summary["source_arm"])
        if graph is None:
            graph, _, source_evidence = source_plan(source)
        elif str(source) != source_evidence["source_arm"]:
            raise ValueError("comparison source trajectory changed")
        observed_work = receipt_work(arm / "replay/native_requests.jsonl")
        if work is not None and work != observed_work:
            raise ValueError("arms execute different request work")
        work = observed_work
        arms[name] = audit_arm(graph, arm)
    result = {
        "source_graph": graph.summary(), "source_evidence": source_evidence, "arms": arms,
        "auditor_sha256": sha256_file(Path(__file__)), "same_request_work_verified": True,
        "performance_accepted": False,
        "scope": [
            "Observed last-completing dependency paths; parallel sibling time is not added.",
            "All workflow JCT values must reconstruct measured event boundaries.",
            "Submit-to-first-launch includes HTTP/front-end/queue/restore/admission.",
            "First-launch-to-native-complete includes prefill/decode/interleaving and other waits, not pure GPU time.",
            "Each arm may realize a different blocking path; component differences are descriptive, not causal ablations.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({
        name: {key: value for key, value in row.items() if key in ("mean_jct_s", "mean_components_s")}
        for name, row in arms.items()
    }, indent=2))


if __name__ == "__main__":
    main()
