#!/usr/bin/env python3
"""Compare service caches on sealed wait-plan queries and earlier native ACKs."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.native_h2d_seed import load_h2d_seed
from beliefkv.runtime.native_transfer_service import load_native_service_seed, pool_shape
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.evaluate_transfer_service_history import read_prefix, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--sealed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("preserve prior replay evidence")
    sealed_bytes = args.sealed.read_bytes()
    sealed = json.loads(gzip.decompress(sealed_bytes))
    arm = Path(sealed["arm"])
    plan_bytes = (arm.parent / "ab_plan.json").read_bytes()
    plan = json.loads(plan_bytes)
    prefixes = sealed["input_jsonl_prefixes"]
    opportunity_path = arm / "opportunities/admission_opportunities.jsonl"
    transfer_path = arm / "server/transfer_telemetry.jsonl"
    units = {"kv": 20480, "mamba": 64389120}
    queries = []
    skipped = Counter()
    for row in read_prefix(opportunity_path, prefixes[str(opportunity_path)]):
        if row.get("event") != "wait_prefetch_plan":
            continue
        amounts = row.get("planned_pool_bytes") or {}
        size = sum(amounts.values())
        timestamp = row.get("ts_ms")
        if (
            size <= 0 or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp)
        ):
            skipped["empty_plan_or_invalid_time"] += 1
            continue
        if set(amounts) - units.keys() or any(
            type(amount) is not int or amount < 0 or amount % units[name]
            for name, amount in amounts.items()
        ):
            raise ValueError("recorded wait plan has nonintegral native pool units")
        queries.append((
            timestamp, size,
            pool_shape(amounts.get("kv", 0), amounts.get("mamba", 0)),
            row.get("source", "unknown"), row.get("reason", "unknown"),
        ))
    queries.sort(key=lambda item: item[0])
    receipts = []
    for row in read_prefix(transfer_path, prefixes[str(transfer_path)]):
        size, delay = row.get("actual_bytes"), row.get("submit_to_ack_ms")
        complete = row.get("complete_ts_ms")
        if (
            row.get("status") == "completed"
            and row.get("direction") in ("h2d", "d2h")
            and type(size) is int and size > 0
            and type(delay) in (int, float) and math.isfinite(delay)
            and 0 < delay <= 60_000
            and type(complete) in (int, float) and math.isfinite(complete)
        ):
            receipts.append((
                complete, NS(
                    status="completed", direction=row["direction"],
                    node_ids=(), child_commits=(), actual_bytes=size,
                    submit_to_ack_ms=delay,
                    enqueue_to_submit_ms=row.get("enqueue_to_submit_ms"),
                    num_tokens_by_pool=tuple((row.get("num_tokens_by_pool") or {}).items()),
                ),
            ))
    receipts.sort(key=lambda item: item[0])
    baseline, baseline_sources = baseline_runtime(args.baseline_revision)
    runtimes = {"baseline": baseline(), "candidate": NativeAdmissionRuntime()}
    rich = load_native_service_seed(
        plan["transfer_service_seed"], plan["transfer_service_seed_sha256"],
    )
    legacy = load_h2d_seed(plan["h2d_seed_artifact"], plan["h2d_seed_sha256"])
    for runtime in runtimes.values():
        if runtime._native_service_samples or runtime._h2d_samples:
            raise ValueError("unexpected environment-provided service evidence")
        runtime._native_service_samples.extend(rich)
        runtime._h2d_samples.extend(legacy)
    query_cpu = {name: [] for name in runtimes}
    callback_cpu_ns = Counter()
    clock = [0.]
    index, counts, receipt_directions = 0, Counter(), Counter()
    started = datetime.now().astimezone().isoformat()
    try:
        with patch("time.monotonic", lambda: clock[0]):
            for query_index, (timestamp, size, shape, source, reason) in enumerate(queries):
                while index < len(receipts) and receipts[index][0] < timestamp:
                    complete, commit = receipts[index]
                    clock[0] = complete / 1000.
                    order = list(runtimes)
                    if index % 2:
                        order.reverse()
                    for name in order:
                        begin = time.thread_time_ns()
                        runtimes[name].on_native_transfer_commit(commit)
                        callback_cpu_ns[name] += time.thread_time_ns() - begin
                    receipt_directions[commit.direction] += 1
                    index += 1
                clock[0] = timestamp / 1000.
                estimates = {}
                order = list(runtimes)
                if query_index % 2:
                    order.reverse()
                for name in order:
                    begin = time.thread_time_ns()
                    estimates[name] = runtimes[name]._estimate_h2d_service(size, shape=shape)
                    query_cpu[name].append((time.thread_time_ns() - begin) / 1000.)
                if estimates["baseline"] != estimates["candidate"]:
                    raise AssertionError(
                        f"estimate changed at {timestamp}: {size}/{shape}/{source}/{reason}"
                    )
                counts["identical_queries"] += 1
                counts[f"source:{source}"] += 1
                counts[f"reason:{reason}"] += 1
                counts["supported" if estimates["candidate"] is not None else "unsupported"] += 1
        if any(runtime.completed_physical_actions for runtime in runtimes.values()):
            raise AssertionError("service cache replay produced a physical action")
        if any(
            tuple(getattr(runtimes["baseline"], attr))
            != tuple(getattr(runtimes["candidate"], attr))
            for attr in ("_native_service_samples", "_h2d_samples", "_h2d_full_service_samples")
        ):
            raise AssertionError("service evidence changed")
        cache_counts = {
            name: {
                key: value for key, value in runtime.counts.items()
                if "service_estimate_" in key or "service_cache_retained_" in key
            } for name, runtime in runtimes.items()
        }
        cpu = {
            name: {
                "query_us": summary(values),
                "query_total_ms": sum(values) / 1000.,
                "callback_total_ms": callback_cpu_ns[name] / 1_000_000.,
                "combined_total_ms": (
                    sum(values) / 1000. + callback_cpu_ns[name] / 1_000_000.
                ),
            } for name, values in query_cpu.items()
        }
        paths = (
            "beliefkv/runtime/sglang_v0520_runtime.py",
            "beliefkv/runtime/native_transfer_service.py",
            "scripts/replay_native_service_cache.py",
            "tests/test_native_service_cache_invalidation.py",
        )
        report = {
            "status": "sealed_service_cache_equivalence_not_live_action_or_gpu_gain",
            "started_at_local": started,
            "finished_at_local": datetime.now().astimezone().isoformat(),
            "baseline_revision": args.baseline_revision,
            "baseline_sources": baseline_sources,
            "collection_revision": plan["code_commit"],
            "sealed_report": str(args.sealed.resolve()),
            "sealed_report_sha256": hashlib.sha256(sealed_bytes).hexdigest(),
            "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
            "source_prefixes": {
                str(path): prefixes[str(path)] for path in (opportunity_path, transfer_path)
            },
            "candidate_sources": {
                path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths
            },
            "queries": len(queries), "counts": dict(counts), "skipped": dict(skipped),
            "receipts_fed_per_runtime": index, "receipt_directions": dict(receipt_directions),
            "service_evidence_identical": True, "cache_counts": cache_counts,
            "thread_cpu": cpu,
            "limitations": [
                "Recorded wait-plan sizes are known at observation; query timestamps approximate estimator call time.",
                "Only completed receipts strictly before queries are fed; actual scheduler callback order is unknown.",
                "Exactly the same histories, estimator, output support and queue quantiles are compared.",
                "Diagnostic plan rows are not an exhaustive log of actual estimator calls or their workload frequency.",
                "Alternating unpinned CPU timing includes the replay clock and concurrent server interference.",
                "No physical allocation, prefetch execution, GPU throughput, JCT or quality gain is inferred.",
            ],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        print(json.dumps({
            "queries": len(queries), "receipts": index, "outputs_equal": True,
            "cache_counts": cache_counts, "thread_cpu": cpu,
            "output": str(args.output),
        }), flush=True)
    finally:
        for runtime in runtimes.values():
            runtime.close()


if __name__ == "__main__":
    main()
