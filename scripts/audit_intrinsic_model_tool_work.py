"""Report intrinsic work and parallel tool paths without serving or queue time."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.causal_replay import evaluate_causal_plan
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tool_timing_overlay import load_overlay
from beliefkv.experiments.workflow_source_work import reconstruct_work
from beliefkv.experiments.workload_mix import summary
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import write


def work_paths(graph, requests, totals, seconds_per_unit):
    service = {
        request: (row["input_increment"] / 100. + row["output_tokens"]) * seconds_per_unit * 1000.
        for request, row in requests.items()
    }
    combined = evaluate_causal_plan(graph, service)["workflow_elapsed_ms"]
    external = evaluate_causal_plan(graph, dict.fromkeys(requests, 0.))["workflow_elapsed_ms"]
    if combined.keys() != totals.keys() or external.keys() != totals.keys():
        raise ValueError("intrinsic work and causal paths have another population")
    return [{
        "workflow_id": workflow, "model_work_units": total / 100.,
        "total_model_service_proxy_s": total / 100. * seconds_per_unit,
        "tool_and_fixed_delay_path_s": external[workflow] / 1000.,
        "combined_causal_path_proxy_s": combined[workflow] / 1000.,
        "total_model_plus_external_path_proxy_s": total / 100. * seconds_per_unit
        + external[workflow] / 1000.,
    } for workflow, total in sorted(totals.items())]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-arm", type=Path, required=True)
    parser.add_argument("--tool-timing", type=Path, required=True)
    parser.add_argument("--cost-review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve prior intrinsic work audits")
    graph, expected, evidence = source_plan(args.source_arm)
    with CapturedTokenCorpus(args.source_arm / "server/request_tokens.jsonl", expected) as corpus:
        requests, totals = reconstruct_work(graph, corpus)
    review = json.loads(args.cost_review.read_bytes())
    coefficient = review["model_cost_calibration"]["model_seconds_per_work_token"]
    transformed, timing = load_overlay(graph, args.tool_timing)
    rows = work_paths(transformed, requests, totals, coefficient)
    metrics = {
        field: summary([row[field] for row in rows])
        for field in rows[0] if field != "workflow_id"
    }
    write(args.output, {
        "workflow_count": len(rows), "requests": len(requests), "rows": rows, "metrics": metrics,
        "seconds_per_model_work_unit": coefficient,
        "source_evidence": evidence, "tool_timing_evidence": timing,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            args.cost_review, args.tool_timing, Path(__file__),
            ROOT / "beliefkv/experiments/workflow_source_work.py",
        )},
        "scope": {
            "complete_intrinsic_model_work_conserved": True,
            "tool_path_parallel_max_not_sum": True,
            "source_clock_or_policy_queue_time_is_not_work": True,
            "model_service_is_a_fixed_cost_proxy_not_GPU_seconds": True,
            "causal_path_assumes_independent_model_service_not_shared_GPU_contention": True,
            "not_a_GPU_JCT_prediction_or_optimal_scheduling_bound": True,
            "GPU_action": False, "performance_accepted": False,
        },
    })
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
