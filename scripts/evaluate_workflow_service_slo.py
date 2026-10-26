#!/usr/bin/env python3
"""Evaluate shared workflow service budgets on complete captured streaming replays."""

import argparse
from itertools import product
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_metrics import evaluate_workflow_service_slo
from scripts.run_causal_comparison import validate_response_metrics


def evaluate(summaries, first_budgets, gap_budgets):
    if not summaries or len({label for label, _ in summaries}) != len(summaries):
        raise ValueError("service comparison needs unique named replay summaries")
    if not first_budgets or not gap_budgets:
        raise ValueError("service comparison needs both common budget ranges")
    reference = None
    results = {}
    for label, path in summaries:
        result = json.loads(path.read_bytes())
        if any(result.get(name) is not True for name in (
            "causal_execution_completed", "exact_tokens_verified",
            "original_native_stopping_verified", "streaming_response_metrics_enabled",
        )):
            raise ValueError("complete captured streaming replay is required")
        token_hash = result.get("token_source", {}).get("token_source_sha256")
        if not token_hash:
            raise ValueError("captured replay lacks exact source token identity")
        identity = {
            "source_plan": result["source_plan"], "token_source_sha256": token_hash,
            "workflow_ids": sorted(result["workflow_elapsed_ms"]),
        }
        if reference is not None and identity != reference:
            raise ValueError("workflow service comparison changes captured work")
        reference = identity
        validate_response_metrics({"stream_response_metrics": True, **identity}, result)
        metrics = result["streaming_response_metrics"]
        results[label] = {
            "replay_summary": str(path.resolve()), "replay_summary_sha256": sha256_file(path),
            "workflow_jct_ms": metrics["workflow_jct_ms"],
            "workflow_ttft_ms": metrics["workflow_ttft_ms"],
            "workflow_max_post_first_ready_output_gap_ms": metrics[
                "workflow_max_post_first_ready_output_gap_ms"
            ],
            "whole_run_output_tokens_per_second": (
                1000. * metrics["output_tokens"] / metrics["workflow_event_makespan_ms"]
            ),
            "coverage": metrics["coverage"],
            "service_budgets": [
                evaluate_workflow_service_slo(
                    metrics, first_output_budget_ms=first, ready_gap_budget_ms=gap,
                ) for first, gap in product(first_budgets, gap_budgets)
            ],
        }
    return {
        "shared_work": reference, "results": results, "all_normal_tails_retained": True,
        "first_output_budgets_ms": first_budgets, "post_first_ready_gap_budgets_ms": gap_budgets,
        "performance_accepted": False,
        "scope": (
            "Offline common-budget attainment on exact captured work and common streaming "
            "protocol. Capacity, serving parameters, GPU equivalence and policy validity "
            "still require the registered comparison operator."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", nargs=2, action="append", metavar=("ARM", "PATH"), required=True)
    parser.add_argument("--first-output-budget-ms", type=float, nargs="+", required=True)
    parser.add_argument("--ready-gap-budget-ms", type=float, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous service-budget reports")
    report = evaluate(
        [(label, Path(path)) for label, path in args.summary],
        args.first_output_budget_ms, args.ready_gap_budget_ms,
    )
    report["analysis_source_sha256"] = {
        name: sha256_file(ROOT / name) for name in (
            "scripts/evaluate_workflow_service_slo.py",
            "scripts/run_causal_comparison.py", "beliefkv/experiments/workflow_metrics.py",
        )
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "arms": list(report["results"]), "all_normal_tails_retained": True,
        "performance_accepted": False,
    }))


if __name__ == "__main__":
    main()
