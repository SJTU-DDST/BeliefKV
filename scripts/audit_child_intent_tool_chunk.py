#!/usr/bin/env python3
"""Bound the lead of the first tool chunk before a child completion notice."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

import numpy as np


def audit(workflows: Path) -> dict:
    paths = sorted(workflows.glob("*/runtime_events.deepagents.jsonl"))
    if not paths:
        raise FileNotFoundError(f"missing workflow events in {workflows}")
    counts = Counter(workflows=len(paths))
    leads: list[float] = []
    for path in paths:
        events = [json.loads(line) for line in path.read_text().splitlines()]
        for start in events:
            if (
                start["kind"] != "tool_start"
                or (start.get("attributes") or {}).get("tool_name")
                != "announce_completion_intent"
            ):
                continue
            counts["intent_tool_starts"] += 1
            child = start.get("invocation_id")
            if not child:
                counts["missing_invocation"] += 1
                continue
            prior = [
                event for event in events
                if event["kind"] == "llm_result"
                and event.get("invocation_id") == child
                and not (event.get("attributes") or {}).get("runtime_internal")
                and float(event["ts_ms"]) < float(start["ts_ms"])
            ]
            if not prior:
                counts["missing_prior_result"] += 1
                continue
            result = max(prior, key=lambda event: float(event["ts_ms"]))
            attrs = result.get("attributes") or {}
            rid = attrs.get("request_id")
            if (
                not rid or attrs.get("finish_reason") != "tool_calls"
                or attrs.get("tool_call_count") != 1
            ):
                counts["ambiguous_result"] += 1
                continue
            submits = [
                event for event in events
                if event["kind"] == "llm_submit"
                and event.get("invocation_id") == child
                and (event.get("attributes") or {}).get("request_id") == rid
                and not (event.get("attributes") or {}).get("runtime_internal")
            ]
            chunks = [
                event for event in events
                if event["kind"] == "structured_action"
                and event.get("invocation_id") == child
                and (event.get("attributes") or {}).get("request_id") == rid
                and (event.get("attributes") or {}).get(
                    "beliefkv_child_first_tool_chunk_shadow"
                )
            ]
            if len(submits) != 1 or len(chunks) != 1:
                counts["missing_or_ambiguous_chunk"] += 1
                continue
            submit, chunk = submits[0], chunks[0]
            if (
                (submit.get("context_id"), submit.get("context_epoch"))
                != (result.get("context_id"), result.get("context_epoch"))
                or (submit.get("context_id"), submit.get("context_epoch"))
                != (chunk.get("context_id"), chunk.get("context_epoch"))
                or not (
                    float(submit["ts_ms"])
                    < float(chunk["ts_ms"])
                    <= float(result["ts_ms"])
                    < float(start["ts_ms"])
                )
            ):
                counts["invalid_identity_or_order"] += 1
                continue
            counts["matched_single_tool_rounds"] += 1
            leads.append(float(start["ts_ms"]) - float(chunk["ts_ms"]))
    return {
        "diagnostic_only": True,
        "counts": dict(counts),
        "first_chunk_to_intent_tool_start_ms": (
            {
                "count": len(leads),
                "p50": float(np.median(leads)),
                "p90": float(np.percentile(leads, 90)),
                "max": max(leads),
                "at_least_500ms": sum(lead >= 500 for lead in leads),
            } if leads else None
        ),
        "scope": (
            "Optimistic posthoc bound: a generic first tool chunk is matched "
            "to the later completion tool only after seeing the result. "
            "The chunk does not identify a completion tool online, and no "
            "prediction or physical migration is evaluated."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(audit(args.workflows), indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
