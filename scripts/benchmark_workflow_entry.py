#!/usr/bin/env python3
"""Measure incremental logical workflow-entry checks on matched policy states."""

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.thunder_agent_migration import ThunderAgentMigration
from scripts.benchmark_native_shared_path import distribution


def fixture(population, ready, threshold):
    live = ThunderAgentMigration(
        population * 1000, retain_blocked=True, workflow_entry_min_ready=8,
    )
    for index in range(population):
        identity = f"agent-{index}"
        live.arrive(identity, 200, now=index / 1000., workflow_id=f"workflow-{index}")
        item = live.agents[identity]
        item.active = index < ready
        item.in_flight = index < ready
        item.pending, item.acting, item.blocked = True, False, None
        if index < ready:
            live.started_workflows.add(item.workflow_id)
    live.workflow_entry_min_ready = threshold
    live._entry_now = 1.
    return live


def measure(*, population, ready, threshold, operation, iterations):
    timings = {"thread_cpu": [], "wall": []}
    for sample in range(iterations + 5):
        live = fixture(population, ready, threshold)
        target = live.agents[f"agent-{population - 1}"]
        cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
        if operation == "entry_check":
            result = live._entry_allowed(target, 1.)
        else:
            result = live._resume()
        cpu_ms = (time.thread_time_ns() - cpu) / 1e6
        wall_ms = (time.perf_counter_ns() - wall) / 1e6
        if operation == "entry_check":
            if result is not (threshold == 0 or ready < threshold):
                raise ValueError("entry check changed its declared behavior")
        elif threshold and len(result) != int(ready < threshold):
            raise ValueError("resume did not enforce one permitted newcomer")
        if any(not live.agents[f"agent-{index}"].in_flight for index in range(ready)):
            raise ValueError("entry control interrupted running work")
        if sample >= 5:
            timings["thread_cpu"].append(cpu_ms)
            timings["wall"].append(wall_ms)
    return {
        "logical_population": population, "ready_active_agents": ready,
        "workflow_entry_min_ready": threshold, "operation": operation,
        "iterations": iterations,
        "cost_ms": {clock: distribution(values) for clock, values in timings.items()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not 1 <= args.iterations <= 10000:
        parser.error("use a fresh output and 1..10000 iterations")
    report = {
        "source_sha256": {
            name: sha256_file(ROOT / name) for name in (
                "scripts/benchmark_workflow_entry.py",
                "beliefkv/policy/thunder_agent_migration.py",
                "beliefkv/policy/paper_baselines.py",
            )
        },
        "cases": [
            measure(
                population=population, ready=ready, threshold=threshold,
                operation=operation, iterations=args.iterations,
            )
            for population in (18, 64, 128)
            for ready in (0, 4, 8)
            for threshold in (0, 8)
            for operation in ("entry_check", "resume")
        ],
        "scope": [
            "Matched logical policy states; setup is outside timing.",
            "Separate single entry check and ordinary resume pass measurements.",
            "No native cache operations, graph ingress, copies or allocations are measured.",
            "This is not live GPU controller overhead, numerical equivalence, JCT or throughput acceptance.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:
        json.dump(report, output, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps({
        "output": str(args.output), "cases": len(report["cases"]),
        "maximum_case_mean_thread_cpu_ms": max(
            row["cost_ms"]["thread_cpu"]["mean_ms"] for row in report["cases"]
        ),
    }, indent=2))


if __name__ == "__main__":
    main()
