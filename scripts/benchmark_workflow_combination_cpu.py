"""Measure bounded R5 search on declared CPU snapshots, never workload JCT."""

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.train_workflow_work import write


def policy_module(path):
    name = "_measured_workflow_combination_policy"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def snapshots(policy, *, candidates, seed, count):
    rng = random.Random(seed)
    cases = []
    for index in range(count):
        rows = []
        for number in range(candidates):
            sibling = number in (1, 2)
            workflow = "pair" if sibling else str(number)
            context = rng.randrange(8000, 70000)
            rows.append(policy.CombinationCandidate(
                str(number), workflow, context + 100, number % 3, number,
                .7 if sibling else rng.uniform(.25, 1.), workflow,
                2 if sibling else 1, 0,
                ((("shared" if sibling else str(number), 1.), 1024 if sibling else 512),),
                rng.choice((1, 8, 64, 256, 4096)), context,
            ))
        resources = policy.CombinationResources(
            (60000, 120000, 240000)[index % 3],
            65536, 2048, 4, growth_tokens=16,
            active_width=4, active_context_tokens=160000,
            chunked_input=True,
        )
        reference, used = [], 0
        for row in rows:
            if used + row.logical_tokens <= resources.logical_tokens and len(reference) < resources.slots:
                reference.append(row.identity)
                used += row.logical_tokens
        cases.append((rows, tuple(reference), resources))
    return cases


def distribution(values):
    values = sorted(values)
    return {
        "count": len(values), "min": values[0], "p50": values[len(values) // 2],
        "p95": values[min(len(values) - 1, int(len(values) * .95))],
        "p99": values[min(len(values) - 1, int(len(values) * .99))],
        "max": values[-1], "sum": sum(values),
    }


def measure(policy, cases, costs, *, budget_ns, repetitions):
    reasons, wall, cpu, subset_counts, selections, scores = Counter(), [], [], [], [], []
    for _repeat in range(repetitions):
        selected, evaluations = [], []
        for rows, reference, resources in cases:
            start_wall, start_cpu = time.perf_counter_ns(), time.thread_time_ns()
            result = policy.choose_combination(
                rows, reference, resources, costs, budget_ns=budget_ns,
            )
            wall.append(time.perf_counter_ns() - start_wall)
            cpu.append(time.thread_time_ns() - start_cpu)
            reasons[result["reason"]] += 1
            subset_counts.append(result["evaluated_subsets"])
            selected.append(result["selected"])
            evaluations.append(result.get("selected_evaluation"))
        selections.append(selected)
        scores.append(evaluations)
    return {
        "reasons": dict(reasons), "wall_ns": distribution(wall),
        "thread_cpu_ns": distribution(cpu), "evaluated_subsets": distribution(subset_counts),
        "selections": selections, "selected_evaluations": scores,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=ROOT / "beliefkv/policy/workflow_combination.py")
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", type=int, default=100)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if (
        os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output.exists()
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
        or not 1 <= args.cases <= 1000 or not 1 <= args.repetitions <= 10
    ):
        raise ValueError("keep CPU priority, hidden CUDA and bounded distinct benchmark evidence")
    policy = policy_module(args.policy)
    from scripts.run_causal_comparison import load

    costs = policy.CombinationCosts(**load(args.artifact)["admission_cost_profile"]["costs"])
    results = {}
    for width in (6, 8):
        cases = snapshots(policy, candidates=width, seed=31 + width, count=args.cases)
        # Warm imports and allocations separately from the recorded rounds.
        measure(policy, cases[:3], costs, budget_ns=10_000_000_000, repetitions=1)
        results[str(width)] = {
            "cases": [
                {"candidates": [asdict(row) for row in rows],
                 "reference": reference, "resources": asdict(resources)}
                for rows, reference, resources in cases
            ],
            "exhaustive": measure(
                policy, cases, costs, budget_ns=10_000_000_000,
                repetitions=args.repetitions,
            ),
            "bounded": measure(
                policy, cases, costs, budget_ns=500_000,
                repetitions=args.repetitions,
            ),
        }
    write(args.output, {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path) for path in (args.policy, args.artifact, Path(__file__))
        },
        "scope": {
            "synthetic_snapshot_CPU_microbenchmark": True,
            "cost_table_from_completed_control": True,
            "actual_native_metadata_or_GPU_performance_measured": False,
            "same_seed_and_snapshots_required_for_comparison": True,
            "current_GPU_experiment_unchanged": True,
        },
        "GPU_action": False, "performance_accepted": False,
    })
    print({
        width: {name: {
            "wall_p50_us": row[name]["wall_ns"]["p50"] / 1000.,
            "wall_p99_us": row[name]["wall_ns"]["p99"] / 1000.,
            "reasons": row[name]["reasons"],
        } for name in ("exhaustive", "bounded")}
        for width, row in results.items()
    })


if __name__ == "__main__":
    main()
