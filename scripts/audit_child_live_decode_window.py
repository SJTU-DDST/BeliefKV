#!/usr/bin/env python3
"""Upper-bound child RETURN windows with a still-unfinished decode request."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

import orjson

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_child_http_stream_transport import _quantile
from scripts.pilot_child_stream_content import collect
from scripts.pilot_join_service_progress import CLOCK_GUARD_MS, clock_bracket

PRIOR_DECODE_MS = 2000.


def audit(run: Path) -> dict:
    workflows = run / "workloads/workflows"
    server = run / "server"
    rows, collection = collect(workflows, min_snapshot_chars=1)
    validity = {
        path.parent.name: orjson.loads(path.read_bytes())["measurement_valid"]
        for path in workflows.glob("*/result.json")
    }
    lower, upper, bracket = clock_bracket(
        workflows, server / "runtime_events.sglang.jsonl",
    )
    by_rid = {row["rid"]: row for row in rows}
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

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        rid = row["rid"]
        done = server_end.get(rid)
        if done is None:
            exclusions["missing_server_result"] += 1
            continue
        window = [
            snap for snap in row["snapshots"]
            if 500 <= row["return_ts"] - snap["ts_ms"] <= 2000
        ] if row["label"] == "return" else []
        unfinished = [
            snap for snap in window
            if done > snap["ts_ms"] + upper + CLOCK_GUARD_MS
        ]
        samples = recent_decode.get(rid, ())
        live = [
            snap for snap in unfinished
            if any(
                snap["ts_ms"] + lower - CLOCK_GUARD_MS
                - PRIOR_DECODE_MS <= when
                < snap["ts_ms"] + lower - CLOCK_GUARD_MS
                for when in samples
            )
        ]
        grouped[row["project"]].append({
            "label": row["label"],
            "join_last": row["join_last"],
            "measurement_valid": validity[row["task"]],
            "has_window": bool(window),
            "has_unfinished": bool(unfinished),
            "has_recent_decode": bool(live),
            "server_end_to_return_ms": (
                row["return_ts"] - (done - lower)
                if row["label"] == "return" else None
            ),
        })
    projects = {}
    for project, entries in sorted(grouped.items()):
        natural = [row for row in entries if row["label"] == "return"]
        last = [row for row in natural if row["join_last"]]
        valid = [row for row in natural if row["measurement_valid"]]
        valid_last = [row for row in valid if row["join_last"]]
        projects[project] = {
            "natural_returns": len(natural),
            "join_last_returns": len(last),
            "content_window_oracle": sum(row["has_window"] for row in natural),
            "window_before_server_end": sum(
                row["has_unfinished"] for row in natural
            ),
            "window_before_server_end_with_prior_decode": sum(
                row["has_recent_decode"] for row in natural
            ),
            "join_last_content_window_oracle": sum(
                row["has_window"] for row in last
            ),
            "join_last_window_before_server_end": sum(
                row["has_unfinished"] for row in last
            ),
            "join_last_window_with_prior_decode": sum(
                row["has_recent_decode"] for row in last
            ),
            "valid_workflow_natural_returns": len(valid),
            "valid_workflow_window_with_prior_decode": sum(
                row["has_recent_decode"] for row in valid
            ),
            "valid_workflow_join_last_returns": len(valid_last),
            "valid_workflow_join_last_window_with_prior_decode": sum(
                row["has_recent_decode"] for row in valid_last
            ),
            "tool_rounds_with_content": sum(
                row["label"] == "tool" for row in entries
            ),
            "server_end_to_return_p50_ms": _quantile([
                row["server_end_to_return_ms"] for row in natural
            ], .5),
        }
    return {
        "scope": (
            "Development-only offline oracle, not an online feature or "
            "calibrated model. Unfinished requests may still be queued. "
            "Prior decode means at least one identity-matched positive token "
            "delta in the preceding 2 seconds; missing samples are not proof "
            "that no decode occurred. Return windows require delivered content."
        ),
        "clock_bracket": bracket,
        "collection": dict(collection),
        "excluded": dict(exclusions),
        "projects": projects,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.write_text(
        json.dumps(audit(args.run), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
