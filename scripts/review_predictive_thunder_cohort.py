"""Compare predictive and ThunderAgent on identical arrivals and complete measured work."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.review_sustained_oracle import compare_cohorts
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import review_matches_execution, verify_execution


TRAFFIC_FIELDS = (
    "source_arm", "source_plan", "token_source_sha256", "source_capacity",
    "source_server_parameters", "engine_tree", "workflow_ids", "sampling_mode",
    "max_running_requests", "request_timeout_s", "stream_response_metrics",
    "workflow_arrivals", "workflow_arrivals_path", "tool_timing_overlay",
)


def matching_traffic(predictive, thunder):
    if predictive.get("order") != ["full"] or thunder.get("order") != ["thunderagent_acting_decay"]:
        raise ValueError("require predictive and the acting-decay ThunderAgent reproduction")
    for name in TRAFFIC_FIELDS:
        if name not in predictive or predictive[name] != thunder.get(name):
            raise ValueError("baseline changed fixed traffic or serving conditions: " + name)
    if thunder.get("oracle_information") is not None:
        raise ValueError("paper baseline must not declare oracle information")


def named_metrics(thunder, predictive):
    result = compare_cohorts(thunder, predictive)
    for group in result["groups"].values():
        group["thunderagent_JCT_ms"] = group.pop("control_JCT_ms")
        group["predictive_JCT_ms"] = group.pop("oracle_JCT_ms")
        group["thunderagent_completed_workflows"] = group.pop("control_completed_workflows")
        group["predictive_completed_workflows"] = group.pop("oracle_completed_workflows")
        for row in group["workflows"]:
            row["thunderagent_JCT_ms"] = row.pop("control_JCT_ms")
            row["predictive_JCT_ms"] = row.pop("oracle_JCT_ms")
    throughput = result["common_window_output_throughput"]
    throughput["thunderagent_tokens_per_second"] = throughput.pop("control_tokens_per_second")
    throughput["predictive_tokens_per_second"] = throughput.pop("oracle_tokens_per_second")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "predictive", "thunder", "predictive-review-dir", "thunder-review-dir",
        "cohort", "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output.exists():
        raise ValueError("hide CUDA and preserve earlier baseline comparisons")
    plans = [causal.load(path) for path in (args.predictive, args.thunder)]
    matching_traffic(*plans)
    for plan in plans:
        causal.comparison.verify_sources(plan)
    executions = [verify_execution(path, args.cohort) for path in (args.predictive, args.thunder)]
    if executions[0]["work"] != executions[1]["work"]:
        raise ValueError("baseline changed complete measured native work")
    cohorts, decisions, inputs = [], [], [args.predictive, args.thunder, args.cohort]
    for directory, execution in zip(
        (args.predictive_review_dir, args.thunder_review_dir), executions,
    ):
        review_path, cohort_path, decision_path = (
            directory / name for name in ("review.json", "cohort.json", "workload_decision.json")
        )
        review, cohort = causal.load(review_path), causal.load(cohort_path)
        review_matches_execution(review, execution)
        if cohort["cohort"] != causal.load(args.cohort):
            raise ValueError("baseline changed fixed cohort membership or throughput window")
        for name, prefix in cohort.get("input_jsonl_prefixes", {}).items():
            path = Path(name)
            if (
                path.stat().st_size != prefix["complete_prefix_bytes"]
                or sha256_file(path) != prefix["complete_prefix_sha256"]
            ):
                raise ValueError("completed cohort evidence changed")
        cohorts.append(cohort)
        decisions.append(causal.load(decision_path))
        inputs.extend((review_path, cohort_path, decision_path))
    result = named_metrics(cohorts[1], cohorts[0])
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        reference="thunderagent_acting_decay", candidate="predictive",
        positive_gain_means_predictive_is_better=True,
        complete_measured_work_fresh_drain_and_actual_exit=True,
        original_workload_decisions_preserved=decisions,
        formal_workload_accepted=False, single_run_statistical_significance_claimed=False,
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in (
            *inputs, Path(__file__), ROOT / "scripts/review_sustained_oracle.py",
            ROOT / "scripts/verify_measured_execution.py", ROOT / "scripts/stop_after_measured_cohort.py",
        )},
        GPU_action=False, automatic_GPU_followup=False, performance_accepted=False,
    )
    write(args.output, result)
    print({
        "predictive_mean_JCT_reduction_percent": result["groups"]["measured"]["mean_JCT_reduction_percent"],
        "common_window_output_throughput": result["common_window_output_throughput"],
        "checks": result["checks"],
    })


if __name__ == "__main__":
    main()
