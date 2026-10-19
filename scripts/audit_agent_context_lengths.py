#!/usr/bin/env python3
"""Measure request and equally weighted agent context lengths from native traces."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from statistics import mean


def records(path, bindings):
    limit = path.stat().st_size
    digest, consumed = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while consumed < limit:
            line = stream.readline(limit - consumed)
            if not line.endswith(b"\n"):
                break
            consumed += len(line)
            digest.update(line)
            if b'"invocation_create"' in line or b'"llm_submit"' in line or b'"llm_result"' in line:
                yield json.loads(line)
    bindings[str(path)] = {
        "complete_prefix_bytes": consumed, "first_read_size": limit,
        "sha256": digest.hexdigest(),
    }


def distribution(values):
    values = sorted(values)
    if not values:
        return {"count": 0}

    def quantile(level):
        position = (len(values) - 1) * level
        low, high = math.floor(position), math.ceil(position)
        return values[low] + (values[high] - values[low]) * (position - low)

    return {
        "count": len(values), "mean": mean(values),
        "min": values[0], "p50": quantile(.5), "p90": quantile(.9),
        "p95": quantile(.95), "p99": quantile(.99), "max": values[-1],
    }


def length_buckets(values, context_limit):
    bounds = (0, 16384, 32768, 65536, 131072, context_limit)
    result = []
    for index, (low, high) in enumerate(zip(bounds, bounds[1:])):
        last = index == len(bounds) - 2
        count = sum(low <= value <= high if last else low <= value < high for value in values)
        result.append({
            "lower_tokens": low, "upper_tokens": high, "upper_inclusive": last,
            "count": count, "percent": 100. * count / len(values) if values else None,
            "lower_context_fraction": low / context_limit,
            "upper_context_fraction": high / context_limit,
        })
    overflow = sum(value < 0 or value > context_limit for value in values)
    return {"buckets": result, "out_of_range": overflow}


def summarize_agents(rows, context_limit):
    result = {
        "agents": len(rows),
        "requests": sum(row["requests"] for row in rows),
    }
    for field in ("mean_prompt_tokens", "mean_request_end_tokens", "peak_context_tokens"):
        values = [row[field] for row in rows if row[field] is not None]
        result[field] = distribution(values)
        result[field + "_buckets"] = length_buckets(values, context_limit)
    return result


def stratify_workflow_eligibility(rows, workflows, context_limit):
    metadata = {}
    for workflow in workflows:
        identity = workflow["workflow_id"]
        if identity in metadata:
            raise ValueError("duplicate workflow summary")
        metadata[identity] = workflow
    ordinary = [row for row in rows if row["role"] in ("root", "child", "nested_child")]
    groups = {}
    for name, eligibility in (("eligible", True), ("excluded", False), ("unknown", None)):
        selected = []
        for row in ordinary:
            value = metadata.get(row["workflow_id"], {}).get("native_agent_jct_eligible")
            value = value if type(value) is bool else None
            if value is eligibility:
                selected.append(row)
        workflow_ids = sorted({row["workflow_id"] for row in selected})
        summary = summarize_agents(selected, context_limit)
        summary.update({
            "workflow_count": len(workflow_ids), "workflow_ids": workflow_ids,
            "request_weighted_mean_prompt_tokens": (
                sum(row["requests"] * row["mean_prompt_tokens"] for row in selected)
                / summary["requests"] if summary["requests"] else None
            ),
            "by_role": {
                role: summarize_agents(
                    [row for row in selected if row["role"] == role], context_limit,
                )
                for role in ("root", "child", "nested_child")
            },
        })
        groups[name] = summary
    return groups


def analyze(creates, submits, finishes, context_limit):
    depths = {}

    def depth(identity, visiting=None):
        if identity in depths:
            return depths[identity]
        row = creates.get(identity)
        if row is None:
            return None
        visiting = set() if visiting is None else visiting
        if identity in visiting:
            raise ValueError("cycle in declared invocation parents")
        parent = row.get("parent_invocation_id")
        if parent is None:
            result = 0 if row.get("relation_type") == "root" else None
        else:
            upper = depth((identity[0], parent), visiting | {identity})
            result = upper + 1 if upper is not None else None
        depths[identity] = result
        return result

    groups = defaultdict(list)
    unknown = Counter()
    for rid, start in submits.items():
        attrs = start.get("attributes") or {}
        prompt = attrs.get("prompt_tokens")
        if type(prompt) is not int or prompt < 0:
            unknown["invalid_prompt_tokens"] += 1
            continue
        finish = finishes.get(rid)
        output = (finish.get("attributes") or {}).get("output_tokens") if finish else None
        if output is not None and (type(output) is not int or output < 0):
            unknown["invalid_output_tokens"] += 1
            output = None
        if finish is None:
            unknown["no_native_result"] += 1
        identity = start["workflow_id"], start["invocation_id"]
        if finish is not None and (
            finish.get("workflow_id"), finish.get("invocation_id"),
            finish.get("context_id"), finish.get("context_epoch"),
        ) != (
            start.get("workflow_id"), start.get("invocation_id"),
            start.get("context_id"), start.get("context_epoch"),
        ):
            raise ValueError("native result identity changed")
        groups[identity].append({
            "request_id": rid, "prompt_tokens": prompt, "output_tokens": output,
            "context_epoch": start.get("context_epoch"),
            "end_tokens": prompt + output if output is not None else None,
        })
    rows = []
    for identity, requests in sorted(groups.items()):
        create = creates.get(identity)
        level = depth(identity)
        internal = bool(create and (create.get("attributes") or {}).get("runtime_internal"))
        role = (
            "unknown" if create is None or level is None else
            "internal" if internal else
            "root" if level == 0 else
            "child" if level == 1 else "nested_child"
        )
        prompts = [row["prompt_tokens"] for row in requests]
        ends = [row["end_tokens"] for row in requests if row["end_tokens"] is not None]
        rows.append({
            "workflow_id": identity[0], "invocation_id": identity[1],
            "context_id": create.get("context_id") if create else None,
            "role": role, "depth": level, "requests": len(requests),
            "requests_without_output": len(requests) - len(ends),
            "mean_prompt_tokens": mean(prompts),
            "mean_prompt_context_fraction": mean(prompts) / context_limit,
            "mean_request_end_tokens": mean(ends) if ends else None,
            "peak_context_tokens": max((*prompts, *ends)),
            "peak_context_fraction": max((*prompts, *ends)) / context_limit,
            "maximum_observed_epoch": max(
                (row["context_epoch"] for row in requests if type(row["context_epoch"]) is int),
                default=None,
            ),
        })
    ordinary = [row for row in rows if row["role"] in ("root", "child", "nested_child")]
    request_rows = [
        row for identity, values in groups.items()
        if identity in creates
        and not (creates[identity].get("attributes") or {}).get("runtime_internal")
        and depth(identity) is not None
        for row in values
    ]
    prompts = [row["prompt_tokens"] for row in request_rows]
    ends = [row["end_tokens"] for row in request_rows if row["end_tokens"] is not None]
    return {
        "served_agents": len(rows), "declared_invocations": len(creates),
        "declared_without_native_service": sum(identity not in groups for identity in creates),
        "native_submit_requests": len(submits), "native_result_requests": len(finishes),
        "results_without_submit": len(set(finishes) - set(submits)),
        "unknown": dict(unknown),
        "ordinary_agents": summarize_agents(ordinary, context_limit),
        "by_role": {
            role: summarize_agents([row for row in rows if row["role"] == role], context_limit)
            for role in ("root", "child", "nested_child", "internal", "unknown")
        },
        "ordinary_requests": {
            "prompt_tokens": distribution(prompts),
            "prompt_length_buckets": length_buckets(prompts, context_limit),
            "request_end_tokens": distribution(ends),
            "request_end_length_buckets": length_buckets(ends, context_limit),
        },
        "agent_rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--context-limit", type=int, default=262144)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--agent-csv", type=Path, required=True)
    args = parser.parse_args()
    if args.context_limit <= 131072 or args.output.exists() or args.agent_csv.exists():
        parser.error("a context limit above 128K and fresh output paths are required")
    bindings, creates, submits, finishes = {}, {}, {}, {}
    clients = sorted(path for path in args.arm.glob("client_*") if path.is_dir())
    if len(clients) != 1:
        raise ValueError("expected one client directory")
    for path in sorted((clients[0] / "workflows").glob("*/runtime_events.deepagents.jsonl")):
        for row in records(path, bindings):
            if row["kind"] != "invocation_create":
                continue
            identity = row["workflow_id"], row["invocation_id"]
            if identity in creates:
                raise ValueError("duplicate invocation declaration")
            creates[identity] = row
    path = args.arm / "server/runtime_events.sglang.jsonl"
    for row in records(path, bindings):
        if row["kind"] not in ("llm_submit", "llm_result"):
            continue
        rid = row["attributes"]["request_id"]
        destination = submits if row["kind"] == "llm_submit" else finishes
        if rid in destination:
            raise ValueError("duplicate native request event")
        destination[rid] = row
    report = analyze(creates, submits, finishes, args.context_limit)
    summary_path = clients[0] / "summary.json"
    if summary_path.exists():
        summary_bytes = summary_path.read_bytes()
        workflows = json.loads(summary_bytes)["workflows"]
        report["workflow_summary_source"] = {
            "path": str(summary_path.resolve()), "bytes": len(summary_bytes),
            "sha256": hashlib.sha256(summary_bytes).hexdigest(),
        }
        report["workflow_outcomes"] = [
            {key: row.get(key) for key in (
                "workflow_id", "outcome", "native_agent_jct_eligible",
                "native_agent_jct_exclusion_reasons",
            )}
            for row in workflows
        ]
    else:
        workflows = []
        report["workflow_summary_source"] = None
        report["workflow_outcomes"] = []
    report["by_workflow_eligibility"] = stratify_workflow_eligibility(
        report["agent_rows"], workflows, args.context_limit,
    )
    report.update({
        "at": datetime.now().astimezone().isoformat(),
        "arm": str(args.arm.resolve()), "context_limit_tokens": args.context_limit,
        "sources": bindings,
        "analyzer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "semantics": [
            "Agent averages give each executed request equal weight, then each agent equal weight.",
            "Prompt tokens are actual native input at first service, including rendered instructions and tool schemas.",
            "Request-end context is input plus actual generated output; it is not a time-weighted decode mean.",
            "Peak context is the largest observed native prompt or request-end context for each invocation.",
            "Compaction/internal invocations and unknown parentage are separate from dynamic root/child agents.",
            "Lengths describe logical contexts, not unique physical KV bytes after radix sharing or residency.",
            "No missing output length is filled with zero. Source prefixes are hashed independently.",
            "Workflow eligibility strata preserve the full population and the recorded exclusion reasons.",
            "Native-agent JCT eligibility is an existing measurement label, not a correctness grade.",
            "Completed workflow status is not task correctness. This audit establishes no policy speedup.",
        ],
    })
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    with args.agent_csv.open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(report["agent_rows"][0]))
        writer.writeheader()
        writer.writerows(report["agent_rows"])
    summary = {
        key: report[key] for key in (
            "served_agents", "declared_invocations", "declared_without_native_service",
            "native_submit_requests", "native_result_requests", "results_without_submit",
            "unknown",
        )
    }
    summary["by_role"] = {
        role: {key: row[key] for key in (
            "agents", "requests", "mean_prompt_tokens", "peak_context_tokens",
        )}
        for role, row in report["by_role"].items()
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
