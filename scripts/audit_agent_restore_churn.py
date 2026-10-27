#!/usr/bin/env python3
"""Reconcile complete FULL demand copies with tool/JOIN reentries and eligibility."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_agent_admission_wait import partition
from scripts.audit_join_reentry_service import join_reentries
from scripts.audit_outstanding_request_throughput import audit_arm
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_tool_reentry_service import tool_reentries


def inspect(native, transfers, categories, *, bytes_per_token, states=()):
    if type(bytes_per_token) is not int or bytes_per_token <= 0:
        raise ValueError("restore audit requires exact FULL bytes per token")
    copied, cohorts, tool_gaps, continuity = Counter(), defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)
    for transfer in transfers:
        size, direction = transfer.get("actual_bytes"), transfer.get("direction")
        if transfer.get("status") != "completed" or direction not in ("h2d", "d2h") or type(size) is not int or size < 0:
            raise ValueError("restore audit requires complete native transfers")
        tagged = [row.get("num_bytes") for row in transfer.get("tagged_child_commits", ()) if row.get("command_id") is not None]
        if any(type(value) is not int or value < 0 for value in tagged) or sum(tagged) > size:
            raise ValueError("native tagged transfer bytes are inconsistent")
        copied[direction + "_bytes"] += size
        copied[direction + "_tagged_bytes"] += sum(tagged)
        copied[direction + "_operations"] += 1
    times = [row["ts_ms"] for row in states]
    previous, seen, rows = {}, set(), []
    host_bytes = 0
    for event in sorted(native, key=lambda row: row["ts_ms"]):
        identity = event["workflow_id"], event["invocation_id"]
        attrs = event["attributes"]
        if event["kind"] == "llm_result":
            previous[identity] = event["ts_ms"]
            continue
        if event["kind"] != "llm_submit":
            continue
        request = attrs["request_id"]
        if request in seen:
            raise ValueError("restore audit found duplicate native submission")
        seen.add(request)
        tokens = attrs.get("cached_tokens_host", 0)
        if type(tokens) is not int or tokens < 0:
            raise ValueError("native Host hit tokens are invalid")
        size = tokens * bytes_per_token
        host_bytes += size
        category = categories.get(request, {"kind": "other"})
        cohort = cohorts[category["kind"]]
        cohort["requests"] += 1
        cohort["host_requests"] += tokens > 0
        cohort["host_hit_tokens"] += tokens
        cohort["host_hit_bytes"] += size
        if not tokens:
            continue
        if category["kind"] == "tool":
            gap = category.get("tool_round_duration_ms")
            bucket = "unknown" if gap is None else "at_most_200ms" if gap <= 200 else "over_200ms"
            tool_gaps[bucket]["requests"] += 1
            tool_gaps[bucket]["host_hit_bytes"] += size
        begin = previous.get(identity)
        end = (attrs.get("native_host_hit_match_path") or {}).get("observed_ts_ms")
        parts = None
        if begin is None:
            eligibility = "no_prior_same_invocation"
        elif not states or end is None:
            eligibility = "unobserved_eligibility_or_first_service"
        else:
            parts = partition(begin, end, identity[1], states)
            eligibility = (
                "observed_inactive" if parts["inactive_ms"] > 0 else
                "all_sampled_active" if parts["unknown_ms"] <= 1. else "active_or_unknown"
            )
        continuity[eligibility]["requests"] += 1
        continuity[eligibility]["host_hit_bytes"] += size
        row = {
            "request_id": request, "workflow_id": identity[0], "invocation_id": identity[1],
            "context_id": event["context_id"], "context_epoch": event["context_epoch"],
            "kind": category["kind"], "tool_round_duration_ms": category.get("tool_round_duration_ms"),
            "host_hit_tokens": tokens, "host_hit_bytes": size,
            "queue_ts_ms": event["ts_ms"], "first_service_observation_ts_ms": end,
            "prior_same_invocation_complete_ts_ms": begin,
            "eligibility_cohort": eligibility, "eligibility_partition": parts,
        }
        index = bisect_right(times, event["ts_ms"]) - 1
        if index >= 0 and event["ts_ms"] - times[index] <= 1500.:
            admission = states[index]["agent_admission"]
            logical, capacity = (
                admission.get("logical_active_tokens"), admission.get("capacity_tokens"),
            )
            row["queue_snapshot_logical_overcommit_tokens"] = (
                max(0, logical - capacity)
                if type(logical) is int and logical >= 0
                and type(capacity) is int and capacity > 0 else None
            )
        rows.append(row)
    demand = copied["h2d_bytes"] - copied["h2d_tagged_bytes"]
    if host_bytes != demand:
        raise ValueError("native demand H2D does not reconcile with request Host-hit bytes")
    return {
        "transfers": dict(copied), "native_demand_h2d_bytes": demand,
        "request_host_hit_bytes": host_bytes, "native_demand_bytes_reconciled": True,
        "by_request_kind": {key: dict(value) for key, value in sorted(cohorts.items())},
        "tool_host_hits_by_tool_duration": {key: dict(value) for key, value in sorted(tool_gaps.items())},
        "by_observed_eligibility_since_prior_completion": {key: dict(value) for key, value in sorted(continuity.items())},
        "host_hit_requests": sorted(rows, key=lambda row: -row["host_hit_bytes"]),
    }


def audit(arm):
    work, reader = audit_arm(arm), SealedRecords()
    status = json.loads((arm / "server/native_telemetry_status.json").read_bytes())
    if status["writer_queue_depth"] != 0:
        raise ValueError("restore audit requires drained native telemetry")
    capacity = json.loads((arm / "server/native_capacity_census.json").read_bytes())["capacity"]
    if (
        capacity["pool_layout"] != "static_full_only" or capacity["host_mamba_slots"] != 0
        or capacity["host_full_bytes"] % capacity["host_full_tokens"] != 0
    ):
        raise ValueError("restore audit requires observed exact FULL-only geometry")
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    tools, _, diagnostics = tool_reentries(events)
    joins, _ = join_reentries(events)
    categories = {}
    for kind, episodes in (("tool", tools), ("join", joins)):
        for episode in episodes:
            request = episode.get("request_id")
            if request is None:
                continue
            if request in categories:
                categories[request] = {"kind": "mixed_join_and_tool"}
            else:
                categories[request] = {
                    "kind": kind, "tool_round_duration_ms": episode.get("tool_round_duration_ms"),
                }
    opportunity = arm / "opportunities/admission_opportunities.jsonl"
    states = [
        row for row in reader(opportunity)
        if row.get("event") == "admission_runtime_state" and row.get("agent_admission") is not None
    ] if opportunity.exists() else []
    if any(row.get("physical_disabled") is not False for row in states):
        raise ValueError("restore audit found disabled native physical control")
    native_path = arm / "server/runtime_events.sglang.jsonl"
    result = inspect(
        reader(native_path), reader(arm / "server/transfer_telemetry.jsonl"), categories,
        bytes_per_token=capacity["host_full_bytes"] // capacity["host_full_tokens"], states=states,
    )
    if reader.prefixes[str(native_path)]["complete_prefix_sha256"] != work["events"]["sha256"]:
        raise ValueError("native request population changed during restore audit")
    return {
        **result, "work": work, "tool_episode_diagnostics": diagnostics,
        "input_jsonl_prefixes": reader.prefixes,
        "capacity_census_sha256": sha256_file(arm / "server/native_capacity_census.json"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True, help="LABEL=ARM_PATH")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve earlier restore audits")
    arms = {}
    for value in args.arm:
        label, separator, path = value.partition("=")
        if not separator or not label or not path or label in arms:
            parser.error("each arm needs a unique LABEL=ARM_PATH")
        arms[label] = audit(Path(path))
    if len({row["work"]["request_identity_and_token_count_sha256"] for row in arms.values()}) != 1:
        raise ValueError("restore comparison has different request work")
    report = {
        "observed_at": datetime.now(timezone.utc).isoformat(), "arms": arms,
        "same_native_request_identities_and_token_counts": True,
        "auditor_sha256": sha256_file(Path(__file__)), "performance_accepted": False,
        "scope": [
            "Complete unchanged replay work; no workflow or normal tail is removed.",
            "FULL-only aggregate untagged H2D reconciles exactly with native request Host-hit bytes. Per-request cohorts are Host-hit associations, not individual DMA command attribution.",
            "Eligibility uses independent samples held at most 1500ms; all_sampled_active cannot exclude unsampled pauses. First-service path observation is not kernel start.",
            "Adapters without per-agent eligibility or logical-budget fields remain unknown; program counts do not establish active identities or logical overcommit.",
            "Logical overcommit and observed inactivity do not prove physical eviction cause, reclaimable pages, saved transfer time or removable JCT.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        name: {key: row[key] for key in ("native_demand_h2d_bytes", "by_request_kind", "tool_host_hits_by_tool_duration")}
        for name, row in arms.items()
    }, indent=2))


if __name__ == "__main__":
    main()
