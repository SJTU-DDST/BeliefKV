#!/usr/bin/env python3
"""Measure tool selection with empty and positive restore plans on CPU."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gc
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.benchmark_prefetch_control_cpu import distribution
from scripts.benchmark_wait_prefetch_dispatch_cpu import dispatch_fixture


def measure(baseline, *, depth, target, cached, slots, samples):
    variants = {
        name: dispatch_fixture(
            cls, source="tool_wait", depth=depth, native_budget=True, running=12,
        )
        for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime))
    }
    values = {name: [] for name in variants}
    budget_calls = Counter()
    choices = {}
    try:
        for name, (runtime, _, cache, reads, *_) in variants.items():
            if not target:
                for node_id in range(depth + 1):
                    node = cache.tree_core.node_by_id(node_id)
                    for component in node.component_data.values():
                        component.value = component.host_value
            if not slots:
                runtime._native_prefill_slots = 0
            original = runtime._current_residency_budget

            def counted(*, _name=name, _original=original, **kwargs):
                budget_calls[_name] += 1
                return _original(**kwargs)

            runtime._current_residency_budget = counted
        for iteration in range(samples + 10):
            for name in ("baseline", "candidate")[::1 if iteration % 2 else -1]:
                runtime, key, _, reads, actions, _, reset, _, now = variants[name]
                reset()
                with patch("time.monotonic", lambda: now):
                    if cached:
                        runtime._tool_opportunity_cache[key] = (
                            now * 1000. + 100., runtime._wait_prefetch_opportunity(key),
                        )
                    budget_calls[name] = 0
                    start = time.thread_time_ns()
                    runtime._roll_tool_prefetch()
                    elapsed = (time.thread_time_ns() - start) / 1000.
                ticket = runtime._tool_ticket
                choices[name] = None if ticket is None else (
                    ticket.key.context_id, ticket.revision, ticket.start_window_ms,
                )
                assert not actions and not runtime.physical_ledger.pending_count
                assert not runtime.physical_disabled
                if iteration >= 10:
                    values[name].append(elapsed)
        assert choices["baseline"] == choices["candidate"]
        assert bool(choices["candidate"]) is (target and slots)
        if not target and cached:
            assert budget_calls["candidate"] == 0
        return {
            "depth": depth, "positive_target": target, "cached_plan": cached,
            "admission_slots_available": slots,
            "costs": {
                name: {**distribution(samples_), "min_us": min(samples_),
                       "max_us": max(samples_), "samples_us": samples_}
                for name, samples_ in values.items()
            },
            "last_call_budget_queries": dict(budget_calls),
            "selection_equal": True, "physical_actions": 0,
        }
    finally:
        for runtime, *_ in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", default="a2b35f5")
    parser.add_argument("--samples", type=int, default=101)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists():
        raise ValueError("use positive samples and a new output")
    paths = (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "scripts/benchmark_tool_empty_restore_cpu.py",
        "scripts/benchmark_native_prepare_path.py",
        "scripts/benchmark_wait_prefetch_dispatch_cpu.py",
        "scripts/benchmark_prefetch_control_cpu.py",
    )
    sources = {
        str(ROOT / path): hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in paths
    }
    baseline, baseline_sources = baseline_runtime(args.baseline_revision)
    cases = [
        measure(
            baseline, depth=depth, target=target, cached=cached,
            slots=slots, samples=args.samples,
        )
        for depth in (1, 32, 224)
        for target, cached, slots in (
            (False, False, True), (False, True, True),
            (True, False, True), (True, True, True), (True, False, False),
        )
    ]
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == value
               for path, value in sources.items())
    report = {
        "status": "cpu_selection_measurement_not_gpu_performance",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "baseline_revision": args.baseline_revision,
        "baseline_sources": baseline_sources, "source_sha256": sources,
        "gc_enabled": gc.isenabled(), "samples_per_variant": args.samples,
        "cases": cases,
        "scope": (
            "CPU native tree/allocator substitutes, complete tool selector; no "
            "transfer, ACK or first-use cost and no aggregate CPU/JCT extrapolation."
        ),
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps([
        {key: case[key] for key in (
            "depth", "positive_target", "cached_plan", "admission_slots_available",
        )} | {"p50_us": {name: row["p50_us"] for name, row in case["costs"].items()}}
        for case in cases
    ], indent=2))


if __name__ == "__main__":
    main()
