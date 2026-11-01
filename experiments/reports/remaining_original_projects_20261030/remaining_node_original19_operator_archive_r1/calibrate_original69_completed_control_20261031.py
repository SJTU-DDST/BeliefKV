"""Calibrate Oracle worker-cost proxies from the completed matching original69."""

from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import sys

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-device-plan-operator-75414c7e")
MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
REPORTS = MAIN / "experiments/reports/remaining_original_projects_20261030"
HERE = REPORTS / "original69_completed_control_costs_r1"
CONTROL = OPS / "workflow_original69_initial12_mean70_predictive_control_20261031_r1.registration.json"
PREPARED = REPORTS / "original69_initial12_mean70_prepare_r1"
REVIEW = REPORTS / "measured_cohort_original69_initial12_mean70_predictive_control_r1/review.json"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.prepare_calibrated_combination_oracle import context_prefill_costs
from scripts.prepare_sustained_oracle_admission import calibrate_costs
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import review_matches_execution, verify_execution


def distribution(values):
    values = sorted(values)
    def percentile(fraction):
        offset = (len(values) - 1) * fraction
        left, right = math.floor(offset), math.ceil(offset)
        return values[left] + (values[right] - values[left]) * (offset - left)
    return {
        "count": len(values), "sum": sum(values), "min": min(values),
        "p50": percentile(.5), "p90": percentile(.9), "p99": percentile(.99),
        "max": max(values),
    }


def main():
    if HERE.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve reports and use nice19 CPU-only calibration")
    cohort_path = PREPARED / "analysis_cohort.json"
    completion = verify_execution(CONTROL, cohort_path)
    review = causal.load(REVIEW)
    review_matches_execution(review, completion)
    plan = causal.load(CONTROL)
    config = causal.load(Path(plan["experiment_configuration"]))
    artifact_path = Path(config["common_environment"]["WORKFLOW_WORK_MODEL"])
    artifact = causal.load(artifact_path)
    if artifact["remaining_work_ranking_enabled"] is not False or plan["order"] != ["full"] or (
        completion["measured_requests"] != 2827 or completion["measured_output_tokens"] != 390934
    ):
        raise ValueError("require the same completed nonranking original69 control")
    arm = Path(plan["run_root"]) / "full"
    batches, transfers = arm / "server/runtime_audit.jsonl", arm / "server/transfer_telemetry.jsonl"
    reader, groups = SealedRecords(), defaultdict(list)
    boundaries, names = [8192, 32768, 65536, 131072], [
        "8192", "32768", "65536", "131072", "at_least_131072",
    ]
    totals = defaultdict(lambda: [0., 0])
    decode_steps, weighted_decode = 0, 0.

    def observed_batches():
        nonlocal decode_steps, weighted_decode
        for row in reader(batches):
            if row.get("event") == "gpu_service_sample":
                if row["phase"] == "decode":
                    decode_steps += row["batch_size"]
                    weighted_decode += row["batch_size"] * row["service_elapsed_ms"]
                if row["phase"] == "prefill" and len(row["request_samples"]) == 1:
                    member = row["request_samples"][0]
                    tokens = member["token_delta"]
                    prefix = member["sequence_tokens_before"]
                    if type(tokens) is not int or tokens < 0 or type(prefix) is not int or prefix < 0:
                        raise ValueError("native prefill geometry is invalid")
                    if tokens >= 256:
                        name = names[bisect_left(boundaries, prefix + tokens)]
                        groups[name].append(row["service_elapsed_ms"] / tokens)
                        totals[name][0] += row["service_elapsed_ms"]
                        totals[name][1] += tokens
            yield row

    costs, support = calibrate_costs(observed_batches(), reader(transfers))
    step = weighted_decode / decode_steps
    if not math.isclose(
        step / 1000., review["model_cost_calibration"]["model_seconds_per_work_token"],
        abs_tol=1e-12, rel_tol=0.,
    ):
        raise ValueError("the independently reconstructed decode proxy differs from complete review")
    if any(len(groups[name]) < 30 for name in names):
        raise ValueError("new context calibration lacks the unchanged minimum30 support")
    for path in (batches, transfers):
        prefix = reader.prefixes[str(path)]
        if path.stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(path) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed control telemetry changed")
    inputs = (
        CONTROL, REVIEW, artifact_path, Path(plan["experiment_configuration"]), cohort_path,
        Path(completion["certificate"]), Path(__file__),
        ROOT / "scripts/prepare_sustained_oracle_admission.py",
        ROOT / "scripts/prepare_calibrated_combination_oracle.py",
    )
    report = {
        "observed_at": datetime.now(timezone.utc).isoformat(), "registration": str(CONTROL),
        "configured_oracle": {
            "prefill_token_weight": .01,
            "model_seconds_per_work_token": artifact["model_seconds_per_work_token"],
        },
        "completed_control_cost_proxy": costs, "calibration_evidence": support,
        "completed_control_weighted_decode_step_ms": step,
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in inputs},
        "scope": {
            "same_completed_control_not_partial_oracle": True,
            "fixed_measured_complete_other_workflows_right_censored": True,
            "worker_intervals_may_include_stalls_and_CPU_submission": True,
            "prefill_rate_from_batches_with_at_least_256_tokens": True,
            "decode_step_is_batch_context_weighted_latency_not_request_GPU_share": True,
            "frozen_oracle_configuration_unchanged": True,
        },
        "GPU_action": False, "performance_accepted": False,
    }
    context = {
        "observed_at": datetime.now(timezone.utc).isoformat(), "source_registration": str(CONTROL),
        "frozen_current_prefill_ms_per_token": costs["prefill_ms_per_token"],
        "single_request_prefill_batches_at_least_256_tokens_by_context_after_upper_bound": {
            name: {
                "batch_count": len(groups[name]), "ms_per_input_token": distribution(groups[name]),
                "total_worker_ms": totals[name][0], "total_input_tokens": totals[name][1],
                "weighted_ms_per_input_token": totals[name][0] / totals[name][1],
            } for name in names
        },
        "input_jsonl_prefixes": {str(batches): reader.prefixes[str(batches)]},
        "scope": {
            "same_completed_control": True,
            "sequence_tokens_before_is_native_prefix_not_total_input": True,
            "GPU_action": False, "worker_not_pure_GPU_cost": True,
        },
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in inputs},
    }
    context_prefill_costs(context, report)
    write(HERE / "calibration.json", report)
    write(HERE / "context_prefill.json", context)
    print({
        "matching_completed_control": "original69", "decode_step_ms": step,
        "prefill_ms_per_token": costs["prefill_ms_per_token"],
        "context_bucket_counts": {name: len(groups[name]) for name in names},
        "formal_workload_accepted": False, "GPU_action": False,
    }, flush=True)


if __name__ == "__main__":
    main()
