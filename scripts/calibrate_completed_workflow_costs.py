"""Freeze admission cost proxies from a verified completed nonranking Full."""

import argparse
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timezone
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.prepare_calibrated_combination_oracle import context_prefill_costs
from scripts.prepare_sustained_oracle_admission import calibrate_costs
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import review_matches_execution, verify_execution


def distribution(values):
    if not values or any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("cost distributions require positive observed worker costs")
    values = sorted(values)

    def quantile(fraction):
        offset = (len(values) - 1) * fraction
        low, high = math.floor(offset), math.ceil(offset)
        return values[low] + (values[high] - values[low]) * (offset - low)

    return {
        "count": len(values), "sum": sum(values), "min": min(values),
        "p50": quantile(.5), "p90": quantile(.9), "p99": quantile(.99), "max": max(values),
    }


def calibrate(registration, cohort, review_path, output):
    if output.exists():
        raise FileExistsError("preserve completed-control calibration attempts")
    completion = verify_execution(registration, cohort)
    review, plan = causal.load(review_path), causal.load(registration)
    review_matches_execution(review, completion)
    configuration = Path(plan["experiment_configuration"])
    original = Path(causal.load(configuration)["common_environment"]["WORKFLOW_WORK_MODEL"])
    artifact = causal.load(original)
    if plan["order"] != ["full"] or artifact.get("remaining_work_ranking_enabled") is not False:
        raise ValueError("calibrate from a completed matched nonranking Full")
    arm = Path(plan["run_root"]) / "full"
    batches, transfers = arm / "server/runtime_audit.jsonl", arm / "server/transfer_telemetry.jsonl"
    reader, groups = SealedRecords(), defaultdict(list)
    boundaries = [8192, 32768, 65536, 131072]
    names = ["8192", "32768", "65536", "131072", "at_least_131072"]
    totals, decode_steps, decode_ms = defaultdict(lambda: [0., 0]), 0, 0.

    def observed():
        nonlocal decode_steps, decode_ms
        for row in reader(batches):
            if row.get("event") == "gpu_service_sample":
                if row["phase"] == "decode":
                    decode_steps += row["decode_batch_size"]
                    decode_ms += row["decode_batch_size"] * row["service_elapsed_ms"]
                if row["phase"] == "prefill" and len(row["request_samples"]) == 1:
                    member = row["request_samples"][0]
                    tokens, prefix = member["token_delta"], member["sequence_tokens_before"]
                    if any(type(value) is not int or value < 0 for value in (tokens, prefix)):
                        raise ValueError("native prefill cost lacks valid extend/prefix geometry")
                    if tokens >= 256:
                        name = names[bisect_left(boundaries, prefix + tokens)]
                        groups[name].append(row["service_elapsed_ms"] / tokens)
                        totals[name][0] += row["service_elapsed_ms"]
                        totals[name][1] += tokens
            yield row

    costs, support = calibrate_costs(observed(), reader(transfers))
    step = decode_ms / decode_steps
    if not math.isclose(
        step / 1000., review["model_cost_calibration"]["model_seconds_per_work_token"],
        abs_tol=1e-12, rel_tol=0.,
    ):
        raise ValueError("cost reconstruction differs from the complete control review")
    if any(len(groups[name]) < 30 for name in names):
        raise ValueError("context costs require at least thirty substantial batches in every bucket")
    for path in (batches, transfers):
        prefix = reader.prefixes[str(path)]
        if path.stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(path) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed cost telemetry changed")
    paths = [
        registration, cohort, review_path, configuration, original, Path(completion["certificate"]),
        Path(__file__), ROOT / "scripts/prepare_sustained_oracle_admission.py",
        ROOT / "scripts/prepare_calibrated_combination_oracle.py",
    ]
    inputs = {str(path.resolve()): sha256_file(path) for path in paths}
    calibration = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(registration.resolve()),
        "configured_oracle": {
            "prefill_token_weight": .01,
            "model_seconds_per_work_token": artifact["model_seconds_per_work_token"],
        },
        "completed_control_cost_proxy": costs, "calibration_evidence": support,
        "completed_control_weighted_decode_step_ms": step,
        "input_jsonl_prefixes": reader.prefixes, "inputs_sha256": inputs,
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
        "observed_at": calibration["observed_at"],
        "source_registration": str(registration.resolve()),
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
        "inputs_sha256": inputs,
    }
    context_prefill_costs(context, calibration)
    write(output / "calibration.json", calibration)
    write(output / "context_prefill.json", context)
    return {
        "decode_step_ms": step, "prefill_ms_per_token": costs["prefill_ms_per_token"],
        "h2d_ms_per_token": costs["h2d_ms_per_token"],
        "context_bucket_counts": {name: len(groups[name]) for name in names}, "GPU_action": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "cohort", "review", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(calibrate(args.registration, args.cohort, args.review, args.output_dir), flush=True)


if __name__ == "__main__":
    main()
