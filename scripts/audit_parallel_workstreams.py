"""Audit complete natural child lengths and offered versus native JOIN overlap."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RelationType, RuntimeEventKind as Kind
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workload_mix import summary
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import write


def shape(events, expected, services):
    creates, returns, submits, results, joins, waits, satisfied = {}, {}, {}, {}, {}, {}, {}
    for event in events:
        owner = event.workflow_id, event.invocation_id
        if event.kind is Kind.INVOCATION_CREATE:
            creates[owner] = event
        elif event.kind is Kind.RETURN:
            returns[owner] = event.ts_ms
        elif event.kind in (Kind.LLM_SUBMIT, Kind.LLM_RESULT):
            request = event.attributes["request_id"]
            target = submits if event.kind is Kind.LLM_SUBMIT else results
            if request in target:
                raise ValueError("duplicate client model boundary")
            target[request] = event
        elif event.kind in (Kind.JOIN_CREATE, Kind.JOIN_WAIT, Kind.JOIN_SATISFIED):
            target = {Kind.JOIN_CREATE: joins, Kind.JOIN_WAIT: waits,
                      Kind.JOIN_SATISFIED: satisfied}[event.kind]
            target[event.workflow_id, event.join_id] = event
    if set(submits) != set(results) or set(results) != set(expected):
        raise ValueError("child audit requires complete matching client and native requests")
    requests = defaultdict(list)
    for rid, event in results.items():
        identity, count = expected[rid]["llm_result"]
        if identity != (event.workflow_id, event.invocation_id, event.context_id, event.context_epoch):
            raise ValueError("client/native child ownership differs")
        if submits[rid].ts_ms > event.ts_ms:
            raise ValueError("negative child model interval")
        requests[event.workflow_id, event.invocation_id].append((rid, count))
    children = []
    for owner, event in creates.items():
        if event.relation_type is RelationType.ROOT or event.attributes.get("runtime_internal"):
            continue
        if owner not in returns or returns[owner] < event.ts_ms:
            raise ValueError("child did not return naturally within the complete trace")
        calls = requests[owner]
        children.append({
            "workflow_id": owner[0], "invocation_id": owner[1],
            "role": event.agent_definition_id, "model_calls": len(calls),
            "output_tokens": sum(count for _rid, count in calls),
            "create_to_return_s": (returns[owner] - event.ts_ms) / 1000.,
        })
    groups, owner_groups = {}, defaultdict(set)
    for token, event in joins.items():
        wait, done = waits.get(token), satisfied.get(token)
        if wait is None or done is None:
            raise ValueError("JOIN lacks explicit wait/completion boundaries")
        members = set(event.member_invocation_ids)
        points = []
        for member in members:
            owner_groups[event.workflow_id, member].add(token)
            for rid, _count in requests[event.workflow_id, member]:
                start = max(submits[rid].ts_ms, wait.ts_ms)
                end = min(results[rid].ts_ms, done.ts_ms)
                if end > start:
                    points.extend(((start, 1), (end, -1)))
        outstanding, peak, overlap, prior = 0, 0, 0., None
        for instant, change in sorted(points):
            if prior is not None and outstanding >= 2:
                overlap += instant - prior
            outstanding += change
            peak = max(peak, outstanding)
            prior = instant
        groups[token] = {
            "workflow_id": event.workflow_id, "join_id": event.join_id,
            "parent_invocation_id": wait.invocation_id,
            "nested": creates[event.workflow_id, wait.invocation_id].relation_type is not RelationType.ROOT,
            "members": len(members), "peak_outstanding_model_requests": peak,
            "multiple_outstanding_model_requests_s": overlap / 1000.,
            "positive_shared_decode_samples": 0,
        }
    service_samples, positive_members = 0, 0
    for row in services:
        if row.get("event") != "gpu_service_sample":
            continue
        service_samples += 1
        if row.get("phase") != "decode":
            continue
        touched = defaultdict(set)
        for member in row["request_samples"]:
            rid = member["request_id"]
            if rid not in expected or member.get("phase") != "decode" or member.get("token_delta", 0) <= 0:
                continue
            identity = tuple(member.get(name) for name in (
                "workflow_id", "invocation_id", "context_id", "context_epoch",
            ))
            if identity != expected[rid]["llm_result"][0]:
                raise ValueError("native shared service has mismatched request ownership")
            positive_members += 1
            for token in owner_groups[identity[:2]]:
                # Natural source events and native launches share host monotonic time.
                launch = row["launch_monotonic_ms"]
                if waits[token].ts_ms <= launch <= satisfied[token].ts_ms:
                    touched[token].add(identity[1])
        for token, owners in touched.items():
            if len(owners) >= 2:
                groups[token]["positive_shared_decode_samples"] += 1
    joined = list(groups.values())
    return {
        "children": children, "joins": joined,
        "summary": {
            "children": len(children), "joins": len(joined),
            "join_fanout_counts": dict(Counter(row["members"] for row in joined)),
            "multi_child_join_fraction": (
                sum(row["members"] >= 2 for row in joined) / len(joined) if joined else None
            ),
            "nested_joins": sum(row["nested"] for row in joined),
            "child_model_calls": summary([row["model_calls"] for row in children]),
            "child_output_tokens": summary([row["output_tokens"] for row in children]),
            "child_create_to_return_s": summary([row["create_to_return_s"] for row in children]),
            "joins_with_multiple_outstanding_model_requests": sum(
                row["multiple_outstanding_model_requests_s"] > 0 for row in joined
            ),
            "joins_with_positive_shared_decode": sum(
                row["positive_shared_decode_samples"] > 0 for row in joined
            ),
            "native_service_samples": service_samples,
            "positive_native_decode_request_members": positive_members,
            "native_service_evidence_available": service_samples > 0,
        },
        "scope": {
            "offered_concurrency_is_not_shared_GPU_service": True,
            "zero_delta_and_prefill_do_not_count_as_shared_decode": True,
            "shared_service_is_sampled_lower_bound": True,
            "child_wall_time_includes_queueing_and_tools": True,
            "JCT_or_physical_capacity_gain_established": False,
        },
    }


def audit(capture):
    capture = Path(capture).resolve()
    plan, expected, evidence = source_plan(capture)
    clients = list(capture.glob("client_*/manifest.json"))
    if len(clients) != 1:
        raise ValueError("natural shape audit needs one original client")
    services = clients[0].parent / "server/runtime_audit.jsonl"
    if not services.is_file():
        raise ValueError("natural shape audit needs the client native service journal")
    before = sha256_file(services)
    with services.open() as stream:
        result = shape(
            [node.event for node in plan.nodes if node.event is not None],
            expected, (json.loads(line) for line in stream),
        )
    if before != sha256_file(services):
        raise ValueError("source native service journal changed during complete audit")
    return {
        **result, "source": str(capture), "source_evidence": evidence,
        "native_service_sha256": {str(services): before}, "GPU_action": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve prior workload shape evidence")
    result = audit(args.capture)
    write(args.output, result)
    print(result["summary"])


if __name__ == "__main__":
    main()
