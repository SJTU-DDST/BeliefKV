"""Compare native SGLang replay with Predictive on exact fixed measured work."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.review_predictive_thunder_cohort import TRAFFIC_FIELDS
from scripts.review_sustained_oracle import compare_cohorts
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import review_matches_execution, verify_execution


def matching_traffic(predictive, native):
    if predictive.get("order") != ["full"] or native.get("order") != ["native_matched"]:
        raise ValueError("require Predictive and the native SGLang replay baseline")
    for name in TRAFFIC_FIELDS:
        if name not in predictive or predictive[name] != native.get(name):
            raise ValueError("native baseline changed fixed traffic or serving conditions: " + name)
    if native.get("oracle_information") is not None:
        raise ValueError("native baseline must not receive oracle information")


def named_metrics(native, predictive):
    result = compare_cohorts(native, predictive)
    for group in result["groups"].values():
        for old, new in (
            ("control_JCT_ms", "native_JCT_ms"), ("oracle_JCT_ms", "predictive_JCT_ms"),
            ("control_completed_workflows", "native_completed_workflows"),
            ("oracle_completed_workflows", "predictive_completed_workflows"),
        ):
            group[new] = group.pop(old)
        for row in group["workflows"]:
            row["native_JCT_ms"] = row.pop("control_JCT_ms")
            row["predictive_JCT_ms"] = row.pop("oracle_JCT_ms")
    throughput = result["common_window_output_throughput"]
    throughput["native_tokens_per_second"] = throughput.pop("control_tokens_per_second")
    throughput["predictive_tokens_per_second"] = throughput.pop("oracle_tokens_per_second")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("predictive", "native", "predictive-review-dir", "native-review-dir", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output.exists():
        raise ValueError("hide CUDA and preserve earlier native comparisons")
    plans = [causal.load(path) for path in (args.predictive, args.native)]
    matching_traffic(*plans)
    for plan in plans:
        causal.comparison.verify_sources(plan)
    executions = [verify_execution(path, args.cohort) for path in (args.predictive, args.native)]
    if executions[0]["work"] != executions[1]["work"]:
        raise ValueError("native comparison changed complete measured native work")
    cohorts, inputs, capacities = [], [args.predictive, args.native, args.cohort], []
    for plan, directory, execution in zip(
        plans, (args.predictive_review_dir, args.native_review_dir), executions, strict=True,
    ):
        review_path, cohort_path = directory / "review.json", directory / "cohort.json"
        review, cohort = causal.load(review_path), causal.load(cohort_path)
        review_matches_execution(review, execution)
        if cohort["cohort"] != causal.load(args.cohort):
            raise ValueError("native comparison changed fixed cohort membership or window")
        for name, prefix in cohort.get("input_jsonl_prefixes", {}).items():
            path = Path(name)
            if path.stat().st_size != prefix["complete_prefix_bytes"] or (
                sha256_file(path) != prefix["complete_prefix_sha256"]
            ):
                raise ValueError("completed cohort evidence changed")
        capacity_path = Path(plan["run_root"]) / plan["order"][0] / "server/native_capacity_census.json"
        capacities.append(causal.load(capacity_path)["capacity"])
        cohorts.append(cohort)
        inputs.extend((review_path, cohort_path, capacity_path))
    if capacities[0] != capacities[1]:
        raise ValueError("actual native/Predictive KV pool capacities differ")
    result = named_metrics(cohorts[1], cohorts[0])
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        reference="native_sglang", candidate="predictive",
        positive_gain_means_predictive_is_better=True,
        complete_measured_work_fresh_drain_and_actual_exit=True,
        same_actual_capacity=capacities[0],
        matched_token_replay_instrumentation_and_common_engine=True,
        unmodified_upstream_native_source_claimed=False,
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
        "same_actual_capacity": True,
    })


if __name__ == "__main__":
    main()
