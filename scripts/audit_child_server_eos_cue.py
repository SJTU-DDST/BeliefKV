#!/usr/bin/env python3
"""Audit server-local first EOS crossings against delivered child content."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pilot_child_stream_content import collect
from scripts.pilot_join_service_progress import clock_bracket


def expected_eos_ids(tokenizer_json: Path) -> frozenset[int]:
    tokenizer = json.loads(tokenizer_json.read_text(encoding="utf-8"))
    found = {
        row["content"]: row["id"]
        for row in tokenizer.get("added_tokens", ())
        if row.get("content") in {"<|im_end|>", "<|endoftext|>"}
        and row.get("special") is True
    }
    if set(found) != {"<|im_end|>", "<|endoftext|>"}:
        raise ValueError("model tokenizer is missing either Qwen EOS token")
    return frozenset(found.values())


def earliest_eligible_cue(
    crossing_ms: float, first_content_ms: float,
    result_ms: float, first_tool_chunk_ms: float,
) -> float | None:
    """Lower bound assuming an instantaneous server-to-runtime notification."""
    cue = max(crossing_ms, first_content_ms)
    return cue if cue < min(result_ms, first_tool_chunk_ms) else None


def _first_tool_chunks(workflows: Path) -> dict[str, float]:
    by_rid: dict[str, float] = {}
    for path in workflows.glob("*/child_stream_content.jsonl"):
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if row.get("event") != "child_stream_content" or not row.get("tool_chunk"):
                    continue
                rid = row["request_id"]
                by_rid[rid] = min(by_rid.get(rid, math.inf), float(row["ts_ms"]))
    return by_rid


def _native_results(events: Path) -> dict[str, dict]:
    results: dict[str, dict] = {}
    with events.open(encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            if event.get("kind") != "llm_result":
                continue
            rid = (event.get("attributes") or {}).get("request_id")
            if rid in results:
                raise ValueError(f"duplicate native result for request {rid}")
            results[rid] = event
    return results


def audit(
    run: Path, tokenizer_json: Path, *, include_request_cues: bool = False,
) -> dict:
    workflows = run / "workloads/workflows"
    rows, collection = collect(workflows, min_snapshot_chars=1)
    eos_ids = expected_eos_ids(tokenizer_json)
    server_event_path = run / "server/runtime_events.sglang.jsonl"
    server_results = _native_results(server_event_path)
    offset_lower, offset_upper, clock_evidence = clock_bracket(
        workflows, server_event_path
    )
    tool_chunks = _first_tool_chunks(workflows)
    by_rid = {row["rid"]: row for row in rows}
    if len(by_rid) != len(rows):
        raise ValueError("duplicate child request IDs")
    valid_workflows = {}
    for row in rows:
        if row["task"] in valid_workflows:
            continue
        result = json.loads(
            (workflows / row["task"] / "result.json").read_text(encoding="utf-8")
        )
        valid_workflows[row["task"]] = (
            result.get("workflow_id"), result.get("measurement_valid") is True
        )

    exclusions: Counter[str] = Counter()
    first: dict[tuple[str, float], dict] = {}
    invalid_rids: set[str] = set()
    with (run / "server/runtime_audit.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            rid = event.get("request_id")
            if rid not in by_rid:
                continue
            if event.get("event") == "targeted_pair_ordinal_invalid":
                invalid_rids.add(rid)
                continue
            if event.get("event") != "targeted_pair_first_crossing":
                continue
            row = by_rid[rid]
            workflow_id, valid = valid_workflows[row["task"]]
            native = server_results.get(rid)
            if not valid:
                exclusions["invalid_workflow"] += 1
                continue
            if (
                workflow_id != event.get("workflow_id")
                or event.get("invocation_id") != row["invocation_id"]
                or event.get("context_id") != row["context_id"]
                or event.get("context_epoch") != row["context_epoch"]
                or native is None
                or native.get("invocation_id") != row["invocation_id"]
                or native.get("context_id") != row["context_id"]
                or native.get("context_epoch") != row["context_epoch"]
            ):
                exclusions["identity_mismatch"] += 1
                invalid_rids.add(rid)
                continue
            ids = event.get("probe_token_ids")
            ordinal = event.get("output_token_ordinal")
            token_count = (native.get("attributes") or {}).get("output_tokens")
            if (
                not isinstance(ids, list)
                or len(ids) != 2
                or any(type(token_id) is not int for token_id in ids)
                or frozenset(ids) != eos_ids
                or type(ordinal) is not int
                or type(token_count) is not int
                or not 1 <= ordinal <= token_count
            ):
                exclusions["eos_id_or_ordinal_mismatch"] += 1
                invalid_rids.add(rid)
                continue
            if float(event["ts_ms"]) > float(native["ts_ms"]):
                exclusions["post_result_crossing"] += 1
                continue
            threshold = event.get("threshold")
            if type(threshold) not in (float, int) or not 0 < threshold < 1:
                exclusions["invalid_threshold"] += 1
                invalid_rids.add(rid)
                continue
            key = rid, float(threshold)
            if key in first:
                exclusions["duplicate_first_crossing"] += 1
                if float(event["ts_ms"]) < float(first[key]["ts_ms"]):
                    invalid_rids.add(rid)
                continue
            first[key] = event

    grouped: dict[tuple[str, float], Counter[str]] = defaultdict(Counter)
    request_cues: dict[str, dict] = {}
    for row in rows:
        if not valid_workflows[row["task"]][1]:
            continue
        group = row["project"]
        first_content = float(row["snapshots"][0]["ts_ms"])
        if include_request_cues:
            request_cues[row["rid"]] = {
                "project": group,
                "label": row["label"],
                "join_last": row["join_last"],
                "return_ts": row["return_ts"],
                "result_ts": row["result_ts"],
                "first_by_threshold": {},
            }
        for threshold in (.0001, .001, .01, .05, .1, .25, .5):
            score = grouped[group, threshold]
            score[f"{row['label']}_with_content"] += 1
            event = first.get((row["rid"], threshold))
            if event is None or row["rid"] in invalid_rids:
                continue
            score["first_server_crossings"] += 1
            crossing = float(event["ts_ms"]) - offset_lower
            if crossing < first_content:
                score["before_first_delivered_content"] += 1
            cue = earliest_eligible_cue(
                crossing, first_content, float(row["result_ts"]),
                tool_chunks.get(row["rid"], math.inf),
            )
            if cue is None:
                score["not_causally_eligible_at_client"] += 1
                continue
            if include_request_cues:
                request_cues[row["rid"]]["first_by_threshold"][str(threshold)] = cue
            score[f"eligible_{row['label']}"] += 1
            if row["label"] == "return":
                lead = float(row["return_ts"]) - cue
                if 500 <= lead <= 2000:
                    score["return_window_500_2000ms"] += 1
                    if row["join_last"]:
                        score["join_last_window_500_2000ms"] += 1
                if lead > 2000:
                    score["return_early_over_2000ms"] += 1
                if lead < 500:
                    score["return_late_under_500ms"] += 1
    return {
        "scope": (
            "Development-only, request-first server EOS crossing; same-model "
            "tokenizer IDs and request/context identity checked. Client eligibility "
            "assumes instantaneous server-to-runtime notification and is therefore "
            "an optimistic bound, NOT an implemented predictive action. No model "
            "threshold is selected on the held-out project."
        ),
        "tokenizer_sha256": hashlib.sha256(tokenizer_json.read_bytes()).hexdigest(),
        "eos_token_ids": sorted(eos_ids),
        "clock_bridge": {
            **clock_evidence,
            "conversion": (
                "server_wall_minus_offset_lower_bounds_latest_client_monotonic_cue"
            ),
        },
        "collection": dict(collection),
        "excluded": dict(exclusions),
        "invalid_child_requests": len(invalid_rids),
        "results": {
            project: {str(threshold): dict(counter)
                      for (name, threshold), counter in sorted(grouped.items())
                      if name == project}
            for project in sorted({row["project"] for row in rows})
        },
        **({"request_cues": request_cues} if include_request_cues else {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--tokenizer-json", type=Path, required=True)
    parser.add_argument("--include-request-cues", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = audit(
        args.run, args.tokenizer_json,
        include_request_cues=args.include_request_cues,
    )
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
