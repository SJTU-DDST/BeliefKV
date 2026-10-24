#!/usr/bin/env python3
"""Audit actual tool-request rounds and successor service without prefetch logs."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.action_frontier import StructuredActionKind
from scripts.audit_join_reentry_service import attach_service, identity
from scripts.audit_native_system_costs import distribution, finite_number
from scripts.audit_prefetch_lifecycle import client_event_paths
from scripts.audit_sealed_wait_prefetch import SealedRecords


def tool_identity(row):
    attrs = row.get("attributes") or {}
    run = attrs.get("tool_run_id")
    call = attrs.get("tool_call_id")
    if type(run) is str and run:
        return "run", run
    if type(call) is str and call:
        return "call", call
    return None


def tool_reentries(events, *, start_seconds=0., end_seconds=None):
    if (
        not finite_number(start_seconds) or start_seconds < 0
        or end_seconds is not None and (
            not finite_number(end_seconds) or end_seconds <= start_seconds
        )
    ):
        raise ValueError("invalid tool cohort bounds")
    starts, streams = [], defaultdict(list)
    workflow_ends = defaultdict(list)
    for row in events:
        when, kind = row.get("ts_ms"), row.get("kind")
        if not finite_number(when):
            raise ValueError("invalid client event clock")
        workflow = row.get("workflow_id")
        if type(workflow) is not str or not workflow:
            raise ValueError("invalid client workflow identity")
        if kind == "workflow_start":
            starts.append(when)
        elif kind == "workflow_end":
            workflow_ends[workflow].append(when)
        invocation = row.get("invocation_id")
        if type(invocation) is str and invocation and kind in (
            "llm_submit", "llm_result", "tool_start", "tool_end",
            "return", "invocation_cancel", "context_compact", "join_wait",
        ):
            streams[workflow, invocation].append(row)
    if not starts:
        raise ValueError("no observed workflow arrival clock")
    anchor, episodes, diagnostics = min(starts), [], Counter()
    for (workflow, invocation), stream in sorted(streams.items()):
        stream.sort(key=lambda row: row["ts_ms"])
        submit_times = Counter(row["ts_ms"] for row in stream if row["kind"] == "llm_submit")
        rounds, results, ends, start_counts = [], defaultdict(list), defaultdict(list), Counter()
        current = None
        for event in stream:
            attrs = event.get("attributes") or {}
            kind = event["kind"]
            if kind == "llm_submit":
                if current is not None:
                    current["following"] = event
                current = {"origin": event, "starts": [], "barriers": [], "following": None}
                rounds.append(current)
            elif kind == "llm_result":
                results[attrs.get("request_id")].append(event)
            elif kind == "tool_start":
                start_counts[tool_identity(event)] += 1
                if current is None:
                    diagnostics["tool_starts_without_origin_submit"] += 1
                else:
                    current["starts"].append(event)
            elif kind == "tool_end":
                ends[tool_identity(event)].append(event)
            elif current is not None:
                current["barriers"].append(event)
        diagnostics["model_request_rounds"] += len(rounds)
        for item in rounds:
            origin, following, tools = item["origin"], item["following"], item["starts"]
            attrs = origin.get("attributes") or {}
            if not tools:
                diagnostics["model_rounds_without_tool_start"] += 1
                continue
            if attrs.get("runtime_internal"):
                diagnostics["internal_tool_rounds"] += 1
                continue
            rid = attrs.get("request_id")
            if identity(origin) is None or type(rid) is not str or not rid:
                raise ValueError("invalid tool-origin request identity")
            earliest = min(tool["ts_ms"] for tool in tools)
            row = {
                "workflow_id": workflow, "invocation_id": invocation,
                "origin_request_id": rid, "origin_context_id": origin["context_id"],
                "origin_context_epoch": origin["context_epoch"],
                "origin_submit_ts_ms": origin["ts_ms"], "tool_wait_start_ts_ms": earliest,
                "tool_call_ids": [(tool.get("attributes") or {}).get("tool_call_id") for tool in tools],
                "tool_run_ids": [(tool.get("attributes") or {}).get("tool_run_id") for tool in tools],
                "observed_tool_starts": len(tools),
                "tool_names": [(tool.get("attributes") or {}).get("tool_name") for tool in tools],
                "status": "tool_round_not_complete_in_prefix",
            }
            outcome = results.get(rid, ())
            complete = None
            problem = None
            if (
                len(outcome) != 1 or identity(outcome[0]) != identity(origin)
                or outcome[0]["ts_ms"] < origin["ts_ms"]
            ):
                problem = "origin_result_unproven"
            else:
                result = outcome[0]
                result_attrs = result.get("attributes") or {}
                kinds = result_attrs.get("structured_action_kinds")
                declared = result_attrs.get("tool_call_count")
                if type(kinds) is list and kinds and all(type(kind) is str for kind in kinds):
                    expected = kinds.count(StructuredActionKind.FUNCTION_CALL.value)
                    row["delegation_calls_in_round"] = kinds.count(StructuredActionKind.SPAWN.value)
                    row["tool_membership_evidence"] = "structured_action_kinds"
                else:
                    expected = declared
                    row["delegation_calls_in_round"] = None
                    row["tool_membership_evidence"] = "declared_tool_call_count"
                row["expected_tool_starts"] = expected
                if type(expected) is not int or expected < 1 or expected != len(tools):
                    problem = "tool_membership_unproven"
                elif row["delegation_calls_in_round"] or any(
                    event["kind"] == "join_wait" for event in item["barriers"]
                ):
                    problem = "mixed_tool_and_delegation_round"
                else:
                    observed_ends = []
                    for tool in tools:
                        key = tool_identity(tool)
                        candidates = ends.get(key, ())
                        if key is None or start_counts[key] != 1 or len(candidates) > 1:
                            problem = "tool_identity_ambiguous"
                            break
                        if not candidates:
                            break
                        end = candidates[0]
                        if (
                            end["ts_ms"] < tool["ts_ms"]
                            or (end.get("attributes") or {}).get("tool_call_id")
                            != (tool.get("attributes") or {}).get("tool_call_id")
                        ):
                            problem = "tool_end_identity_or_clock_mismatch"
                            break
                        observed_ends.append(end["ts_ms"])
                    if len(observed_ends) == len(tools) and problem is None:
                        complete = max(observed_ends)
                        row["tool_completed_ts_ms"] = complete
                        row["tool_round_duration_ms"] = complete - earliest
                        ready = max(complete, result["ts_ms"])
                        if following is not None and ready > following["ts_ms"]:
                            problem = "tool_round_overlaps_following_model_request"
                        else:
                            row["client_ready_ts_ms"] = ready
            if problem is not None:
                row["status"] = problem
            elif complete is not None:
                blockers = [
                    (event["ts_ms"], "agent_terminal")
                    for event in item["barriers"]
                    if event["kind"] in ("return", "invocation_cancel")
                ]
                blockers.extend(
                    (when, "workflow_terminal") for when in workflow_ends[workflow]
                    if origin["ts_ms"] <= when and (
                        following is None or when <= following["ts_ms"]
                    )
                )
                if blockers:
                    row["status"] = min(blockers)[1]
                elif following is None:
                    row["status"] = "no_successor_submit_in_prefix"
                elif (following.get("attributes") or {}).get("runtime_internal"):
                    row["status"] = "internal_request_before_agent_reentry"
                elif identity(following) is None:
                    row["status"] = "successor_identity_unproven"
                elif (
                    submit_times[origin["ts_ms"]] > 1
                    or submit_times[following["ts_ms"]] > 1
                    or any(tool["ts_ms"] == following["ts_ms"] for tool in tools)
                ):
                    row["status"] = "ambiguous_model_round_boundary"
                else:
                    successor = (following.get("attributes") or {}).get("request_id")
                    if type(successor) is not str or not successor or successor == rid:
                        row["status"] = "successor_identity_unproven"
                    else:
                        row.update(
                            status="submitted_no_native_service_in_prefix",
                            request_id=successor, context_id=following["context_id"],
                            context_epoch=following["context_epoch"],
                            client_submit_ts_ms=following["ts_ms"],
                            client_ready_to_submit_ms=following["ts_ms"] - row["client_ready_ts_ms"],
                            context_compactions_before_successor=sum(
                                event["kind"] == "context_compact" for event in item["barriers"]
                            ),
                        )
            elapsed = (row.get("client_ready_ts_ms", complete or earliest) - anchor) / 1000.
            row["ready_seconds_from_first_arrival"] = elapsed
            if elapsed >= start_seconds and (end_seconds is None or elapsed < end_seconds):
                episodes.append(row)
    successors = [row["request_id"] for row in episodes if row.get("request_id")]
    if len(successors) != len(set(successors)):
        raise ValueError("separate tool rounds resolved to the same successor request")
    return episodes, anchor, dict(diagnostics)


def summarize(episodes):
    served = [row for row in episodes if row["status"] == "first_service_observed"]
    groups = defaultdict(list)
    for row in episodes:
        prompt = row.get("prompt_tokens")
        if type(prompt) is not int or prompt < 0:
            bucket = "unknown"
        else:
            bucket = next(
                (name for upper, name in (
                    (8192, "lt_8k"), (32768, "8k_to_32k"),
                    (65536, "32k_to_64k"), (131072, "64k_to_128k"),
                ) if prompt < upper), "ge_128k",
            )
        groups[bucket].append(row)
    return {
        "tool_request_rounds": len(episodes),
        "status_counts": dict(Counter(row["status"] for row in episodes)),
        "submitted_successor_requests": sum("request_id" in row for row in episodes),
        "first_service_observed_requests": len(served),
        **{
            field: distribution(row[field] for row in episodes if field in row)
            for field in (
                "tool_round_duration_ms", "client_ready_to_submit_ms",
                "native_queue_to_service_ms", "api_received_to_queue_ms",
                "api_received_to_service_ms",
            )
        },
        "served_request_logical_host_hit_tokens": sum(row.get("cached_tokens_host", 0) for row in served),
        "served_requests_with_logical_host_hit": sum(row.get("cached_tokens_host", 0) > 0 for row in served),
        "by_prompt_tokens": {
            bucket: {
                "tool_request_rounds": len(rows),
                "first_service_observed_requests": sum(
                    row["status"] == "first_service_observed" for row in rows
                ),
                **{
                    field: distribution(row[field] for row in rows if field in row)
                    for field in ("native_queue_to_service_ms", "api_received_to_service_ms")
                },
            }
            for bucket, rows in sorted(groups.items())
        },
    }


def audit_arm(arm, *, start_seconds=0., end_seconds=None):
    reader = SealedRecords()
    clients = client_event_paths(arm)
    episodes, anchor, diagnostics = tool_reentries(
        (row for path in clients for row in reader(path)),
        start_seconds=start_seconds, end_seconds=end_seconds,
    )
    attach_service(
        episodes, reader(arm / "server/runtime_events.sglang.jsonl"),
        reader(arm / "server/runtime_audit.jsonl"),
    )
    return {
        "arm": str(arm.resolve()), "first_workflow_start_monotonic_ms": anchor,
        "start_seconds": start_seconds, "end_seconds": end_seconds,
        "summary": summarize(episodes), "episodes": episodes,
        "client_event_diagnostics": diagnostics,
        "client_event_diagnostics_scope": "complete client prefixes before cohort filtering",
        "input_jsonl_prefixes": reader.prefixes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True, help="NAME=ARM_PATH")
    parser.add_argument("--start-seconds", type=float, default=0.)
    parser.add_argument("--end-seconds", type=float)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous evidence; use a fresh output")
    arms = {}
    for item in args.arm:
        name, path = item.split("=", 1)
        if not name or name in arms:
            parser.error("arm names must be unique and nonempty")
        arms[name] = audit_arm(Path(path), start_seconds=args.start_seconds, end_seconds=args.end_seconds)
    sources = (
        Path(__file__), ROOT / "scripts/audit_join_reentry_service.py",
        ROOT / "scripts/audit_native_system_costs.py", ROOT / "scripts/audit_sealed_wait_prefetch.py",
        ROOT / "beliefkv/runtime/action_frontier.py",
        ROOT / "scripts/audit_prefetch_lifecycle.py",
    )
    report = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "Independent observed tool-start rounds bound to their actual preceding model request. "
            "Membership requires the model result's action count and matching tool ends. "
            "Mixed delegation rounds, incomplete or ambiguous identities remain explicit. "
            "Client ready-to-submit and server ingress/queue-to-service use separate local clocks; "
            "their timestamps are never subtracted across clocks or summed into end-to-end delay. "
            "Served latency distributions are conditional; unknown and still-unserved rounds remain. "
            "Logical Host-hit tokens are not physical H2D bytes or a prefetch oracle."
        ),
        "auditor_source_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources
        },
        "arms": arms, "gpu_performance_accepted": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "output": str(args.output), "arms": {name: value["summary"] for name, value in arms.items()},
    }, indent=2))


if __name__ == "__main__":
    main()
