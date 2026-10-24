#!/usr/bin/env python3
"""Compare same-work frontier control before and after redundant capture removal."""

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
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.benchmark_prefetch_control_cpu import distribution
from tests.test_frontier_cold_reclaim import cold_frontier_fixture


def sample(cls, scenario):
    runtime, _key, request, cache, _reads, actions, _records, _reset, _free = (
        cold_frontier_fixture(
            free_tokens=65 if scenario == "free_partial" else 1,
            reclaimed_tokens=128 if scenario == "cold_reclaimed" else 0,
            runtime_class=cls,
        )
    )
    runtime._borrowable_parent_headroom = True
    runtime._opportunity_writer = NS(record=lambda _row: None, close=lambda: None)
    refreshes = [0]
    original = runtime._wait_prefetch_opportunity

    def refresh(*args, **kwargs):
        refreshes[0] += 1
        return original(*args, **kwargs)

    runtime._wait_prefetch_opportunity = refresh
    batch = NS(reqs=[])
    try:
        wall_start, cpu_start = time.perf_counter_ns(), time.thread_time_ns()
        runtime.dispatch_frontier_restore([request], running_batch=batch)
        cpu = (time.thread_time_ns() - cpu_start) / 1000.
        wall = (time.perf_counter_ns() - wall_start) / 1000.
        signature = {
            "issued_nodes": len(actions),
            "pending_commands": runtime.physical_ledger.pending_count,
            "pending_bytes": runtime.physical_ledger.pending_action_bytes("PREFETCH_GPU"),
            "cold_reclaims": cache.reclaim_beliefkv_handoff_capacity.call_count,
            "native_splits": cache.split_beliefkv_prefetch_full_prefix.call_count,
            "physical_disabled": runtime.physical_disabled,
        }
        if (
            signature["issued_nodes"] != int(scenario != "cold_no_victim")
            or signature["pending_commands"] != signature["issued_nodes"]
            or signature["cold_reclaims"] != int(scenario != "free_partial")
            or signature["native_splits"] != int(scenario == "free_partial")
            or signature["physical_disabled"]
        ):
            raise AssertionError((scenario, signature))
        return cpu, wall, signature, refreshes[0]
    finally:
        runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="f3d56ec4")
    parser.add_argument("--samples", type=int, default=101)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists() or not gc.isenabled():
        parser.error("positive samples, normal GC and a new output are required")
    baseline, pinned = baseline_runtime(args.baseline)
    sources = {
        path: sha256_file(ROOT / path) for path in (
            "beliefkv/runtime/sglang_v0520_runtime.py",
            "beliefkv/runtime/sglang_v0520_physical.py",
            "scripts/benchmark_frontier_recapture_cpu.py",
            "scripts/benchmark_native_prepare_path.py",
            "scripts/benchmark_wait_prefetch_dispatch_cpu.py",
            "tests/test_frontier_cold_reclaim.py",
        )
    }
    cases = []
    for scenario in ("free_partial", "cold_no_victim", "cold_reclaimed"):
        rows = {
            name: {"cpu": [], "wall": [], "signature": None, "refreshes": None}
            for name in ("baseline", "candidate")
        }
        for index in range(args.samples + 10):
            classes = [("baseline", baseline), ("candidate", NativeAdmissionRuntime)]
            if index % 2:
                classes.reverse()
            for name, cls in classes:
                cpu, wall, signature, refreshes = sample(cls, scenario)
                row = rows[name]
                if row["signature"] is not None and (
                    signature != row["signature"] or refreshes != row["refreshes"]
                ):
                    raise AssertionError("Control fixture changed across samples.")
                row["signature"], row["refreshes"] = signature, refreshes
                if index >= 10:
                    row["cpu"].append(cpu)
                    row["wall"].append(wall)
        if rows["baseline"]["signature"] != rows["candidate"]["signature"]:
            raise AssertionError("Optimization changed the native work.")
        if scenario == "free_partial" and (
            rows["baseline"]["refreshes"], rows["candidate"]["refreshes"]
        ) != (2, 1):
            raise AssertionError("Expected duplicate capture was not removed.")
        cases.append({
            "scenario": scenario,
            "variants": {
                name: {
                    "cpu": distribution(row["cpu"]), "wall": distribution(row["wall"]),
                    "cpu_samples_us": row["cpu"], "wall_samples_us": row["wall"],
                    "control_signature": row["signature"],
                    "post_selection_refreshes": row["refreshes"],
                } for name, row in rows.items()
            },
        })
    if any(sha256_file(ROOT / path) != digest for path, digest in sources.items()):
        raise RuntimeError("Benchmark sources changed during measurement.")
    result = {
        "scope": (
            "Alternating independent fixtures with identical native payloads. "
            "Actual Python control and receipt ledger; scalar native allocator, "
            "split, reclaim and DMA stand-ins. Not full-run overhead or JCT saving."
        ),
        "baseline_revision": args.baseline, "baseline_source_sha256": pinned,
        "candidate_source_sha256": sources,
        "normal_gc": True, "retains_all_samples": True,
        "setup_and_teardown_outside_measurement": True, "cases": cases,
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "cases": [
            {"scenario": row["scenario"],
             "cpu_p50_us": {name: value["cpu"]["p50_us"] for name, value in row["variants"].items()}}
            for row in cases
        ],
    }))


if __name__ == "__main__":
    main()
