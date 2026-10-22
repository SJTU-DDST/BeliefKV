#!/usr/bin/env python3
"""Alternate complete idle-copy dispatches against the pre-queued-backup source."""

from __future__ import annotations

import argparse
import ast
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from types import MethodType, SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime import sglang_v0520_runtime as module
from tests.test_idle_queued_full_backup import fixture


def distribution(values):
    return {
        "samples": len(values), "p50_us": statistics.median(values),
        "mean_us": statistics.mean(values),
        "p90_us": sorted(values)[int(.9 * (len(values) - 1))],
        "p99_us": sorted(values)[int(.99 * (len(values) - 1))],
        "max_us": max(values),
    }


def reference_methods(revision):
    source = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", f"{revision}:beliefkv/runtime/sglang_v0520_runtime.py"],
        text=True,
    )
    cls = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "NativeAdmissionRuntime"
    )
    names = {"_live_backup_full_owners", "dispatch_idle_prepare"}
    nodes = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in nodes} != names:
        raise ValueError("reference lacks both backup execution methods")
    namespace = dict(vars(module))
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), "<reference>", "exec"), namespace)
    return {name: namespace[name] for name in names}, hashlib.sha256(source.encode()).hexdigest()


def case(size, kind, methods, samples):
    instances = {}
    try:
        for label in ("reference", "candidate"):
            runtime, _, key, request, cache = fixture()
            if label == "reference":
                for name, function in methods.items():
                    setattr(runtime, name, MethodType(function, runtime))
            queue = []
            for index in range(size - 1):
                rid = f"ordinary-{index}"
                queue.append(NS(rid=rid))
                runtime.visible[rid] = replace(key, request_id=rid)
            runtime._capacity_waiting_queue = [*queue, request]
            if kind == "blocked":
                runtime.graph.invocations[key.invocation_id].state = InvocationState.WAIT_TOOL
            observed = {"eligible_checks": 0, "issued": 0}

            def prepare(runtime=runtime, key=key, observed=observed):
                accepted = 0 if kind == "empty" else sum(
                    runtime._live_backup_full_owners(((key.session_id, key.session_generation),))
                    for _ in range(32)
                )
                observed["eligible_checks"] = accepted
                observed["issued"] = int(accepted > 0)
                return ((71, 1., 4),) if accepted else ()

            cache.prepare_beliefkv_idle_full = prepare
            instances[label] = runtime, observed
        durations = {label: [] for label in instances}
        for iteration in range(samples + 1):
            labels = ("candidate", "reference") if iteration % 2 else ("reference", "candidate")
            for label in labels:
                runtime, observed = instances[label]
                runtime._idle_prepare_next_ms = 0.
                start = time.perf_counter_ns()
                runtime.dispatch_idle_prepare()
                elapsed = (time.perf_counter_ns() - start) / 1000.
                if iteration:
                    durations[label].append(elapsed)
                expected = 32 if kind == "blocked" or kind == "queued" and label == "candidate" else 0
                if observed != {"eligible_checks": expected, "issued": int(expected > 0)}:
                    raise ValueError("unexpected native-copy outcome")
                if runtime._idle_backup_queue_scope or runtime._idle_backup_queued_requests is not None:
                    raise ValueError("queued backup proof escaped its native call")
                if runtime.physical_ledger.pending_count:
                    raise ValueError("background backup created restore reservations")
        return {
            "queue_size": size, "case": kind,
            "timing": {label: distribution(values) for label, values in durations.items()},
            "candidate_minus_reference_p50_us": (
                statistics.median(durations["candidate"]) - statistics.median(durations["reference"])
            ),
            "native_outcomes": {label: observed for label, (_, observed) in instances.items()},
            "queued_index_builds": {
                label: runtime.counts["idle_full_prepare_queued_index_builds"]
                for label, (runtime, _) in instances.items()
            },
            "raw_us": durations,
        }
    finally:
        for runtime, _ in instances.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", default="833a4c6")
    parser.add_argument("--samples", type=int, default=201)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 3 or args.output.exists():
        parser.error("use at least three samples and preserve old reports")
    revision = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", args.reference], text=True,
    ).strip()
    methods, old_hash = reference_methods(revision)
    paths = (
        Path(__file__), ROOT / "beliefkv/runtime/sglang_v0520_runtime.py",
        ROOT / "tests/test_idle_queued_full_backup.py",
        ROOT / "tests/test_queued_full_donor.py", ROOT / "tests/test_causal_full_donor.py",
    )
    report = {
        "reference_commit": revision, "reference_runtime_sha256": old_hash,
        "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
        "cases": [case(size, kind, methods, args.samples) for size in (8, 36, 128) for kind in ("empty", "blocked", "queued")],
        "semantics": (
            "Alternating complete dispatches with normal GC and one warmup. Existing blocked "
            "and empty cases preserve copy outcomes. Queued cases intentionally add backup "
            "eligibility. The native selector is a 32-owner scalar stand-in; there is no DMA, "
            "real allocator or GPU service. Samples do not establish frequency, JCT gain or "
            "the scheduler-wide cost of runtime control."
        ),
        "gpu_performance_accepted": False,
    }
    with args.output.open("x") as output:
        json.dump(report, output, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps([
        {name: row[name] for name in (
            "queue_size", "case", "timing", "candidate_minus_reference_p50_us", "native_outcomes",
        )} for row in report["cases"]
    ], indent=2))


if __name__ == "__main__":
    main()
