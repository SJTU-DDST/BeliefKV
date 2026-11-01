"""Account observed token costs without confusing worker time with CUDA time."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import argparse
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_decode_batch_efficiency import OutputConservation, padding_bucket
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


def context_bin(tokens):
    for limit in (8192, 32768, 65536, 131072, 262144):
        if tokens <= limit:
            return "le_" + str(limit)
    return "gt_262144"


def add_cost(counter, row):
    samples = row["request_samples"]
    elapsed = row["service_elapsed_ms"]
    counter["forwards"] += 1
    counter["worker_ms"] += elapsed
    counter["request_steps"] += len(samples)
    counter["output_tokens"] += sum(member["output_token_delta"] for member in samples)
    counter["zero_output_request_steps"] += sum(
        not member["output_token_delta"] for member in samples
    )
    counter["context_tokens"] += sum(member["sequence_tokens_before"] for member in samples)
    counter["productive_row_worker_ms"] += elapsed * sum(
        member["output_token_delta"] > 0 for member in samples
    )
    counter["request_row_worker_ms"] += elapsed * len(samples)
    if row["phase"] == "prefill":
        counter["executed_extend_tokens"] += sum(member["token_delta"] for member in samples)
    elif row["cuda_graph_replayed"]:
        counter["graph_real_rows"] += len(samples)
        counter["graph_bucket_rows"] += row["configured_bucket"]
        counter["padded_forwards"] += row["configured_bucket"] != len(samples)


def summarize(counter):
    values = dict(counter)
    output, steps = counter["output_tokens"], counter["request_steps"]
    worker = counter["worker_ms"]
    values.update(
        worker_ms_per_output_token=worker / output if output else None,
        worker_ms_per_executed_extend_token=(
            worker / counter["executed_extend_tokens"]
            if counter["executed_extend_tokens"] else None
        ),
        mean_context_tokens=counter["context_tokens"] / steps if steps else None,
        worker_weighted_productive_width=(
            counter["productive_row_worker_ms"] / worker if worker else None
        ),
        worker_weighted_declared_width=(
            counter["request_row_worker_ms"] / worker if worker else None
        ),
        configured_padding_rows_per_real_row=(
            counter["graph_bucket_rows"] / counter["graph_real_rows"] - 1
            if counter["graph_real_rows"] else None
        ),
    )
    return values


def analyze(rows, *, buckets, receipts, measured, window):
    conservation = OutputConservation()
    totals, phases = defaultdict(Counter), defaultdict(Counter)
    widths, contexts, prefill_sizes = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)
    intervals, gaps, representatives = [], [], {}
    measured_outputs, seen, domains = Counter(), set(), set()
    previous_end = -math.inf
    for row in rows:
        if row.get("event") != "gpu_service_sample":
            continue
        start, end = row["service_start_monotonic_ms"], row["complete_monotonic_ms"]
        if (
            row["forward_iter"] in seen or row["phase"] not in ("decode", "prefill")
            or not all(type(value) in (int, float) and math.isfinite(value) for value in (start, end))
            or end < start or start < previous_end - .001
            or not math.isclose(end - start, row["service_elapsed_ms"], abs_tol=.001)
            or row["batch_size"] != len(row["request_samples"])
            or any(member["phase"] != row["phase"] for member in row["request_samples"])
        ):
            raise ValueError("invalid, overlapping or mixed worker interval")
        conservation.observe(row)
        seen.add(row["forward_iter"])
        domains.add(row["monotonic_clock_domain"])
        intervals.append((start, end))
        if previous_end != -math.inf:
            gaps.append(start - previous_end)
        previous_end = end
        if row["phase"] == "decode":
            row = {
                **row, "configured_bucket": padding_bucket(
                    row["batch_size"], buckets, graph=row["cuda_graph_replayed"],
                ),
            }
        add_cost(totals["whole_observed_execution"], row)
        add_cost(phases["whole:" + row["phase"]], row)
        for member in row["request_samples"]:
            if member["workflow_id"] in measured:
                measured_outputs[member["request_id"]] += member["output_token_delta"]
        if not window[0] <= end < window[1]:
            continue
        add_cost(totals["common_window"], row)
        add_cost(phases["window:" + row["phase"]], row)
        mean_context = sum(
            member["sequence_tokens_before"] for member in row["request_samples"]
        ) / row["batch_size"]
        if row["phase"] == "decode":
            width = row["batch_size"]
            add_cost(widths[width], row)
            add_cost(contexts[context_bin(mean_context)], row)
            key = (width, context_bin(mean_context))
            # Pick a typical-cost actual member vector, not a synthetic average.
            target = 15.
            if key not in representatives or abs(row["service_elapsed_ms"] - target) < abs(
                representatives[key]["worker_ms"] - target
            ):
                representatives[key] = {
                    "forward_iter": row["forward_iter"], "width": width,
                    "configured_bucket": row["configured_bucket"],
                    "worker_ms": row["service_elapsed_ms"],
                    "context_lengths": [
                        member["sequence_tokens_before"] for member in row["request_samples"]
                    ],
                    "request_ids": [member["request_id"] for member in row["request_samples"]],
                }
        else:
            size = sum(member["token_delta"] for member in row["request_samples"])
            bucket = "le_64" if size <= 64 else "le_256" if size <= 256 else "le_1024" if size <= 1024 else "gt_1024"
            add_cost(prefill_sizes[bucket], row)
    if len(domains) != 1 or not intervals:
        raise ValueError("one nonempty worker clock domain is required")
    for rid, receipt in receipts.items():
        if conservation.ends.get(rid) != receipt["output_tokens"]:
            raise ValueError("completed native request output differs from its receipt: " + rid)
    expected_measured = {
        rid: receipt["output_tokens"] for rid, receipt in receipts.items()
        if receipt["native_receipt"]["identity"][0] in measured
    }
    if dict(measured_outputs) != expected_measured:
        raise ValueError("measured request output was incomplete or unreceipted")
    left, right = window
    coverage = sum(max(0., min(end, right) - max(start, left)) for start, end in intervals)
    return {
        "scopes": {key: summarize(value) for key, value in totals.items()},
        "phase_costs": {key: summarize(value) for key, value in phases.items()},
        "common_window_decode_by_width": {
            str(key): summarize(value) for key, value in sorted(widths.items())
        },
        "common_window_decode_by_mean_context": {
            key: summarize(value) for key, value in contexts.items()
        },
        "common_window_prefill_by_executed_tokens": {
            key: summarize(value) for key, value in prefill_sizes.items()
        },
        "between_worker_intervals_ms": distribution(gaps),
        "common_window_worker_coverage_ms": coverage,
        "common_window_outside_worker_intervals_ms": right - left - coverage,
        "output_conservation": {
            "completed_requests": len(receipts),
            "completed_output_tokens": sum(row["output_tokens"] for row in receipts.values()),
            "measured_requests": len(expected_measured),
            "measured_output_tokens": sum(expected_measured.values()),
            "incomplete_background_requests_with_observed_output": sorted(
                set(conservation.ends) - receipts.keys()
            ),
            "all_completed_receipts_match_observed_output_ordinals": True,
            "measured_requests_complete": True,
        },
        "representative_decode_batches": list(representatives.values()),
        "service_rows": len(seen),
    }


def inspect(registration, cohort_path):
    plan, cohort = (json.loads(path.read_bytes()) for path in (registration, cohort_path))
    arm, reader = Path(plan["run_root"]) / "full", SealedRecords()
    info_path = arm / "server/server_info.json"
    status_path = arm / "server/native_telemetry_status.json"
    info, status = (json.loads(path.read_bytes()) for path in (info_path, status_path))
    if status["writer_queue_depth"] != 0 or info["enable_mixed_chunk"] or info.get("speculative_algorithm"):
        raise ValueError("this audit requires drained ordinary nonmixed output accounting")
    offsets = {
        row["workflow_id"]: row["offset_seconds"]
        for row in plan["workflow_arrivals"]["arrival_schedule"]
    }
    origins = [
        row["ts_ms"] - 1000 * offsets[row["workflow_id"]]
        for row in reader(arm / "replay/runtime_events.replay.jsonl")
        if row["kind"] == "workflow_start"
    ]
    if not origins or max(origins) - min(origins) > .00001:
        raise ValueError("arrival origin changed")
    window = tuple(origins[0] + 1000 * value for value in cohort["throughput_window_offset_seconds"])
    receipts = {}
    for row in reader(arm / "replay/native_requests.jsonl"):
        if row["request_id"] in receipts:
            raise ValueError("duplicate receipt")
        receipts[row["request_id"]] = row
    result = analyze(
        reader(arm / "server/runtime_audit.jsonl"),
        buckets=info["cuda_graph_config"]["decode"]["bs"],
        receipts=receipts, measured=set(cohort["measured_workflow_ids"]), window=window,
    )
    expected = status["background_writer_timing"]["categories"]["audit:gpu_service_sample"]["records_seen"]
    if result["service_rows"] != expected:
        raise ValueError("not every native forward was captured")
    for filename, prefix in reader.prefixes.items():
        if Path(filename).stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(Path(filename)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed evidence changed")
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        common_window_offset_seconds=cohort["throughput_window_offset_seconds"],
        engine={
            key: info.get(key) for key in (
                "model_path", "dtype", "kv_cache_dtype", "attention_backend",
                "moe_runner_backend", "max_running_requests", "max_total_tokens",
                "chunked_prefill_size", "enable_mixed_chunk", "cuda_graph_config",
            )
        },
        input_jsonl_prefixes=reader.prefixes,
        inputs_sha256={
            str(path.resolve()): sha256_file(path)
            for path in (registration, cohort_path, info_path, status_path, Path(__file__))
        },
        semantics=[
            "Useful output means actual observed emitted output token deltas, including first prefill outputs.",
            "Worker time includes GPU work, dependency waits and CPU submission; it is not exclusive CUDA kernel time.",
            "Whole execution includes warmup and right-censored background. Every completed receipt and every measured output is reconciled.",
            "Common-window throughput includes background output. A batch's cost is assigned by completion, while worker coverage is clipped exactly.",
            "Prefill extend tokens are actual executed input, not proved new semantic input; they can include recomputation.",
            "Per-output total cost charges necessary prefill once; prefill zero-output steps are not automatically wasted work.",
            "Configured padding rows are geometry, not proportional GPU waste or attainable speedup.",
            "Outside-worker time includes legitimate external idle and scheduling, not proved removable GPU idle.",
        ],
        GPU_action=False, performance_accepted=False,
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve evidence and use nice19 CPU-only analysis")
    result = inspect(args.registration, args.cohort)
    write(args.output, result)
    print(json.dumps({
        key: result[key] for key in (
            "scopes", "phase_costs", "output_conservation",
            "common_window_worker_coverage_ms", "common_window_outside_worker_intervals_ms",
        )
    }))


if __name__ == "__main__":
    main()
