#!/usr/bin/env python3
"""Measure captured parent/child input overlap without inferring free KV pages."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_workflow_service import distribution
from scripts.run_causal_gpu_replay import source_plan


def common_prefix(left, right):
    start, stop = 0, min(len(left), len(right))
    while start < stop:
        middle = (start + stop + 1) // 2
        if left[:middle] == right[:middle]:
            start = middle
        else:
            stop = middle - 1
    return start


def audit(arm):
    plan, expected, evidence = source_plan(arm)
    events = sorted(
        (node.event for node in plan.nodes if node.event is not None),
        key=lambda event: event.ts_ms,
    )
    latest, parents, observed, completed, pairs, unknown = {}, {}, set(), set(), [], Counter()
    for event in events:
        scope = event.workflow_id, event.invocation_id
        if event.kind is Kind.INVOCATION_CREATE and event.parent_invocation_id:
            parent = latest.get((event.workflow_id, event.parent_invocation_id))
            parents[scope] = (event.parent_invocation_id, parent, parent in completed)
        elif event.kind is Kind.LLM_RESULT:
            completed.add(event.attributes["request_id"])
        elif event.kind is Kind.LLM_SUBMIT:
            request = event.attributes["request_id"]
            latest[scope] = request
            if scope not in parents or scope in observed:
                continue
            observed.add(scope)
            parent, parent_request, parent_completed = parents[scope]
            if parent_request is None:
                unknown["no_parent_request_before_child_creation"] += 1
                continue
            if not parent_completed:
                unknown["parent_output_not_observed_before_child_creation"] += 1
                continue
            pairs.append((event.workflow_id, parent, event.invocation_id, parent_request, request))
    rows = []
    with CapturedTokenCorpus(arm / "server/request_tokens.jsonl", expected) as corpus:
        for workflow, parent, child, parent_request, child_request in pairs:
            before, after = corpus.get(parent_request), corpus.get(child_request)
            if before.identity[:2] != (workflow, parent) or after.identity[:2] != (workflow, child):
                raise ValueError("captured parent/child request ownership changed")
            history = before.prompt_tokens + before.output_tokens
            overlap = common_prefix(history, after.prompt_tokens)
            namespace_matches = (
                before.extra_key == after.extra_key
                and (before.cache_salt or None) == (after.cache_salt or None)
            )
            rows.append({
                "workflow_id": workflow, "parent_invocation_id": parent,
                "child_invocation_id": child, "parent_request_id": parent_request,
                "child_request_id": child_request,
                "parent_history_tokens": len(history),
                "child_prompt_tokens": len(after.prompt_tokens),
                "common_prefix_tokens": overlap,
                "same_native_cache_namespace": namespace_matches,
                "namespace_compatible_common_prefix_tokens": overlap if namespace_matches else 0,
                "common_fraction_of_child_prompt": overlap / len(after.prompt_tokens),
                "parent_prompt_sha256": before.prompt_sha256,
                "child_prompt_sha256": after.prompt_sha256,
            })
        token_evidence = corpus.evidence
    return {
        "source_evidence": evidence, "token_evidence": token_evidence,
        "summary": {
            "observed_child_invocations_with_model_calls": len(observed),
            "verified_pairs": len(rows), "unknown_pairs": dict(unknown),
            "same_cache_namespace_pairs": sum(row["same_native_cache_namespace"] for row in rows),
            **{
                name: distribution(row[name] for row in rows)
                for name in (
                    "parent_history_tokens", "child_prompt_tokens", "common_prefix_tokens",
                    "namespace_compatible_common_prefix_tokens", "common_fraction_of_child_prompt",
                )
            },
        },
        "rows": rows,
        "analysis_source_sha256": {
            name: sha256_file(ROOT / name) for name in (
                "scripts/audit_spawn_prefix_sharing.py", "scripts/run_causal_gpu_replay.py",
                "beliefkv/experiments/causal_replay_tokens.py",
            )
        },
        "semantics": [
            "Compare each child's first actual prompt with the parent's last submitted history at child creation.",
            "Parent generation is already complete before this model-directed creation; no later parent prompt or child call is used.",
            "Exact captured token content and native cache namespaces are checked; no raw token content is written.",
            "Token overlap does not establish simultaneous residency, canonical extent identity, exclusive reclaimable capacity or admission headroom.",
            "This offline source audit grants no online scheduling or eviction authority and is not a performance result.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-arm", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous source overlap evidence")
    report = audit(args.source_arm.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:
        json.dump(report, output, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
