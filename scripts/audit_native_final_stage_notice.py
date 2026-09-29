#!/usr/bin/env python3
"""Audit native child completion notices against natural child returns."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import median


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    return ordered[lower] + (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]) * (position - lower)


def _distribution(values: list[float]) -> dict | None:
    if not values:
        return None
    return {
        "count": len(values),
        "p50_ms": median(values),
        "p90_ms": _percentile(values, 0.9),
        "p95_ms": _percentile(values, 0.95),
        "max_ms": max(values),
    }


def audit(client_dir: Path) -> dict:
    counts: Counter[str] = Counter()
    lead, submit_wait, service, return_gap = [], [], [], []
    paths = sorted(client_dir.glob("workflows/*/runtime_events.deepagents.jsonl"))
    if not paths:
        raise ValueError(f"no workflow events in {client_dir}")
    for path in paths:
        counts["workflow_traces"] += 1
        with path.open(encoding="utf-8") as stream:
            events = [json.loads(line) for line in stream if line.strip()]
        children = {
            row.get("target_invocation_id") for row in events
            if row.get("kind") == "spawn" and row.get("target_invocation_id")
        }
        counts["spawned_children"] += len(children)
        for child in children:
            child_events = [
                row for row in events if row.get("invocation_id") == child
            ]
            natural_terminals = [
                row for row in child_events
                if row.get("kind") == "return"
                and row.get("attributes", {}).get("outcome") == "completed"
                and row.get("attributes", {}).get("child_report_status") != "blocked"
            ]
            notices = [
                row for row in child_events
                if row.get("kind") == "tool_end"
                and row.get("attributes", {}).get("tool_name")
                == "announce_completion_intent"
                and row["attributes"].get("status") == "success"
            ]
            if not notices:
                counts["children_without_notice"] += 1
                counts[
                    "natural_returns_without_notice"
                    if natural_terminals else "unreturned_children_without_notice"
                ] += 1
                continue
            counts["announced_children"] += 1
            counts["extra_notices"] += max(0, len(notices) - 1)
            notice = notices[-1]
            starts = [
                row for row in child_events
                if row.get("kind") == "tool_start"
                and row.get("attributes", {}).get("tool_name")
                == "announce_completion_intent"
                and row["ts_ms"] <= notice["ts_ms"]
            ]
            if starts:
                counts["notice_tool_calls"] += 1
            later_tools = [
                row for row in child_events
                if row.get("kind") == "tool_start"
                and row["ts_ms"] > notice["ts_ms"]
                and row.get("attributes", {}).get("tool_name")
                != "announce_completion_intent"
            ]
            if later_tools:
                counts["notices_invalidated_by_later_tool"] += 1
                continue
            terminals = [
                row for row in natural_terminals
                if row["ts_ms"] > notice["ts_ms"]
            ]
            if not terminals:
                counts["notices_without_natural_return"] += 1
                continue
            end = terminals[0]["ts_ms"]
            counts["paired_natural_returns"] += 1
            lead.append(end - notice["ts_ms"])
            submits = [
                row for row in child_events
                if row.get("kind") == "llm_submit"
                and notice["ts_ms"] < row["ts_ms"] < end
                and not row.get("attributes", {}).get("runtime_internal")
            ]
            results = [
                row for row in child_events
                if row.get("kind") == "llm_result"
                and notice["ts_ms"] < row["ts_ms"] < end
                and not row.get("attributes", {}).get("runtime_internal")
            ]
            if len(submits) == len(results) == 1 and submits[0]["ts_ms"] <= results[0]["ts_ms"]:
                counts["single_final_request"] += 1
                submit_wait.append(submits[0]["ts_ms"] - notice["ts_ms"])
                service.append(results[0]["ts_ms"] - submits[0]["ts_ms"])
                return_gap.append(end - results[0]["ts_ms"])
            else:
                counts["other_final_request_shape"] += 1
    return {
        "counts": dict(counts),
        "notice_to_return": _distribution(lead),
        "notice_to_final_submit": _distribution(submit_wait),
        "final_submit_to_result": _distribution(service),
        "final_result_to_return": _distribution(return_gap),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.client_dir), indent=2))


if __name__ == "__main__":
    main()
