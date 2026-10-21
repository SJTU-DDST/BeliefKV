#!/usr/bin/env python3
"""Compare identity storage through the same CPU planner and live selector."""

from __future__ import annotations

import argparse
from copy import copy, deepcopy
from dataclasses import asdict, replace
import gc
import hashlib
import json
from pathlib import Path
import pickle
from statistics import median
import subprocess
import sys
import time
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime import sglang_v0520_admission as admission
from beliefkv.runtime import sglang_v0520_runtime as runtime_module
from scripts.benchmark_native_shared_path import synthetic_runtime

SOURCE = "beliefkv/runtime/sglang_v0520_admission.py"


def bindings(module):
    return {
        name: getattr(module, name) for name in (
            "PrefillCandidateKey", "_request_key", "compile_native_prefill_plan",
        )
    }


def fixture(module, size):
    with patch.dict(vars(runtime_module), bindings(module)):
        runtime, queue = synthetic_runtime(
            runtime_module.NativeAdmissionRuntime, CausalFrontierScheduler, size, 4,
        )
    runtime._tool_timing_only = True
    runtime.enable_resident_first = False
    return runtime, queue


def snapshot(runtime, plan, selection):
    return {
        "plan": asdict(plan),
        "selected": [req.rid for req in selection.candidates],
        "rejected": selection.rejected,
        "visible": {rid: asdict(key) for rid, key in runtime.visible.items()},
        "sessions": {cid: asdict(key) for cid, key in runtime.context_sessions.items()},
        "counts": dict(runtime.counts),
        "semantic_revision": runtime.semantic_revision,
        "graph": runtime.graph.snapshot(),
        "pending_physical": runtime.physical_ledger.pending_count,
    }


def measure(modules, *, size, copied, samples):
    fixtures = {name: fixture(module, size) for name, module in modules.items()}
    values = {name: {"wall_us": [], "thread_cpu_us": []} for name in modules}
    calls = 20
    running, adder = NS(reqs=[]), NS()
    try:
        for sample in range(samples + 20):
            observations = {}
            for name in tuple(modules)[::1 if sample % 2 else -1]:
                module = modules[name]
                runtime, queue = fixtures[name]
                if copied:
                    runtime.visible = {rid: replace(key) for rid, key in runtime.visible.items()}
                now = time.monotonic()
                runtime._visible_since = {rid: now for rid in runtime.visible}
                with patch.dict(vars(runtime_module), bindings(module)):
                    wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                    for _ in range(calls):
                        runtime.scheduler_step(queue, defer_observations=True)
                        inspected = runtime.inspect_native_prefill_queue(queue)
                        plan = runtime.plan_native_prefill(
                            queue, running_batch=running, adder=adder,
                            _queue_inspection=inspected,
                        )
                        selection = module.select_native_prefill_candidates(
                            queue, plan=plan,
                            current_semantic_revision=runtime.semantic_revision,
                        )
                    elapsed_wall = (time.perf_counter_ns() - wall) / calls / 1000.
                    elapsed_cpu = (time.thread_time_ns() - cpu) / calls / 1000.
                if sample >= 20:
                    values[name]["wall_us"].append(elapsed_wall)
                    values[name]["thread_cpu_us"].append(elapsed_cpu)
                observations[name] = snapshot(runtime, plan, selection)
            if observations["baseline"] != observations["candidate"]:
                raise AssertionError("identity, ordering, graph or physical state changed")
            if len(selection.candidates) != size or selection.rejected:
                raise AssertionError("fixture did not authorize the complete live queue")
        return {
            "queue_size": size, "equal_keys_copied_before_sample": copied,
            "calls_per_sample": calls, "complete_planning_selection_state_equal": True,
            "costs": {
                name: {
                    clock: {"p50": median(samples_), "samples": samples_}
                    for clock, samples_ in clocks.items()
                } for name, clocks in values.items()
            },
        }
    finally:
        for runtime, _ in fixtures.values():
            runtime.close()


def storage(module):
    key = module.PrefillCandidateKey("r", "wf", "agent", "ctx", 1, 0, "session", 1)
    for convert in (
        copy, deepcopy, lambda value: replace(value),
        lambda value: pickle.loads(pickle.dumps(value)),
        lambda value: module.PrefillCandidateKey(**json.loads(json.dumps(asdict(value)))),
    ):
        restored = convert(key)
        if type(restored) is not type(key) or asdict(restored) != asdict(key):
            raise AssertionError("identity copy or serialization changed")
        if {key: True}[restored] is not True:
            raise AssertionError("serialized identity no longer names the same authorization")
    return {
        "instance_bytes": sys.getsizeof(key),
        "instance_dictionary_bytes": (
            sys.getsizeof(key.__dict__) if hasattr(key, "__dict__") else 0
        ),
        "excludes_referenced_field_objects": True,
        "copy_replace_pickle_json_and_hash_equal": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--samples", type=int, default=101)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists() or not gc.isenabled():
        parser.error("positive samples, normal GC and a fresh output are required")
    source = subprocess.check_output(
        ["git", "show", f"{args.baseline}:{SOURCE}"], cwd=ROOT, text=True,
    )
    baseline = ModuleType("beliefkv.runtime._identity_storage_baseline")
    sys.modules[baseline.__name__] = baseline
    exec(compile(source, f"{args.baseline}:{SOURCE}", "exec"), vars(baseline))
    paths = (
        SOURCE, "beliefkv/runtime/sglang_v0520_runtime.py",
        "scripts/benchmark_native_shared_path.py",
        "scripts/benchmark_prefill_identity_storage_cpu.py",
    )
    sources = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}
    modules = {"baseline": baseline, "candidate": admission}
    cases = [
        measure(modules, size=size, copied=copied, samples=args.samples)
        for size in (12, 64, 156) for copied in (False, True)
    ]
    if any(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != sha
           for name, sha in sources.items()):
        raise RuntimeError("benchmark source changed during measurement")
    result = {
        "scope": (
            "CPU scheduler maintenance, queue inspection, complete planning and "
            "live selection with each admission module's own identity class. "
            "Native allocation, cache matching, transfers, GPU and JCT are outside "
            "this fixture. Locality scans are disabled. Both arms use the same "
            "runtime body; source modules, key objects and planner results stay "
            "independent. Normal GC and all outliers are retained."
        ),
        "baseline": args.baseline,
        "baseline_admission_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "candidate_source_sha256": sources, "normal_gc": True,
        "alternating_samples": args.samples, "warmup_samples": 20,
        "retains_all_outliers": True, "cases": cases,
        "storage": {name: storage(module) for name, module in modules.items()},
        "gpu_performance_accepted": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({
        "output": str(args.output), "cases": len(cases),
        "complete_planning_selection_state_equal": True,
        "gpu_performance_accepted": False,
    }, indent=2))


if __name__ == "__main__":
    main()
