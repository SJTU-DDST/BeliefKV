#!/usr/bin/env python3
"""Aggregate completed child streams by raw HTTP wait and consumer delay."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sys

import orjson

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pilot_child_stream_content import collect
from scripts.pilot_join_service_progress import CLOCK_GUARD_MS, clock_bracket


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    return round(
        ordered[lower]
        + (ordered[math.ceil(position)] - ordered[lower])
        * (position - lower),
        2,
    )


def evaluate(run: Path) -> dict:
    workflows = run / "workloads/workflows"
    server_events = run / "server/runtime_events.sglang.jsonl"
    rows, collection = collect(workflows, min_snapshot_chars=1)
    lower, upper, clocks = clock_bracket(workflows, server_events)
    by_rid = {row["rid"]: row for row in rows}
    server_results = {}
    with server_events.open("rb") as stream:
        for line in stream:
            event = orjson.loads(line)
            if event.get("kind") == "llm_result":
                rid = (event.get("attributes") or {}).get("request_id")
                if rid in by_rid:
                    server_results[rid] = event

    transport = {}
    duplicates = set()
    for path in workflows.glob("*/child_stream_content.jsonl"):
        with path.open("rb") as stream:
            for line in stream:
                event = orjson.loads(line)
                if event.get("event") != "llm_stream_http_transport":
                    continue
                rid = event["request_id"]
                if rid in transport:
                    duplicates.add(rid)
                transport[rid] = event

    exclusions = Counter()
    groups = defaultdict(list)
    for row in rows:
        rid = row["rid"]
        record = transport.get(rid)
        server = server_results.get(rid)
        if rid in duplicates:
            exclusions["duplicate_transport_request"] += 1
            continue
        if record is None or server is None:
            exclusions["missing_transport_or_server_result"] += 1
            continue
        if (
            server.get("invocation_id") != row["invocation_id"]
            or server.get("context_id") != row["context_id"]
            or server.get("context_epoch") != row["context_epoch"]
        ):
            exclusions["server_identity_mismatch"] += 1
            continue
        if (
            not record["stream_consumed"]
            or record["first_raw_at_ms"] is None
            or record["last_raw_at_ms"] is None
        ):
            exclusions["unfinished_http_stream"] += 1
            continue
        groups[row["project"], row["label"]].append((
            row, record, float(server["ts_ms"]),
        ))

    results = {}
    for project, label in sorted(groups):
        group = groups[project, label]
        long = [
            (row, record, done)
            for row, record, done in group
            if record["last_raw_at_ms"] - record["first_raw_at_ms"] > 30_000
        ]
        after_server = [
            (row, record, done)
            for row, record, done in group
            if record["last_raw_at_ms"] + lower - CLOCK_GUARD_MS > done
        ]
        results[f"{project}|{label}"] = {
            "requests": len(group),
            "long_raw_stream_over_30s": len(long),
            "last_raw_after_server_end": len(after_server),
            "last_raw_after_server_end_min_p50_ms": _quantile([
                record["last_raw_at_ms"] + lower - done
                for _, record, done in after_server
            ], .5),
            "raw_span_p50_ms": _quantile([
                record["last_raw_at_ms"] - record["first_raw_at_ms"]
                for _, record, _ in group
            ], .5),
            "raw_span_p90_ms": _quantile([
                record["last_raw_at_ms"] - record["first_raw_at_ms"]
                for _, record, _ in group
            ], .9),
            "raw_pull_total_p50_ms": _quantile([
                record["raw_pull_total_ms"] for _, record, _ in group
            ], .5),
            "consumer_pause_total_p50_ms": _quantile([
                record["consumer_pause_total_ms"]
                for _, record, _ in group
            ], .5),
            "long_raw_pull_total_p50_ms": _quantile([
                record["raw_pull_total_ms"] for _, record, _ in long
            ], .5),
            "long_consumer_pause_total_p50_ms": _quantile([
                record["consumer_pause_total_ms"]
                for _, record, _ in long
            ], .5),
            "long_consumer_pause_fraction_p50": _quantile([
                record["consumer_pause_total_ms"] / max(
                    1., record["consumer_pause_total_ms"]
                    + record["raw_pull_total_ms"],
                )
                for _, record, _ in long
            ], .5),
        }
    return {
        "scope": (
            "development-only matched completed child requests. Pull time "
            "includes waiting for the next raw HTTP block. Consumer pause "
            "includes SDK parsing, callbacks, downstream processing and "
            "thread scheduling; it is not a callback-only timer. Future "
            "server completion is diagnostic, never a prediction feature."
        ),
        "collection": dict(collection),
        "clock_bracket": clocks,
        "excluded": dict(exclusions),
        "groups": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = evaluate(args.run)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
