#!/usr/bin/env python3
"""Compare stream content milestones with the final visible response length."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.audit_native_stream_shadow import _rows


def audit(workflows: Path) -> dict:
    totals = Counter()
    by_project: dict[str, Counter] = defaultdict(Counter)
    examples = []
    for path in sorted(workflows.glob("*/runtime_events.deepagents.jsonl")):
        project = path.parent.name.split("__", 1)[0]
        results = {}
        milestones = defaultdict(set)
        for event in _rows(path):
            attrs = event.get("attributes") or {}
            request_id = attrs.get("request_id")
            if not request_id:
                continue
            if event["kind"] == "llm_result":
                results[request_id] = attrs
            elif (
                event["kind"] == "structured_action"
                and attrs.get("beliefkv_child_substantial_content_shadow")
            ):
                milestones[request_id].add(int(attrs["content_threshold_chars"]))
        for request_id, thresholds in milestones.items():
            result = results.get(request_id)
            for counts in (totals, by_project[project]):
                counts["requests_with_milestone"] += 1
            if result is None:
                totals["no_result"] += 1
                by_project[project]["no_result"] += 1
                continue
            max_milestone = max(thresholds)
            final_chars = int(result.get("output_chars") or 0)
            for counts in (totals, by_project[project]):
                counts["paired"] += 1
                if max_milestone > final_chars:
                    counts["milestone_exceeds_final_visible_chars"] += 1
                if max_milestone >= 1024:
                    counts["paired_1024_or_more"] += 1
                    if max_milestone > final_chars:
                        counts["large_milestone_exceeds_final"] += 1
            if max_milestone > final_chars and len(examples) < 12:
                examples.append({
                    "workflow": path.parent.name,
                    "request_id": request_id,
                    "max_milestone": max_milestone,
                    "final_visible_chars": final_chars,
                    "finish_reason": result.get("finish_reason"),
                    "tool_call_count": result.get("tool_call_count"),
                })
    return {
        "status": "read_only_stream_content_accounting_no_physical_actions",
        "totals": dict(totals),
        "by_project": {
            project: dict(counts) for project, counts in sorted(by_project.items())
        },
        "examples": examples,
        "limitation": (
            "Streamed content may differ from final parsed visible text. "
            "An excess milestone invalidates visible-character ETA features "
            "for that request; this report alone cannot identify its cause."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.workflows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["totals"], indent=2))


if __name__ == "__main__":
    main()
