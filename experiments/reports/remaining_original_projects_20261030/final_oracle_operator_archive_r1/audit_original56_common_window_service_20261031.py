"""Compare a complete common wall window without estimating unfinished JCT."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys


def measure(plan, cohort, reader):
    arm = Path(plan["run_root"]) / "full"
    offsets = {row["workflow_id"]: row["offset_seconds"] for row in plan["workflow_arrivals"]["arrival_schedule"]}
    origins = [
        row["ts_ms"] - offsets[row["workflow_id"]] * 1000.
        for row in reader(arm / "replay/runtime_events.replay.jsonl")
        if row["kind"] == "workflow_start"
    ]
    if not origins or max(origins) - min(origins) > 1e-5:
        raise ValueError("common window lacks one frozen arrival origin")
    origin = origins[0]
    left, right = cohort["throughput_window_offset_seconds"]
    if not 0 <= left < right:
        raise ValueError("invalid common wall window")
    bins = [{
        "start_s": left + 60 * index, "end_s": min(right, left + 60 * (index + 1)),
        "output_tokens": 0, "decode_tokens": 0, "decode_batches": 0,
    } for index in range(math.ceil((right - left) / 60))]
    counts, sums, widths, seen = Counter(), Counter(), Counter(), set()
    last = -math.inf
    for row in reader(arm / "server/runtime_audit.jsonl"):
        if row.get("event") != "gpu_service_sample":
            continue
        forward, when = row["forward_iter"], row["complete_monotonic_ms"]
        if forward in seen or not math.isfinite(when):
            raise ValueError("duplicate or invalid native service observation")
        seen.add(forward)
        offset = (when - origin) / 1000.
        last = max(last, offset)
        if not left <= offset < right:
            continue
        if row["phase"] not in ("prefill", "decode"):
            raise ValueError("expected the declared non-mixed native phases")
        outputs = [member.get("output_token_delta") for member in row["request_samples"]]
        elapsed = row["service_elapsed_ms"]
        if (
            any(type(value) is not int or value < 0 for value in outputs)
            or type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0
        ):
            raise ValueError("native service lacks valid output deltas or worker intervals")
        output = sum(outputs)
        if output and not elapsed:
            raise ValueError("positive output lacks a native worker interval")
        bucket = bins[min(int((offset - left) / 60), len(bins) - 1)]
        bucket["output_tokens"] += output
        counts["output_tokens"] += output
        counts[row["phase"] + "_batches"] += 1
        sums[row["phase"] + "_worker_interval_ms"] += elapsed
        if row["phase"] == "decode":
            width = row["decode_batch_size"]
            if type(width) is not int or not 1 <= width <= 24 or width != len(outputs):
                raise ValueError("native decode geometry changed")
            counts["decode_tokens"] += output
            counts["zero_output_decode_batches"] += not output
            bucket["decode_tokens"] += output
            bucket["decode_batches"] += 1
            sums["decode_declared_request_ms"] += width * elapsed
            sums["decode_productive_request_ms"] += sum(value > 0 for value in outputs) * elapsed
            widths[width] += elapsed
    if last < right:
        raise ValueError("the full shared throughput window is not observed yet")
    for bucket in bins:
        duration = bucket["end_s"] - bucket["start_s"]
        bucket["output_tokens_per_second"] = bucket["output_tokens"] / duration
        bucket["decode_tokens_per_second"] = bucket["decode_tokens"] / duration
    worker = sums["decode_worker_interval_ms"]
    if worker <= 0:
        raise ValueError("common window has no positive decode interval")
    return {
        "origin_monotonic_ms": origin, "start_s": left, "end_s": right,
        "duration_s": right - left, "counts": dict(counts),
        "output_tokens_per_second": counts["output_tokens"] / (right - left),
        "decode_tokens_per_second": counts["decode_tokens"] / (right - left),
        "decode_worker_weighted_declared_width": sums["decode_declared_request_ms"] / worker,
        "decode_worker_weighted_productive_width": sums["decode_productive_request_ms"] / worker,
        "worker_intervals_ms": dict(sums), "decode_worker_ms_by_width": dict(widths),
        "bins": bins,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("checkout", "control", "oracle", "cohort", "control-cohort-review", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve prior common-window comparisons")
    sys.path.insert(0, str(args.checkout))
    from beliefkv.experiments.source_provenance import sha256_file
    from scripts import run_causal_comparison as causal
    from scripts.audit_sealed_wait_prefetch import SealedRecords
    from scripts.register_sustained_oracle import matching_conditions

    plans = [causal.load(path) for path in (args.control, args.oracle)]
    matching_conditions(*(causal.load(Path(row["experiment_configuration"])) for row in plans))
    if any(plans[0][name] != plans[1][name] for name in (
        "source_plan", "token_source_sha256", "source_capacity", "engine_tree",
        "sampling_mode", "workflow_ids", "workflow_arrivals",
    )):
        raise ValueError("the paired runs changed work, data plane, or arrival conditions")
    cohort, reference = causal.load(args.cohort), causal.load(args.control_cohort_review)
    if cohort != reference["cohort"]:
        raise ValueError("changed measurement cohort")
    reader = SealedRecords()
    before, after = (measure(row, cohort, reader) for row in plans)
    if not math.isclose(
        before["output_tokens_per_second"], reference["common_window_output_tokens_per_second"],
        rel_tol=1e-12,
    ):
        raise ValueError("all-phase output differs from the completed control cohort audit")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "control": before, "oracle": after,
        "all_output_throughput_change_percent": 100 * (
            after["output_tokens_per_second"] / before["output_tokens_per_second"] - 1
        ),
        "decode_output_throughput_change_percent": 100 * (
            after["decode_tokens_per_second"] / before["decode_tokens_per_second"] - 1
        ),
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            args.control, args.oracle, args.cohort, args.control_cohort_review, Path(__file__),
        )},
        "scope": {
            "complete_identical_arrival_based_wall_window": True,
            "background_output_included": True,
            "all_phase_output_delta_used_not_prefill_input_delta": True,
            "completed_control_reconciled_to_cohort_audit": True,
            "unfinished_cohort_JCT_estimated": False,
            "worker_intervals_are_not_CUDA_kernel_time": True,
            "coalescing_and_oracle_ranking_are_joint_change": True,
            "independently_sealed_prefixes_not_atomic": True,
            "GPU_action": False,
        },
        "performance_accepted": False,
    }
    causal.reservation.write_json(args.output, result)
    print(json.dumps({
        "control_output_tokens_per_s": before["output_tokens_per_second"],
        "oracle_output_tokens_per_s": after["output_tokens_per_second"],
        "all_output_change_percent": result["all_output_throughput_change_percent"],
        "decode_output_change_percent": result["decode_output_throughput_change_percent"],
    }))


if __name__ == "__main__":
    main()
