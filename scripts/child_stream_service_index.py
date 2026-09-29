"""Offline-only request service status for delivered child stream snapshots."""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
from pathlib import Path

import orjson

from scripts.pilot_join_service_progress import CLOCK_GUARD_MS, clock_bracket

PRIOR_DECODE_MS = 2000.


def load_index(
    run: Path, rows: list[dict], *, workflows: Path | None = None,
) -> tuple[dict[str, dict], dict, dict]:
    workflows = workflows or run / "workloads/workflows"
    server = run / "server"
    lower, upper, bracket = clock_bracket(
        workflows, server / "runtime_events.sglang.jsonl",
    )
    by_rid = {row["rid"]: row for row in rows}
    if len(by_rid) != len(rows):
        raise ValueError("duplicate child request identity")
    server_end = {}
    exclusions = Counter()
    with (server / "runtime_events.sglang.jsonl").open("rb") as stream:
        for line in stream:
            event = orjson.loads(line)
            if event.get("kind") != "llm_result":
                continue
            rid = (event.get("attributes") or {}).get("request_id")
            row = by_rid.get(rid)
            if row is None:
                continue
            if (
                event.get("invocation_id") != row["invocation_id"]
                or event.get("context_id") != row["context_id"]
                or event.get("context_epoch") != row["context_epoch"]
            ):
                exclusions["server_identity_mismatch"] += 1
                continue
            if rid in server_end:
                exclusions["duplicate_server_result"] += 1
                continue
            server_end[rid] = float(event["ts_ms"])

    recent_decode: dict[str, list[float]] = defaultdict(list)
    with (server / "runtime_audit.jsonl").open("rb") as stream:
        for line in stream:
            event = orjson.loads(line)
            if (
                event.get("event") != "gpu_service_sample"
                or event.get("phase") != "decode"
            ):
                continue
            for sample in event.get("request_samples") or ():
                row = by_rid.get(sample.get("request_id"))
                if row is None:
                    continue
                if (
                    sample.get("invocation_id") == row["invocation_id"]
                    and sample.get("context_id") == row["context_id"]
                    and sample.get("context_epoch") == row["context_epoch"]
                    and sample.get("token_delta_semantics")
                    == "observed_output_ids_delta"
                    and type(sample.get("token_delta")) is int
                    and sample["token_delta"] > 0
                ):
                    recent_decode[row["rid"]].append(float(event["ts_ms"]))
    for samples in recent_decode.values():
        samples.sort()
    return {
        rid: {
            "server_end_ms": done,
            "offset_lower_ms": lower,
            "offset_upper_ms": upper,
            "decode_sample_times_ms": recent_decode.get(rid, []),
        }
        for rid, done in server_end.items()
    }, bracket, dict(exclusions)


def snapshot_status(
    state: dict | None, trigger_ms: float,
) -> str:
    """Classify a client trigger; server completion is never an online input."""
    if state is None:
        return "missing_server_result"
    done = state["server_end_ms"]
    lower = trigger_ms + state["offset_lower_ms"] - CLOCK_GUARD_MS
    upper = trigger_ms + state["offset_upper_ms"] + CLOCK_GUARD_MS
    if done < lower:
        return "server_finished_before_trigger"
    if done <= upper:
        return "clock_ambiguous"
    times = state["decode_sample_times_ms"]
    index = bisect_left(times, lower)
    if index and times[index - 1] >= lower - PRIOR_DECODE_MS:
        return "unfinished_with_recent_decode"
    return "unfinished_without_recent_decode"
