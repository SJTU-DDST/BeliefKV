#!/usr/bin/env python3
"""Compare matched natural child returns across two development stream runs."""

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


def _load(run: Path) -> tuple[dict[tuple[str, str], dict], dict]:
    workflows = run / "workloads/workflows"
    rows, collection = collect(workflows, min_snapshot_chars=1)
    by_task: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["label"] == "return":
            by_task[row["task"]].append(row)

    indexed = {}
    for task, task_rows in by_task.items():
        workflow = workflows / task
        result = orjson.loads((workflow / "result.json").read_bytes())
        roles = {
            report["invocation_id"]: report.get("role")
            for report in orjson.loads((workflow / "child_reports.json").read_bytes())
        }
        transports = {}
        with (workflow / "child_stream_content.jsonl").open("rb") as stream:
            for line in stream:
                event = orjson.loads(line)
                if event.get("event") == "llm_stream_http_transport":
                    rid = event["request_id"]
                    if rid in transports:
                        raise ValueError(f"duplicate transport record: {task}: {rid}")
                    transports[rid] = event
        for row in task_rows:
            key = (task, row["invocation_id"])
            if key in indexed:
                raise ValueError(f"multiple natural returns for child: {key}")
            rid = row["rid"]
            transport = transports.get(rid)
            if transport is None or not transport["stream_consumed"]:
                continue
            snaps = row["snapshots"]
            indexed[key] = {
                "project": row["project"],
                "role": roles.get(row["invocation_id"]),
                "measurement_valid": result["measurement_valid"],
                "chars": snaps[-1]["content_chars"],
                "content_span_ms": snaps[-1]["ts_ms"] - snaps[0]["ts_ms"],
                "raw_span_ms": (
                    transport["last_raw_at_ms"] - transport["first_raw_at_ms"]
                ),
                "pull_ms": transport["raw_pull_total_ms"],
                "pause_ms": transport["consumer_pause_total_ms"],
                "raw_bytes": transport["raw_bytes"],
                "raw_chunks": transport["raw_chunks"],
                "first_to_return_ms": row["return_ts"] - snaps[0]["ts_ms"],
                "last_to_return_ms": row["return_ts"] - snaps[-1]["ts_ms"],
            }
    return indexed, dict(collection)


def _summary(pairs: list[tuple[dict, dict]]) -> dict:
    def side(field: str, index: int) -> dict:
        values = [pair[index][field] for pair in pairs]
        return {
            "p50": _quantile(values, .5),
            "p90": _quantile(values, .9),
        }

    return {
        "matched_children": len(pairs),
        "eos": {
            field: side(field, 0)
            for field in (
                "chars", "content_span_ms", "raw_span_ms", "pull_ms",
                "pause_ms", "raw_bytes", "raw_chunks",
                "first_to_return_ms", "last_to_return_ms",
            )
        },
        "content_only": {
            field: side(field, 1)
            for field in (
                "chars", "content_span_ms", "raw_span_ms", "pull_ms",
                "pause_ms", "raw_bytes", "raw_chunks",
                "first_to_return_ms", "last_to_return_ms",
            )
        },
        "eos_minus_content_p50_ms": {
            field: _quantile([eos[field] - content[field] for eos, content in pairs], .5)
            for field in ("content_span_ms", "raw_span_ms", "pull_ms", "pause_ms")
        },
        "long_raw_over_30s": {
            "eos": sum(eos["raw_span_ms"] > 30_000 for eos, _ in pairs),
            "content_only": sum(content["raw_span_ms"] > 30_000 for _, content in pairs),
        },
        "eos_raw_over_content_by_30s": sum(
            eos["raw_span_ms"] - content["raw_span_ms"] > 30_000
            for eos, content in pairs
        ),
    }


def compare(eos_run: Path, content_run: Path) -> dict:
    eos, eos_collection = _load(eos_run)
    content, content_collection = _load(content_run)
    common = sorted(eos.keys() & content.keys())
    role_mismatch = [
        {"task": task, "invocation_id": child, "eos_role": eos[task, child]["role"],
         "content_role": content[task, child]["role"]}
        for task, child in common
        if eos[task, child]["role"] != content[task, child]["role"]
    ]
    aligned = [
        (eos[key], content[key])
        for key in common
        if eos[key]["role"] == content[key]["role"]
    ]
    valid = [
        (a, b) for a, b in aligned
        if a["measurement_valid"] and b["measurement_valid"]
    ]
    valid_keys = [
        key for key in common
        if eos[key]["role"] == content[key]["role"]
        and eos[key]["measurement_valid"]
        and content[key]["measurement_valid"]
    ]
    similar_size = [
        (a, b) for a, b in valid
        if .5 <= a["chars"] / max(1, b["chars"]) <= 2
    ]
    return {
        "scope": (
            "Development-only pairs by task and child invocation identity. "
            "Different run trajectories are not controlled by pairing. "
            "No future server completion is used as a feature; timings are "
            "client-observed, and content spans cover sampled snapshots only."
        ),
        "eos_collection": eos_collection,
        "content_collection": content_collection,
        "eos_natural_returns_with_transport": len(eos),
        "content_natural_returns_with_transport": len(content),
        "matched_identity_count": len(common),
        "role_mismatch": role_mismatch,
        "valid_role_aligned_by_project": dict(Counter(
            a["project"] for a, _ in valid
        )),
        "valid_workflows": len({task for task, _ in valid_keys}),
        "valid_workflows_by_project": dict(Counter(
            task.split("__", 1)[0] for task in {task for task, _ in valid_keys}
        )),
        "role_aligned_all": _summary(aligned),
        "role_aligned_valid_workflows": _summary(valid),
        "role_aligned_valid_similar_chars": _summary(similar_size),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eos-run", type=Path, required=True)
    parser.add_argument("--content-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.write_text(
        json.dumps(compare(args.eos_run, args.content_run), indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
