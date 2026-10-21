#!/usr/bin/env python3
"""Measure actual JOIN reentry without requiring prefetch observations."""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_native_system_costs import distribution, finite_number
from scripts.audit_sealed_wait_prefetch import SealedRecords


def identity(row):
    values = tuple(row.get(key) for key in (
        "workflow_id", "invocation_id", "context_id", "context_epoch",
    ))
    if (
        not all(type(value) is str and value for value in values[:3])
        or type(values[3]) is not int or values[3] < 0
    ):
        return None
    return values


def join_reentries(events, *, start_seconds=0., end_seconds=None):
    if (
        not finite_number(start_seconds) or start_seconds < 0
        or end_seconds is not None and (
            not finite_number(end_seconds) or end_seconds <= start_seconds
        )
    ):
        raise ValueError("invalid JOIN cohort bounds")
    starts, waits, completions = [], {}, {}
    submits, barriers = defaultdict(list), defaultdict(list)
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
        join, parent = row.get("join_id"), row.get("invocation_id")
        if kind in ("join_wait", "join_satisfied"):
            if type(join) is not str or not join:
                raise ValueError("invalid JOIN identity")
            key = workflow, join
            if kind == "join_satisfied":
                if key in completions:
                    raise ValueError("duplicate JOIN satisfaction")
                completions[key] = when
            else:
                if type(parent) is not str or not parent:
                    raise ValueError("invalid JOIN waiter")
                waits[key, parent] = min(waits.get((key, parent), when), when)
                barriers[workflow, parent].append((when, "another_join_wait", join))
        elif kind in ("return", "invocation_cancel") and parent:
            barriers[workflow, parent].append((when, "parent_terminal", None))
        elif kind == "workflow_end":
            workflow_ends[workflow].append(when)
        elif kind == "llm_submit" and not (row.get("attributes") or {}).get("runtime_internal"):
            rid = (row.get("attributes") or {}).get("request_id")
            if identity(row) is None or type(rid) is not str or not rid:
                raise ValueError("invalid parent-submit identity")
            submits[workflow, parent].append(row)
    if not starts:
        raise ValueError("no observed workflow arrival clock")
    anchor = min(starts)
    for rows in submits.values():
        rows.sort(key=lambda row: row["ts_ms"])
    submit_times = {key: [row["ts_ms"] for row in rows] for key, rows in submits.items()}
    episodes = []
    for (key, parent), wait in sorted(waits.items()):
        workflow, join = key
        completion = completions.get(key)
        elapsed = ((max(wait, completion) if completion is not None else wait) - anchor) / 1000.
        if elapsed < start_seconds or end_seconds is not None and elapsed >= end_seconds:
            continue
        row = {
            "workflow_id": workflow, "join_id": join, "parent_invocation_id": parent,
            "join_wait_ts_ms": wait, "join_satisfied_ts_ms": completion,
            "join_ready_seconds_from_first_arrival": elapsed,
            "status": "join_not_satisfied_in_prefix",
        }
        if completion is not None:
            ready = max(wait, completion)
            rows = submits.get((workflow, parent), ())
            position = bisect_left(submit_times.get((workflow, parent), ()), ready)
            candidate = rows[position] if position < len(rows) else None
            following = [
                (when, reason) for when, reason, other in barriers[workflow, parent]
                if when >= (wait if reason == "parent_terminal" else ready) and other != join
                and (candidate is None or when <= candidate["ts_ms"])
            ]
            following.extend(
                (when, "workflow_terminal") for when in workflow_ends[workflow]
                if when >= wait and (candidate is None or when <= candidate["ts_ms"])
            )
            if following:
                row["status"] = min(following)[1]
            elif candidate is None:
                row["status"] = "no_parent_submit_in_prefix"
            elif position + 1 < len(rows) and rows[position + 1]["ts_ms"] == candidate["ts_ms"]:
                row["status"] = "ambiguous_parent_submit"
            else:
                row.update(
                    status="submitted_no_native_service_in_prefix",
                    request_id=candidate["attributes"]["request_id"],
                    context_id=candidate["context_id"],
                    context_epoch=candidate["context_epoch"],
                    client_submit_ts_ms=candidate["ts_ms"],
                    client_ready_to_submit_ms=candidate["ts_ms"] - ready,
                )
        episodes.append(row)
    requests = [row["request_id"] for row in episodes if row.get("request_id")]
    if len(requests) != len(set(requests)):
        raise ValueError("separate JOIN episodes resolved to the same parent request")
    return episodes, anchor


def attach_service(episodes, native_events, service_events):
    targets = {row["request_id"]: row for row in episodes if row.get("request_id")}
    native, services = {}, {}
    for event in native_events:
        rid = (event.get("attributes") or {}).get("request_id")
        if event.get("kind") != "llm_submit" or rid not in targets:
            continue
        if rid in native:
            raise ValueError("duplicate native request submission")
        native[rid] = event
    for event in service_events:
        if event.get("event") != "gpu_service_sample":
            continue
        start, end = event.get("service_start_ts_ms"), event.get("complete_ts_ms")
        if not finite_number(start) or not finite_number(end) or start > end:
            continue
        for sample in event.get("request_samples") or ():
            rid = sample.get("request_id")
            if rid in targets and identity(sample) is not None:
                key = rid, identity(sample)
                services[key] = min(services.get(key, start), start)
    for rid, row in targets.items():
        event = native.get(rid)
        if event is None:
            continue
        expected = row["workflow_id"], row["parent_invocation_id"], row["context_id"], row["context_epoch"]
        if identity(event) != expected:
            row["status"] = "native_identity_mismatch"
            continue
        start = services.get((rid, expected))
        if start is None:
            row["status"] = "native_queue_observed_service_unproven"
            continue
        queued = event.get("ts_ms")
        if not finite_number(queued) or queued > start:
            row["status"] = "native_service_precedes_queue"
            continue
        attrs = event.get("attributes") or {}
        row.update(
            status="first_service_observed", native_queue_ts_ms=queued,
            service_start_ts_ms=start, native_queue_to_service_ms=start - queued,
        )
        ingress = attrs.get("native_ingress_timing_ms") or {}
        received = ingress.get("api_received_ts_ms")
        if finite_number(received) and received <= queued:
            row["api_received_to_queue_ms"] = queued - received
            row["api_received_to_service_ms"] = start - received
        for name in ("prompt_tokens", "cached_tokens_device", "cached_tokens_host", "mamba_host_hit_slots"):
            value = attrs.get(name)
            if type(value) is int and value >= 0:
                row[name] = value


def summarize(episodes):
    served = [row for row in episodes if row["status"] == "first_service_observed"]
    return {
        "join_waiter_episodes": len(episodes),
        "unique_join_groups": len({(row["workflow_id"], row["join_id"]) for row in episodes}),
        "status_counts": dict(Counter(row["status"] for row in episodes)),
        "submitted_parent_requests": sum("request_id" in row for row in episodes),
        "first_service_observed_requests": len(served),
        **{
            field: distribution(row[field] for row in episodes if field in row)
            for field in (
                "client_ready_to_submit_ms", "native_queue_to_service_ms",
                "api_received_to_queue_ms", "api_received_to_service_ms",
            )
        },
        "served_request_logical_host_hit_tokens": sum(row.get("cached_tokens_host", 0) for row in served),
        "served_requests_with_logical_host_hit": sum(row.get("cached_tokens_host", 0) > 0 for row in served),
    }


def audit_arm(arm, *, start_seconds=0., end_seconds=None):
    reader = SealedRecords()
    clients = sorted(arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl"))
    episodes, anchor = join_reentries(
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
        "input_jsonl_prefixes": reader.prefixes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True, help="NAME=ARM_PATH")
    parser.add_argument("--start-seconds", type=float, default=0.)
    parser.add_argument("--end-seconds", type=float)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or not finite_number(args.start_seconds) or args.start_seconds < 0
        or args.end_seconds is not None and (
            not finite_number(args.end_seconds) or args.end_seconds <= args.start_seconds
        )
    ):
        parser.error("fresh output and finite increasing nonnegative bounds required")
    arms = {}
    for item in args.arm:
        name, path = item.split("=", 1)
        if not name or name in arms:
            parser.error("arm names must be unique and nonempty")
        arms[name] = audit_arm(
            Path(path), start_seconds=args.start_seconds, end_seconds=args.end_seconds,
        )
    sources = (
        Path(__file__), ROOT / "scripts/audit_native_system_costs.py",
        ROOT / "scripts/audit_sealed_wait_prefetch.py",
    )
    report = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "Actual JOIN waiter episodes from independent sealed prefixes, without "
            "prefetch observations or natural-completion filtering. Client JOIN "
            "ready-to-submit and server ingress/queue-to-service use separate local "
            "clocks; their timestamps are never subtracted across clocks or summed "
            "as end-to-end delay. Still-unserved episodes remain explicit; served "
            "latency distributions are conditional, not an unbiased live comparison. "
            "Bounds are relative to first workflow arrival, not GPU-monitor origin. "
            "Native logical Host-hit token counts may include resident FULL with "
            "Host copies; they are not physical missing pages, DMA bytes, or saved wait."
        ),
        "auditor_source_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sources
        },
        "arms": arms, "gpu_performance_accepted": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "arms": {name: value["summary"] for name, value in arms.items()},
    }, indent=2))


if __name__ == "__main__":
    main()
