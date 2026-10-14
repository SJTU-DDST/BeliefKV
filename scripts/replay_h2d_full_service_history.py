#!/usr/bin/env python3
"""Check runtime estimates against sealed causal-history evaluation results."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.native_h2d_seed import load_h2d_seed
from beliefkv.runtime.native_transfer_service import load_native_service_seed
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.evaluate_transfer_service_history import read_prefix, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("preserve prior replay evidence")
    evaluation_bytes = args.evaluation.read_bytes()
    evaluation = json.loads(evaluation_bytes)
    detailed_path = Path(evaluation["detailed_predictions"])
    detailed_bytes = detailed_path.read_bytes()
    if hashlib.sha256(detailed_bytes).hexdigest() != evaluation["detailed_predictions_sha256"]:
        raise ValueError("evaluation predictions changed")
    detailed = json.loads(gzip.decompress(detailed_bytes))
    sealed_bytes = Path(evaluation["sealed_report"]).read_bytes()
    if hashlib.sha256(sealed_bytes).hexdigest() != evaluation["sealed_report_sha256"]:
        raise ValueError("sealed source changed")
    sealed = json.loads(gzip.decompress(sealed_bytes))
    plan = json.loads((Path(sealed["arm"]).parent / "ab_plan.json").read_bytes())
    if plan["code_commit"] != evaluation["collection_code_commit"]:
        raise ValueError("collection revision changed")
    baseline, baseline_sources = baseline_runtime(plan["code_commit"])
    runtimes = {"mixed_fifo": baseline(), "mixed_fifo_plus_recent_full": NativeAdmissionRuntime()}
    rich_seed = load_native_service_seed(
        plan["transfer_service_seed"], plan["transfer_service_seed_sha256"],
    )
    legacy_seed = load_h2d_seed(plan["h2d_seed_artifact"], plan["h2d_seed_sha256"])
    for runtime in runtimes.values():
        if runtime._native_service_samples or runtime._h2d_samples:
            raise ValueError("unexpected external seed environment")
        runtime._native_service_samples.extend(rich_seed)
        runtime._h2d_samples.extend(legacy_seed)
    source = Path(evaluation["transfer_source"]["path"])
    by_command = {
        row["command_id"]: row for row in read_prefix(source, evaluation["transfer_source"])
        if row.get("command_id")
    }
    transfers = detailed["transfer_predictions"]
    completed = sorted(transfers, key=lambda row: by_command[row["command_id"]]["complete_ts_ms"])
    queries = sorted(
        [(row["timestamp_ms"], "transfer", row) for row in transfers]
        + [(row["timestamp_ms"], "service_rejection", row)
           for row in detailed["service_rejection_queries"]],
        key=lambda item: item[0],
    )
    counts, index, cpu_us = Counter(), 0, {name: [] for name in runtimes}
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic") as clock:
            for timestamp, kind, row in queries:
                while index < len(completed):
                    previous = by_command[completed[index]["command_id"]]
                    if previous["complete_ts_ms"] >= timestamp:
                        break
                    clock.return_value = previous["complete_ts_ms"] / 1000.
                    receipt = NS(
                        direction=previous["direction"], status=previous["status"],
                        node_ids=(), child_commits=(), actual_bytes=previous["actual_bytes"],
                        submit_to_ack_ms=previous["submit_to_ack_ms"],
                        enqueue_to_submit_ms=previous.get("enqueue_to_submit_ms"),
                        num_tokens_by_pool=tuple(previous["num_tokens_by_pool"].items()),
                    )
                    for runtime in runtimes.values():
                        runtime.on_native_transfer_commit(receipt)
                    index += 1
                clock.return_value = timestamp / 1000.
                estimates = {}
                variants = list(runtimes)
                if counts["queries"] % 2:
                    variants.reverse()
                for name in variants:
                    runtime = runtimes[name]
                    method = getattr(runtime, f"_estimate_{row['direction']}_service")
                    started = time.thread_time_ns()
                    estimate = method(row["size_bytes"], shape=row["shape"])
                    cpu_us[name].append((time.thread_time_ns() - started) / 1000.)
                    expected = row["predictions"][name]
                    if (estimate is None) != (expected is None):
                        raise AssertionError(f"support mismatch: {kind}, {row}, {name}")
                    if estimate is not None and (
                        abs(estimate.submit_to_ack_p50_ms - expected["p50_ms"]) > 1e-8
                        or abs(estimate.submit_to_ack_p90_ms - expected["p90_ms"]) > 1e-8
                        or estimate.sample_count != expected["sample_count"]
                    ):
                        raise AssertionError(f"estimate mismatch: {kind}, {row}, {name}")
                    estimates[name] = estimate
                    if kind == "service_rejection":
                        counts[f"rejection_supported:{name}"] += estimate is not None
                base = estimates["mixed_fifo"]
                candidate = estimates["mixed_fifo_plus_recent_full"]
                if base is not None:
                    if asdict(base) != asdict(candidate):
                        raise AssertionError("previously supported runtime estimate changed")
                    counts["supported_estimates_identical"] += 1
                counts["queries"] += 1
                counts[kind] += 1
        for runtime in runtimes.values():
            if runtime.completed_physical_actions:
                raise AssertionError("a service replay produced physical actions")
        report = {
            "status": "exact_runtime_receipt_timing_replay_not_live_callback_order_or_policy_gain",
            "counts": dict(counts), "receipts_fed_per_runtime": index,
            "baseline_revision": plan["code_commit"], "baseline_sources": baseline_sources,
            "candidate_runtime_sha256": hashlib.sha256(
                (ROOT / "beliefkv/runtime/sglang_v0520_runtime.py").read_bytes(),
            ).hexdigest(),
            "evaluation": str(args.evaluation.resolve()),
            "evaluation_sha256": hashlib.sha256(evaluation_bytes).hexdigest(),
            "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "uncontrolled_runtime_prediction_thread_cpu_us": {
                name: summary(values) for name, values in cpu_us.items()
            },
            "candidate_fallback_counts": {
                key: value for key, value in runtimes["mixed_fifo_plus_recent_full"].counts.items()
                if key.startswith("h2d_full_service")
            },
            "maximum_supplement_samples": runtimes[
                "mixed_fifo_plus_recent_full"
            ]._h2d_full_service_samples.maxlen,
            "limitations": [
                "Completed receipts are fed at their recorded completion time, strictly before queries.",
                "Local monotonic time is simulated from receipt wall time; actual callback/cache order is unknown.",
                "Real runtime estimators, receipt validation and cache invalidation are exercised without a GPU.",
                "Rejection queries have no executed-transfer labels; new support does not establish action benefit.",
                "Previously supported estimates must remain exactly equal, including sample count/support/queue estimate.",
                "CPU includes the offline patched clock and is unpinned; it is not scheduler or end-to-end speedup.",
            ],
        }
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        print(json.dumps({
            "counts": report["counts"], "receipts_fed_per_runtime": index,
            "fallback": report["candidate_fallback_counts"], "output": str(args.output),
        }), flush=True)
    finally:
        for runtime in runtimes.values():
            runtime.close()


if __name__ == "__main__":
    main()
