#!/usr/bin/env python3
"""Locate native Host-hit demand by workflow and agent within sealed windows."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_native_system_costs import distribution, finite_number
from scripts.audit_sealed_wait_prefetch import SealedRecords


def repeated_context_demand(rows):
    grouped, unknown = defaultdict(list), 0
    for row in rows:
        identity = row.get("workflow_id"), row.get("context_id")
        if any(type(value) is not str or not value for value in identity):
            unknown += 1
            continue
        grouped[identity].append(row)
    first_bytes, later_bytes, later, gaps, roles = 0, 0, 0, [], Counter()
    for group in grouped.values():
        ordered = sorted(group, key=lambda row: (
            row["first_service_observation_ts_ms"], row["request_id"],
        ))
        first_bytes += ordered[0]["reported_host_full_bytes"]
        later += len(ordered) - 1
        later_bytes += sum(row["reported_host_full_bytes"] for row in ordered[1:])
        roles.update(row["role"] for row in ordered[1:])
        gaps.extend(
            after["first_service_observation_ts_ms"] - before["first_service_observation_ts_ms"]
            for before, after in zip(ordered, ordered[1:])
        )
    known_bytes = first_bytes + later_bytes
    return {
        "known_identity_contexts": len(grouped),
        "contexts_with_multiple_positive_observations": sum(len(group) > 1 for group in grouped.values()),
        "first_positive_observations_in_window": len(grouped),
        "later_positive_observations_in_same_context": later,
        "first_reported_host_full_bytes": first_bytes,
        "later_reported_host_full_bytes": later_bytes,
        "later_fraction_of_known_reported_host_full_bytes": (
            later_bytes / known_bytes if known_bytes else None
        ),
        "later_positive_observations_by_role": dict(roles),
        "interval_between_positive_observations_ms": distribution(gaps),
        "unknown_context_identity_rows": unknown,
        "semantics": (
            "First/later refer only to positive Host observations within the sealed window, "
            "not to the context lifetime or consecutive model requests. Intervening resident "
            "requests may exist. Repeated request accounting does not prove identical physical "
            "pages, redundant traffic, or avoidable restore time; epochs and prefixes can change."
        ),
    }


def host_demand(events, scopes, begin, end, bytes_per_token):
    rows, seen, unknown = [], set(), Counter()
    for event in events:
        if event.get("kind") != "llm_submit":
            continue
        attrs = event.get("attributes") or {}
        rid = attrs.get("request_id")
        if not rid or rid in seen:
            raise ValueError(f"missing or duplicate native first-service identity: {rid!r}")
        seen.add(rid)
        tokens = attrs.get("cached_tokens_host")
        if tokens is None:
            unknown["host_tokens_unrecorded_in_prefix"] += 1
            continue
        if type(tokens) is not int or tokens < 0:
            raise ValueError("invalid native Host-hit token count")
        if tokens == 0:
            continue
        path = attrs.get("native_host_hit_match_path") or {}
        observed = path.get("observed_ts_ms")
        if not finite_number(observed):
            unknown["positive_host_hit_without_first_service_timestamp_in_prefix"] += 1
            continue
        if observed < event["ts_ms"]:
            raise ValueError("first-service observation precedes queue registration")
        if not begin <= observed < end:
            continue
        identity = event.get("workflow_id"), event.get("invocation_id")
        scope = scopes.get(identity) or {}
        device, prompt = attrs.get("cached_tokens_device"), attrs.get("prompt_tokens")
        rows.append({
            "request_id": rid, "workflow_id": identity[0],
            "invocation_id": identity[1], "context_id": event.get("context_id"),
            "context_epoch": event.get("context_epoch"),
            "instance_id": scope.get("instance_id"), "role": scope.get("role", "unknown"),
            "native_queue_ts_ms": event["ts_ms"],
            "first_service_observation_ts_ms": observed,
            "queue_to_first_service_observation_ms": observed - event["ts_ms"],
            "reported_host_full_tokens": tokens,
            "reported_host_full_bytes": tokens * bytes_per_token,
            "reported_device_full_tokens": device, "prompt_tokens": prompt,
            "mamba_host_hit_slots": attrs.get("mamba_host_hit_slots"),
        })

    def summary(group):
        return {
            "host_hit_requests": len(group),
            "contexts": len({(row["workflow_id"], row["context_id"]) for row in group}),
            "reported_host_full_tokens": sum(row["reported_host_full_tokens"] for row in group),
            "reported_host_full_bytes": sum(row["reported_host_full_bytes"] for row in group),
            "host_full_tokens_per_request": distribution(
                row["reported_host_full_tokens"] for row in group
            ),
            "queue_to_first_service_observation_ms": distribution(
                row["queue_to_first_service_observation_ms"] for row in group
            ),
        }

    grouped = defaultdict(list)
    for row in rows:
        grouped[row["instance_id"], row["role"], row["context_id"]].append(row)
    contexts = [
        {"instance_id": instance, "role": role, "context_id": context, **summary(group)}
        for (instance, role, context), group in grouped.items()
    ]
    contexts.sort(key=lambda row: row["reported_host_full_tokens"], reverse=True)
    workflows = defaultdict(list)
    for row in rows:
        workflows[row["instance_id"]].append(row)
    return {
        "summary": summary(rows),
        "repeated_context_demand": repeated_context_demand(rows),
        "by_role": {
            role: summary([row for row in rows if row["role"] == role])
            for role in sorted({row["role"] for row in rows})
        },
        "by_workflow": sorted(
            [{"instance_id": instance, **summary(group)} for instance, group in workflows.items()],
            key=lambda row: row["reported_host_full_tokens"], reverse=True,
        ),
        "by_context": contexts, "rows": rows,
        "unknown_timing_or_size_across_full_recorded_prefix": dict(unknown),
    }


def analyze_arm(arm, begin, end, bytes_per_token):
    sealed, scopes = SealedRecords(), {}
    for path in sorted(arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl")):
        instance = path.parent.name
        for event in sealed(path):
            if event.get("kind") != "invocation_create":
                continue
            identity = event.get("workflow_id"), event.get("invocation_id")
            if identity in scopes:
                raise ValueError(f"duplicate invocation creation: {identity}")
            attrs = event.get("attributes") or {}
            role = (
                "summary" if attrs.get("runtime_internal") is True
                else "child" if event.get("relation_type") == "spawn"
                else "root" if event.get("relation_type") == "root"
                else "other"
            )
            scopes[identity] = {"instance_id": instance, "role": role}
    native_path = arm / "server/runtime_events.sglang.jsonl"
    if not native_path.is_file():
        raise FileNotFoundError(native_path)
    result = host_demand(sealed(native_path), scopes, begin, end, bytes_per_token)
    result.update({"arm": str(arm), "input_jsonl_prefixes": sealed.prefixes})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-report", type=Path, required=True)
    parser.add_argument("--start-seconds", type=float, required=True)
    parser.add_argument("--end-seconds", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output exists; choose a fresh report ID")
    payload = args.window_report.read_bytes()
    basis = json.loads(payload)
    arms = {}
    for name, run in basis["runs"].items():
        windows = [
            row for row in run["windows"]
            if row["start_seconds"] == args.start_seconds and row["end_seconds"] == args.end_seconds
        ]
        if len(windows) != 1 or windows[0].get("service_window_observed_complete") is not True:
            raise ValueError(f"requested service window is not complete: {name}")
        anchor = run["clock_alignment"]["anchor_unix_ms"]
        arms[name] = analyze_arm(
            Path(run["run"]), anchor + args.start_seconds * 1000,
            anchor + args.end_seconds * 1000, windows[0]["pool_bytes_per_unit"]["kv"],
        )
    report = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "start_seconds": args.start_seconds, "end_seconds": args.end_seconds,
        "window_source": {
            "path": str(args.window_report), "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
        "arms": arms,
        "auditor_sources": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(__file__), ROOT / "scripts/audit_sealed_wait_prefetch.py")
        },
        "semantics": [
            "Positive native cached_tokens_host counters are grouped by the first-service match-path observation timestamp, not earlier queue timestamps.",
            "Role uses exact workflow/invocation creation metadata. Missing identity or timing remains unknown; it is not a zero-demand observation.",
            "Host FULL counters describe native request accounting. Their sum is repeated demand, not unique physical pages, actual transferred bytes or component forward-use proof.",
            "Queue-to-observation includes native admission, restore and control work. It is not pure H2D delay or a removable JCT bound.",
            "Each JSONL uses a stat-bounded complete-line prefix with length/SHA256. Cross-file prefixes are not an atomic snapshot.",
        ],
    }
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "arms": {
            name: {
                "summary": arm["summary"], "by_role": arm["by_role"],
                "top_workflows": arm["by_workflow"][:5],
                "top_contexts": arm["by_context"][:5],
            } for name, arm in arms.items()
        },
    }, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
