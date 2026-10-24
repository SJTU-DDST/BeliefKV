#!/usr/bin/env python3
"""Measure bounded ordinary restore control with scalar native transfer stand-ins."""

import argparse
import gc
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.benchmark_prefetch_control_cpu import distribution
from tests.test_frontier_cold_reclaim import cold_frontier_fixture


SCENARIOS = ("no_backed_copy", "probe_backoff", "no_cold_victim", "reclaimed_prefix", "free_partial")


def measure(scenario, enabled):
    initial = 65 if scenario == "free_partial" else 1
    runtime, _key, request, cache, _reads, actions, _records, _reset, _free = (
        cold_frontier_fixture(
            free_tokens=initial,
            reclaimed_tokens=128 if scenario == "reclaimed_prefix" else 0,
        )
    )
    runtime.enable_frontier_cold_reclaim = enabled
    if scenario == "no_backed_copy":
        cache.tree_core.full_host_duplicates.clear()
    elif scenario == "probe_backoff":
        runtime._frontier_reclaim_after_ms = float("inf")
    batch = NS(reqs=[])
    try:
        wall_start, cpu_start = time.perf_counter_ns(), time.thread_time_ns()
        runtime.dispatch_frontier_restore([request], running_batch=batch)
        cpu_us = (time.thread_time_ns() - cpu_start) / 1000.
        wall_us = (time.perf_counter_ns() - wall_start) / 1000.
        issued = enabled and scenario in ("reclaimed_prefix", "free_partial")
        reclaim = enabled and scenario in ("no_cold_victim", "reclaimed_prefix")
        split = enabled and scenario == "free_partial"
        expected_tokens = 128 if scenario == "reclaimed_prefix" else 64
        signature = {
            "commands": runtime.physical_ledger.pending_count,
            "bytes": runtime.physical_ledger.pending_action_bytes("PREFETCH_GPU"),
            "native_inspections": cache.inspect_beliefkv_reentry.call_count,
            "native_reclaims": cache.reclaim_beliefkv_handoff_capacity.call_count,
            "native_splits": cache.split_beliefkv_prefetch_full_prefix.call_count,
        }
        if (
            signature["commands"] != int(issued) or len(actions) != int(issued)
            or signature["native_reclaims"] != int(reclaim)
            or signature["native_splits"] != int(split)
            or signature["bytes"] != (
                expected_tokens * cache.host_pool_group.entry_map["kv"].host_pool.size_per_token
                if issued else 0
            )
            or runtime.physical_disabled or runtime._execution_handoff is not None
        ):
            raise AssertionError((scenario, enabled, signature))
        if scenario in ("no_backed_copy", "probe_backoff") and signature["native_inspections"]:
            raise AssertionError("negative capacity gate inspected a request")
        return cpu_us, wall_us, signature
    finally:
        runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=101)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists() or not gc.isenabled():
        parser.error("positive samples, normal GC and a new output are required")
    sources = {
        name: sha256_file(ROOT / name) for name in (
            "beliefkv/runtime/sglang_v0520_runtime.py",
            "beliefkv/runtime/sglang_v0520_physical.py",
            "scripts/benchmark_frontier_cold_reclaim_cpu.py",
            "scripts/benchmark_wait_prefetch_dispatch_cpu.py",
            "tests/test_frontier_cold_reclaim.py",
        )
    }
    cases = []
    for scenario in SCENARIOS:
        samples = {enabled: {"cpu": [], "wall": [], "signature": None} for enabled in (False, True)}
        for index in range(args.samples + 10):
            for enabled in ((False, True) if index % 2 == 0 else (True, False)):
                cpu, wall, signature = measure(scenario, enabled)
                record = samples[enabled]
                if record["signature"] is not None and record["signature"] != signature:
                    raise AssertionError("fixture payload changed across samples")
                record["signature"] = signature
                if index >= 10:
                    record["cpu"].append(cpu)
                    record["wall"].append(wall)
        for enabled, record in samples.items():
            cases.append({
                "scenario": scenario, "cold_reclaim": enabled,
                "control_payload_signature": record["signature"],
                "cpu": distribution(record["cpu"]), "wall": distribution(record["wall"]),
                "cpu_samples_us": record["cpu"], "wall_samples_us": record["wall"],
            })
    if any(sha256_file(ROOT / name) != digest for name, digest in sources.items()):
        raise RuntimeError("benchmark sources changed during measurement")
    result = {
        "scope": (
            "Actual control, session observation, prefix planning, optional split, "
            "native enqueue callbacks, receipt ledger and issuance tracking. "
            "Native allocator/reclaim/DMA are scalar stand-ins; no measured GPU "
            "overlap, whole-run controller overhead or JCT saving is claimed."
        ),
        "comparison": (
            "Alternate independent fresh enabled/disabled fixtures. Enabling cold "
            "capacity intentionally changes payload; costs are not speedup estimates."
        ),
        "fixture_setup_and_teardown_outside_timing": True,
        "normal_gc": True, "retains_all_samples": True,
        "source_sha256": sources, "cases": cases,
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "cases": [
            {"scenario": row["scenario"], "enabled": row["cold_reclaim"],
             "cpu_p50_us": row["cpu"]["p50_us"], "cpu_p99_us": row["cpu"]["p99_us"]}
            for row in cases
        ],
    }))


if __name__ == "__main__":
    main()
