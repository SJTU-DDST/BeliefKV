#!/usr/bin/env python3
"""Measure synthetic agent-control CPU components without claiming JCT gains."""

import argparse
import json
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.policy.agent_working_set import AgentWorkingSet
from beliefkv.runtime.agent_working_set import AgentAdmission
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey


def measure(operation, samples):
    observations = []
    for _ in range(samples):
        start = time.thread_time_ns()
        operation()
        observations.append((time.thread_time_ns() - start) / 1000.)
    return {
        "samples": samples, "unit": "thread_cpu_us",
        "mean": statistics.mean(observations), "p50": statistics.median(observations),
        "p99": sorted(observations)[min(samples - 1, int(samples * .99))],
    }


def benchmark(population, samples):
    runtime = NS(_join_priority_pending={}, _long_tool_wait=lambda _key: False)
    admission = AgentAdmission(runtime)
    admission.policy = AgentWorkingSet(805198)
    keys = []
    for index in range(population):
        key = PrefillCandidateKey(str(index), "w", str(index), str(index), 0, 0)
        admission.policy.arrive(key.invocation_id, 65536, now=0.)
        admission.requests[key.request_id] = key
        keys.append(key)
    for item in admission.policy.agents.values():
        if item.active:
            admission.policy.begin(item.identity)
    admission.next_tick = float("inf")
    sequence = 20.

    def maintenance():
        nonlocal sequence
        sequence += 1.
        admission.next_tick = sequence
        admission.tick(now=sequence)

    result = {
        "population": population,
        "one_cached_tick": measure(lambda: admission.tick(now=1.), samples),
        "queue_gate_all_candidates": measure(lambda: [admission.allows(key) for key in keys], samples),
        "one_due_maintenance": measure(maintenance, samples),
    }
    result["active_agents"] = sum(item.active for item in admission.policy.agents.values())
    if admission.policy.used_tokens > admission.policy.capacity:
        raise ValueError("fixture working set exceeds logical capacity")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not 10 <= args.samples <= 100000:
        raise ValueError("choose fresh evidence and a bounded sample count")
    result = {
        "cases": [benchmark(count, args.samples) for count in (18, 36, 128)],
        "scope": "Synthetic Python thread CPU components; excludes native sorting, DMA, GPU and end-to-end JCT.",
        "capacity_tokens": 805198, "context_tokens_per_agent": 65536,
        "gpu_used": False, "performance_accepted": False,
    }
    with args.output.open("x") as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
