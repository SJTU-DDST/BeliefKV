#!/usr/bin/env python3
"""Reconcile natural-source invocation sizes and delivered soft-budget notices."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords


def audit(arm):
    reader = SealedRecords()
    creates, returns, tool_calls = {}, {}, defaultdict(set)
    tools = Counter()
    workflow_by_directory = {}
    paths = sorted(arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl"))
    if not paths:
        raise ValueError("natural source requires client invocation events")
    for path in paths:
        workflows = set()
        for row in reader(path):
            workflows.add(row["workflow_id"])
            key = row["workflow_id"], row.get("invocation_id")
            if row["kind"] == "invocation_create":
                if key in creates:
                    raise ValueError("duplicate invocation identity")
                creates[key] = row
            elif row["kind"] == "return":
                if key in returns:
                    raise ValueError("duplicate terminal return")
                returns[key] = row
            elif row["kind"] == "tool_start":
                tools[key] += 1
                call = row["attributes"].get("tool_call_id")
                if call:
                    tool_calls[row["workflow_id"], call].add(key)
        if len(workflows) != 1:
            raise ValueError("client event file must contain one source workflow")
        workflow_by_directory[path.parent] = next(iter(workflows))
    request_seen, completed = set(), set()
    model_calls, outputs, prompts, maximum_prompt = Counter(), Counter(), Counter(), Counter()
    for row in reader(arm / "server/runtime_events.sglang.jsonl"):
        key = row["workflow_id"], row["invocation_id"]
        if key not in creates:
            raise ValueError("native invocation has no client creation provenance")
        attrs = row["attributes"]
        rid = attrs["request_id"]
        if row["kind"] == "llm_submit":
            if rid in request_seen:
                raise ValueError("duplicate native model request")
            request_seen.add(rid)
            model_calls[key] += 1
            prompts[key] += attrs["prompt_tokens"]
            maximum_prompt[key] = max(maximum_prompt[key], attrs["prompt_tokens"])
        elif row["kind"] == "llm_result":
            if rid in completed:
                raise ValueError("duplicate native model completion")
            completed.add(rid)
            outputs[key] += attrs["output_tokens"]
    if request_seen != completed or creates.keys() != returns.keys():
        raise ValueError("source invocation or model completion is incomplete")

    def depth(key):
        seen, current, value = set(), key, 0
        while creates[current].get("parent_invocation_id") is not None:
            if current in seen:
                raise ValueError("invocation ancestry contains a cycle")
            seen.add(current)
            current = current[0], creates[current]["parent_invocation_id"]
            if current not in creates:
                raise ValueError("invocation parent is missing")
            value += 1
        return value

    notice_by_child = defaultdict(list)
    audit_counts, notice_thresholds = Counter(), Counter()
    budget_returns, declared_returns = Counter(), Counter()
    unmatched_notices = []
    for path in sorted(arm.glob("client_*/workflows/*/sandbox_audit.jsonl")):
        workflow = workflow_by_directory[path.parent]
        for row in reader(path):
            event = row.get("event")
            audit_counts[event] += 1
            if event == "child_work_budget_notice":
                owners = tool_calls[workflow, row["tool_call_id"]]
                if len(owners) != 1:
                    unmatched_notices.append(row)
                    continue
                key = next(iter(owners))
                if row["scope"] != "autonomous:" + creates[key]["agent_definition_id"]:
                    raise ValueError("budget notice changed its child role")
                if row.get("forced") is not False:
                    raise ValueError("soft notice was forced")
                notice_by_child[key].append(row["model_calls"])
                notice_thresholds[row["model_calls"]] += 1
            elif event == "child_work_budget_return":
                budget_returns[workflow, row["scope"], row["model_calls"]] += 1
                declared_returns[row["declared_status"]] += 1
    if unmatched_notices:
        raise ValueError("a child notice has no unique observed tool-call owner")
    children = {key for key in creates if depth(key) > 0}
    native_return_counts = Counter(
        (key[0], "autonomous:" + creates[key]["agent_definition_id"], model_calls[key])
        for key in children
    )
    if native_return_counts != budget_returns:
        raise ValueError("budget return counts differ from native per-child model calls")
    by_depth = {}
    for level in sorted({depth(key) for key in creates}):
        keys = [key for key in creates if depth(key) == level]
        by_depth[str(level)] = {
            "invocations": len(keys),
            "model_calls": sum(model_calls[key] for key in keys),
            "output_tokens": sum(outputs[key] for key in keys),
            "prompt_tokens": sum(prompts[key] for key in keys),
            "tools": sum(tools[key] for key in keys),
            "model_calls_per_invocation": distribution([model_calls[key] for key in keys]),
            "maximum_prompt_tokens_per_invocation": distribution([
                maximum_prompt[key] for key in keys
            ]),
        }
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "arm": str(arm.resolve()),
        "requests": len(request_seen), "prompt_tokens": sum(prompts.values()),
        "output_tokens": sum(outputs.values()), "by_depth": by_depth,
        "child_notices": {
            "matched_to_unique_tool_call_owner": sum(notice_thresholds.values()),
            "threshold_counts": dict(notice_thresholds),
            "children_with_notices": len(notice_by_child),
            "per_child_threshold_counts": dict(Counter(
                ",".join(str(value) for value in sorted(notice_by_child[key]))
                for key in children
            )),
            "budget_return_counts_match_native_child_calls": True,
            "declared_return_status_counts": dict(declared_returns),
        },
        "child_returns": {
            "count": len(children),
            "explicit_context_retired": sum(
                returns[key]["attributes"].get("context_retired") is True for key in children
            ),
        },
        "audit_event_counts": dict(audit_counts),
        "input_jsonl_prefixes": reader.prefixes,
        "auditor_sha256": sha256_file(Path(__file__)),
        "scope": [
            "Complete original natural-source work and native model counts.",
            "Soft notices are linked through actual tool-call IDs; no forced return is introduced.",
            "Prompt length is logical context size, not exclusive physical residency.",
            "Notice delivery does not prove semantic compliance with a soft work target.",
            "This report neither changes the workload nor qualifies a new GPU/performance result.",
        ],
        "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous source audits")
    result = audit(args.arm)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        name: result[name] for name in ("requests", "by_depth", "child_notices", "child_returns")
    }, indent=2))


if __name__ == "__main__":
    main()
