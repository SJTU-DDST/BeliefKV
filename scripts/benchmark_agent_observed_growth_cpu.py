#!/usr/bin/env python3
"""Measure bounded observed-token accounting on real agent-policy CPU fixtures."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.agent_working_set import AgentAdmission
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey


def distribution(values):
    ordered = sorted(values)
    return {
        "samples": len(values),
        "mean_ms": statistics.mean(values),
        "p50_ms": statistics.median(values),
        "p90_ms": ordered[(len(ordered) * 9 - 1) // 10],
        "maximum_ms": ordered[-1],
    }


def measure(size, iterations):
    runtime = NS(visible={}, _join_priority_pending=set())
    adapter = AgentAdmission(runtime, strategy="thunderagent_retained")
    adapter.attach(NS(
        tree_components=(0,),
        token_to_kv_pool_allocator=NS(size_full=size * 100000),
    ))
    try:
        for index in range(size):
            identity = str(index)
            key = PrefillCandidateKey(identity, "wf", identity, identity, 0, 0)
            req = NS(
                rid=identity, origin_input_ids=[0] * 40000, output_ids=[1] * 256,
            )
            runtime.visible[identity] = key
            adapter.registered(req, key)
            adapter.admitted(req)
        checks = adapter.counts["streaming_progress_checks"]
        wall, cpu = [], []
        for iteration in range(iterations + 5):
            wall_start, cpu_start = time.perf_counter_ns(), time.thread_time_ns()
            adapter._refresh_streaming_progress()
            cpu_ms = (time.thread_time_ns() - cpu_start) / 1e6
            wall_ms = (time.perf_counter_ns() - wall_start) / 1e6
            if iteration >= 5:
                wall.append(wall_ms)
                cpu.append(cpu_ms)
        if adapter.counts["streaming_progress_checks"] - checks != (iterations + 5) * size:
            raise ValueError("fixture did not inspect every live consumer")
        if not all(
            item.tokens == 40256 and item.active and item.in_flight
            for item in adapter.policy.agents.values()
        ):
            raise ValueError("observed progress or native admission changed")
        return {
            "live_requests": size,
            "thread_cpu": distribution(cpu),
            "wall": distribution(wall),
            "all_observed_contexts_verified": True,
            "all_existing_running_agents_retained": True,
            "runtime_accounting": adapter.status()["streaming_context_accounting"],
        }
    finally:
        adapter.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--iterations", type=int, default=1000)
    args = parser.parse_args()
    if args.iterations < 1 or args.output.exists():
        parser.error("positive iterations and a fresh output path are required")
    paths = (
        Path(__file__), ROOT / "beliefkv/runtime/agent_working_set.py",
        ROOT / "beliefkv/policy/thunder_agent_migration.py",
        ROOT / "beliefkv/policy/paper_baselines.py",
        ROOT / "beliefkv/runtime/sglang_v0520_admission.py",
    )
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "iterations": args.iterations,
        "warmup_rounds": 5,
        "cases": [measure(size, args.iterations) for size in (1, 24, 128)],
        "source_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths
        },
        "gpu_used": False,
        "performance_accepted": False,
        "scope": (
            "Actual agent adapter and ThunderAgent-derived policy, with CPU request "
            "and native-capacity stand-ins. Measures the refresh callback only, "
            "including counters and observed-context checks; fixture construction "
            "is excluded. No GPU execution, whole-controller overhead or JCT gain "
            "is inferred. Runtime refreshes at five-second maintenance points "
            "and genuine new-agent arrivals, never every decode step."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result["cases"], indent=2))


if __name__ == "__main__":
    main()
