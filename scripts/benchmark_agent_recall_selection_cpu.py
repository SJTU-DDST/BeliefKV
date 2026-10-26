#!/usr/bin/env python3
"""Measure actual logical recall selection, excluding fixture setup and GPU work."""

import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import mean
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.thunder_agent_migration import MigratingProgram, ThunderAgentMigration


def fixture(width, selection, case, observed):
    borrowers = {
        str(index): MigratingProgram(
            str(index), 1000 + index * 137, active=True, steps=2,
            pending=index % 3 != 0, acting=index % 3 == 0,
            blocked="join" if index % 3 == 0 else None,
        ) for index in range(width)
    }
    total = sum(item.tokens + 100 for item in borrowers.values())
    tokens = (
        1000 if case == "single" else
        max(item.tokens + 100 for item in borrowers.values()) + 550
        if case == "multiple" else total + 1000
    )
    policy = ThunderAgentMigration(
        total + 100, retain_blocked=True, recall_join_loans=True,
        recall_selection=selection, observe_recall=observed.append,
        recall_urgent=lambda identity: int(identity) % 5 == 1,
    )
    policy.programs = {
        **borrowers, "parent": MigratingProgram("parent", tokens, pending=True, critical=True),
    }
    policy.loan_borrowers = {"parent": set(borrowers)}
    policy.borrowed_from = {identity: {"parent"} for identity in borrowers}
    return policy


def measure(width, selection, case):
    samples, credit = [], Counter()
    for index in range(205):
        observed = []
        policy = fixture(width, selection, case, observed)
        started = time.thread_time_ns()
        admitted = policy._recall_recorded_loan(policy.agents["parent"], now=1., source="join")
        elapsed = time.thread_time_ns() - started
        if admitted is not (case != "insufficient"):
            raise ValueError("recall benchmark did not execute its declared outcome")
        if policy.used_tokens > policy.capacity or any(
            item.in_flight and not item.active for item in policy.agents.values()
        ):
            raise ValueError("recall benchmark violated native logical ownership")
        if admitted and (
            len(observed) != 1
            or observed[0]["selected_credit_tokens"] < observed[0]["needed_tokens"]
        ):
            raise ValueError("recall benchmark lost the actual decision evidence")
        if index >= 5:
            samples.append(elapsed / 1e6)
            if observed:
                credit.update(
                    needed_tokens=observed[0]["needed_tokens"],
                    selected_credit_tokens=observed[0]["selected_credit_tokens"],
                )
    return {
        "agents": width, "selection": selection, "case": case, "samples": len(samples),
        "mean_thread_cpu_ms": mean(samples), "maximum_thread_cpu_ms": max(samples),
        "mean_needed_tokens": credit["needed_tokens"] / len(samples),
        "mean_selected_credit_tokens": credit["selected_credit_tokens"] / len(samples),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous control measurements")
    result = {
        "cases": [
            measure(width, selection, case)
            for width in (1, 4, 16, 64)
            for selection in ("largest", "cold_fit")
            for case in ("single", "multiple", "insufficient")
            if case != "multiple" or width > 1
        ],
        "source_sha256": {
            name: sha256_file(ROOT / name) for name in (
                "scripts/benchmark_agent_recall_selection_cpu.py",
                "beliefkv/policy/thunder_agent_migration.py",
                "beliefkv/policy/paper_baselines.py",
            )
        },
        "scope": "Actual policy recall with decision-dictionary creation and append callback. Fixture setup, native cache operations, serialization/background writer and GPU costs are excluded. No performance benefit is measured.",
        "performance_accepted": False,
    }
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
