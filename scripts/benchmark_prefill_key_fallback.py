#!/usr/bin/env python3
"""Retain distinct-key and foreign-type controls for immutable key equality."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
from unittest.mock import patch

from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from scripts.benchmark_native_shared_path import distribution
from scripts.benchmark_prefill_key_equality import ROOT, baseline_equality


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations <= 0 or args.output.exists():
        raise ValueError("positive iterations and a fresh report path required")
    baseline, baseline_sources = baseline_equality(args.baseline_revision)
    implementations = {"baseline": baseline, "candidate": PrefillCandidateKey.__eq__}
    key = PrefillCandidateKey(
        "request-156", "workflow-156", "invocation-156", "context-156", 2, 3,
        "session-156", 4,
    )

    class OtherKey(PrefillCandidateKey):
        pass

    scenarios = {
        "identical_object": (key, key, True),
        "equal_distinct_objects": (key, replace(key), True),
        "equal_deserialized_fields": (
            key, PrefillCandidateKey(**json.loads(json.dumps(asdict(key)))), True,
        ),
        "changed_session_generation": (key, replace(key, session_generation=5), False),
        "foreign_tuple": (key, tuple(asdict(key).values()), False),
        "foreign_subclass": (key, OtherKey(**asdict(key)), False),
    }
    calls = 1000
    cases = []
    for kind, (left, right, expected) in scenarios.items():
        samples = {name: [] for name in implementations}
        for iteration in range(args.iterations + 20):
            names = list(implementations)
            if iteration % 2:
                names.reverse()
            for name in names:
                with patch.object(PrefillCandidateKey, "__eq__", implementations[name]):
                    started = time.thread_time_ns()
                    result = sum(left == right for _ in range(calls))
                    elapsed = (time.thread_time_ns() - started) / calls
                if result != calls * expected:
                    raise AssertionError(f"equality contract changed: {kind}")
                if iteration >= 20:
                    samples[name].append(elapsed / 1_000_000.)
        costs = {name: distribution(values) for name, values in samples.items()}
        cases.append({
            "kind": kind, "thread_cpu_ms_per_comparison": costs,
            "expected_equal": expected, "all_results_equal": True,
            "thread_cpu_mean_reduction_fraction": 1. - (
                costs["candidate"]["mean_ms"] / costs["baseline"]["mean_ms"]
            ),
        })
    paths = (
        "beliefkv/runtime/sglang_v0520_admission.py",
        "scripts/benchmark_prefill_key_equality.py",
        "scripts/benchmark_prefill_key_fallback.py",
    )
    report = {
        "status": "isolated_equality_controls_not_admission_or_jct_gain",
        "observed_at_local": datetime.now().astimezone().isoformat(),
        "baseline_revision": args.baseline_revision, "baseline_sources": baseline_sources,
        "candidate_sources": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths
        },
        "iterations": args.iterations, "calls_per_sample": calls, "cases": cases,
        "limitations": [
            "These isolated comparisons preserve unfavorable fallback costs; complete planning/selection costs are reported separately.",
            "Scenario frequencies are not measured production proportions; do not extrapolate a JCT saving.",
            "Both variants use the same key class/hash and all original field validation remains outside this equality method.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([
        {key: row[key] for key in ("kind", "thread_cpu_mean_reduction_fraction")}
        for row in cases
    ], indent=2), flush=True)


if __name__ == "__main__":
    main()
