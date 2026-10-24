#!/usr/bin/env python3
"""Bound selected-queue restore control cost without allocating GPU tensors."""

import argparse
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
from tests.test_selected_queue_full_donor import selected_fixture


SCENARIOS = {
    "no_victim": (1, 0),
    "incomplete_prefix": (65, 128),
    "complete_reclaim": (1, 512),
    "free_checkpoint": (577, 0),
}


def measure(scenario, policy):
    initial, freed = SCENARIOS[scenario]
    runtime, _key, request, cache, _reads, actions, _records, _reset, _free = (
        cold_frontier_fixture(free_tokens=initial, reclaimed_tokens=freed)
    )
    runtime.frontier_cold_reclaim_policy = policy
    try:
        wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
        runtime.dispatch_frontier_restore([request], running_batch=NS(reqs=[]))
        cpu_us = (time.thread_time_ns() - cpu) / 1000.
        wall_us = (time.perf_counter_ns() - wall) / 1000.
        expected = scenario in ("complete_reclaim", "free_checkpoint") or (
            policy == "waiting" and scenario == "incomplete_prefix"
        )
        if bool(actions) != expected or runtime.physical_disabled or runtime._execution_handoff:
            raise AssertionError((scenario, policy, actions))
        return cpu_us, wall_us, {
            "commands": runtime.physical_ledger.pending_count,
            "bytes": runtime.physical_ledger.pending_action_bytes("PREFETCH_GPU"),
            "native_reclaims": cache.reclaim_beliefkv_handoff_capacity.call_count,
            "native_splits": cache.split_beliefkv_prefetch_full_prefix.call_count,
        }
    finally:
        runtime.close()


def measure_scope(policy):
    runtime, key, donor, _selected, _request, cache = selected_fixture()
    runtime.frontier_cold_reclaim_policy = policy
    owners = ((donor.session_id, donor.session_generation),)
    try:
        wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
        with runtime._queued_full_donor_scope(key) as count:
            accepted = cache.beliefkv_cold_full_owner_validator(owners)
        cpu_us = (time.thread_time_ns() - cpu) / 1000.
        wall_us = (time.perf_counter_ns() - wall) / 1000.
        if accepted != (policy == "selected_queue") or count != int(accepted):
            raise AssertionError("scoped native owner permission mismatch")
        return cpu_us, wall_us, {"queued_candidates": count, "owner_accepted": accepted}
    finally:
        runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=101)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists():
        parser.error("positive samples and a new output are required")
    sources = {
        path: sha256_file(ROOT / path) for path in (
            "beliefkv/runtime/sglang_v0520_runtime.py",
            "beliefkv/runtime/sglang_v0520_physical.py",
            "tests/test_frontier_cold_reclaim.py",
            "tests/test_selected_queue_full_donor.py",
            "scripts/benchmark_selected_queue_frontier_cpu.py",
        )
    }
    cases = []
    for scenario in (*SCENARIOS, "scoped_owner"):
        samples = {
            policy: {"cpu": [], "wall": [], "signature": None}
            for policy in ("waiting", "selected_queue")
        }
        for index in range(args.samples + 10):
            for policy in (
                ("waiting", "selected_queue") if index % 2 == 0
                else ("selected_queue", "waiting")
            ):
                cpu, wall, signature = (
                    measure_scope(policy) if scenario == "scoped_owner"
                    else measure(scenario, policy)
                )
                record = samples[policy]
                if record["signature"] is not None and record["signature"] != signature:
                    raise AssertionError("sample payload changed")
                record["signature"] = signature
                if index >= 10:
                    record["cpu"].append(cpu)
                    record["wall"].append(wall)
        for policy, record in samples.items():
            cases.append({
                "scenario": scenario, "policy": policy,
                "signature": record["signature"],
                "cpu": distribution(record["cpu"]), "wall": distribution(record["wall"]),
                "cpu_samples_us": record["cpu"], "wall_samples_us": record["wall"],
            })
    if any(sha256_file(ROOT / path) != digest for path, digest in sources.items()):
        raise RuntimeError("sources changed while benchmarking")
    result = {
        "scope": (
            "Scalar native allocator/reclaim/DMA stand-ins with actual control, "
            "native prefix plans and receipt tracking; fixture setup/cleanup "
            "excluded. Different policies can perform different work. These "
            "measurements do not establish GPU overlap, saved JCT or whole-run overhead."
        ),
        "source_sha256": sources, "cases": cases,
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({
        "output": str(args.output), "cases": [
            {
                "scenario": case["scenario"], "policy": case["policy"],
                "cpu_p50_us": case["cpu"]["p50_us"], "cpu_p99_us": case["cpu"]["p99_us"],
                "signature": case["signature"],
            } for case in cases
        ],
    }))


if __name__ == "__main__":
    main()
