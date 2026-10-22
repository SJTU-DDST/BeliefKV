#!/usr/bin/env python3
"""CPU-only work audit after the registered ablation matrix completes."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.metrics.workload_compatibility import classify_workload
from scripts.run_internal_comparison import load, reservation


def build_report(plan: dict, state: dict, references: dict) -> dict:
    completed = state.get("completed_arms", [])
    metrics = load(Path(plan["run_root"]) / "module_comparison.json")["runs"]
    arms = {
        label: {
            "work": {name: metrics[label].get(name)
                     for name in ("output_tokens", "llm_requests", "input_tokens", "tool_calls")},
            "raw_mean_jct_seconds": metrics[label].get("jct_mean_seconds"),
            "outcome": state.get("arm_outcomes", {}).get(label),
            **classify_workload(metrics[label], references),
        }
        for label in completed
    }
    full = completed == plan["order"] and state.get("status") == "collected_performance_review_pending"
    counts = Counter(row["closest_reference"] or "unknown" for row in arms.values())
    majority = next((name for name in references if counts[name] > len(arms) / 2), None) if full else None
    return {
        "schema_version": 1, "completed_matrix": full,
        "completed_arms": completed, "expected_arms": plan["order"],
        "references": references, "arms": arms,
        "closer_reference_counts": dict(counts), "majority_closer_to": majority,
        "close_to_neither_count": sum(not row["close_references"] for row in arms.values()),
        "decision": "review_completed_work_audit_before_selecting_rerun" if full else
                    "wait_for_remaining_ablations_no_rerun_decision",
        "no_gpu_experiment_launched": True, "performance_accepted": False,
        "scope": (
            "Each ablation counts once; the historical Full is not another ablation vote. "
            "Distance is RMS log ratio of output tokens, LLM requests and input tokens. "
            "Report every dimension and 5% closeness separately. No arm is removed for "
            "being far from either reference; majority closeness does not establish a fair "
            "comparison or select a favorable run for publication."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--reference-comparison", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("workload auditing must run with GPU disabled")
    plan = load(args.registration)
    baseline = load(args.reference_comparison)["runs"]
    references = {
        "v123_full": baseline["full_no_frontier"],
        "native": baseline["native_matched"],
    }
    references = {
        name: {field: row[field] for field in (
            "input_tokens", "output_tokens", "llm_requests", "tool_calls",
        )} for name, row in references.items()
    }
    path = args.registration.with_suffix(".state.json")
    while True:
        state = load(path)
        if state.get("completed_arms"):
            try:
                report = build_report(plan, state, references)
            except (json.JSONDecodeError, FileNotFoundError):
                if not args.watch or not reservation.alive(state["watcher"]):
                    raise
                time.sleep(15)
                continue
            report["source_registration"] = str(args.registration.resolve())
            report["checked_at"] = reservation.now().isoformat()
            reservation.write_json(args.output, report)
            if report["completed_matrix"]:
                print(json.dumps({
                    "output": str(args.output), "majority_closer_to": report["majority_closer_to"],
                    "close_to_neither_count": report["close_to_neither_count"],
                    "no_gpu_experiment_launched": True,
                }))
                return
        if not args.watch:
            return
        if state.get("error") or not reservation.alive(state["watcher"]):
            raise ValueError("matrix stopped before completion; preserve the partial audit")
        time.sleep(15)


if __name__ == "__main__":
    main()
