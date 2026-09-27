#!/usr/bin/env python3
"""Audit exact final-report tokens against hypothetical completion caps."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

import numpy as np

try:
    from scripts.evaluate_child_return_intent_timing import load_episodes
    from scripts.evaluate_cold_tool_project_loo import require_complete_batch
except ModuleNotFoundError:
    from evaluate_child_return_intent_timing import load_episodes
    from evaluate_cold_tool_project_loo import require_complete_batch


CAPS = (256, 384, 512, 768, 1024)


def _summary(samples: list[dict]) -> dict:
    if not samples:
        return {"matched_natural_reports": 0}
    tokens = np.asarray([row["output_tokens"] for row in samples])
    duration = np.asarray([row["final_request_ms"] for row in samples])
    return {
        "matched_natural_reports": len(samples),
        "output_tokens_p50_p90_p95": [
            float(x) for x in np.percentile(tokens, (50, 90, 95))
        ],
        "final_request_ms_p50_p90_p95": [
            float(x) for x in np.percentile(duration, (50, 90, 95))
        ],
        "would_exceed_token_cap": {
            str(cap): int(np.sum(tokens > cap)) for cap in CAPS
        },
    }


def audit(run_root: Path) -> dict:
    workflows = run_root / "intent_workloads" / "workflows"
    ids, runner_errors = require_complete_batch(workflows)
    episodes, counts = load_episodes(workflows.parent)
    wanted = {}
    for row in episodes:
        post = row.get("post_notice")
        if not post or not post.get("final_request_id"):
            continue
        rid = post["final_request_id"]
        if rid in wanted:
            raise ValueError(f"duplicate final request ID: {rid}")
        wanted[rid] = row
    server_path = run_root / "server" / "runtime_events.sglang.jsonl"
    if not server_path.is_file():
        raise FileNotFoundError(f"missing SGLang request telemetry: {server_path}")
    tokens = {}
    with server_path.open(encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            attrs = event.get("attributes") or {}
            rid = attrs.get("request_id")
            if event.get("kind") != "llm_result" or rid not in wanted:
                continue
            if rid in tokens:
                raise ValueError(f"duplicate server result for request {rid}")
            if isinstance(attrs.get("output_tokens"), int):
                tokens[rid] = attrs["output_tokens"]
    by_project = defaultdict(list)
    for rid, row in wanted.items():
        if rid not in tokens:
            continue
        sample = {
            "output_tokens": tokens[rid],
            "final_request_ms": row["post_notice"]["llm_submit_to_result_ms"],
        }
        by_project[row["project"]].append(sample)
    return {
        "status": "read_only_hypothetical_cap_not_a_quality_ablation",
        "frozen_workflows": len(ids),
        "runner_errors": runner_errors,
        "natural_child_returns": counts["natural_returns"],
        "natural_notice_episodes": len(episodes),
        "episodes_without_single_final_request": len(episodes) - len(wanted),
        "missing_server_result": len(set(wanted) - set(tokens)),
        "matched": _summary([
            sample for samples in by_project.values() for sample in samples
        ]),
        "by_project": {
            project: _summary(samples)
            for project, samples in sorted(by_project.items())
        },
        "interpretation": (
            "Server output_tokens is an observed request-level count joined by "
            "request ID to a naturally returned child's final model call. "
            "would_exceed_token_cap counts past responses longer than each "
            "hypothetical cap; it does not estimate exact truncation, quality "
            "loss, counterfactual latency or future JOIN timing. The final "
            "request duration also contains queuing/service stalls."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.run_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
