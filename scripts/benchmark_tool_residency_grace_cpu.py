#!/usr/bin/env python3
"""Measure bounded short-tool native pins and their scheduler maintenance."""

import argparse
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.benchmark_prefetch_control_cpu import distribution
from tests.test_full_only_native_lifecycle import full_native
from tests.test_tool_residency_grace import pinned_round


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=201)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists():
        parser.error("positive samples and a new output are required")
    names = (
        "beliefkv/runtime/tool_continuity.py",
        "beliefkv/runtime/tool_residency_grace.py",
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "tests/test_tool_residency_grace.py",
        "scripts/benchmark_tool_residency_grace_cpu.py",
    )
    sources = {name: sha256_file(ROOT / name) for name in names}
    samples = {"pin_release_and_reacquire": [], "maintenance_disabled": [],
               "maintenance_empty": [], "maintenance_pinned": []}
    with pytest.MonkeyPatch.context() as patch:
        native = full_native.__wrapped__(patch, NS())
        fixture = pinned_round.__wrapped__(native, patch)
        runtime, _, _, leaf, _, cache, _, writes, loads = next(fixture)
        manager = runtime._tool_residency_grace
        key = manager.key
        try:
            for index in range(args.samples + 20):
                started = time.thread_time_ns()
                runtime._release_tool_residency_grace("benchmark")
                assert manager.retain(cache, key, now=12.)
                elapsed = (time.thread_time_ns() - started) / 1e3
                if index >= 20:
                    samples["pin_release_and_reacquire"].append(elapsed)
                cases = ("disabled", "empty", "pinned")
                for case in cases if index % 2 else tuple(reversed(cases)):
                    runtime._release_tool_residency_grace("benchmark")
                    runtime._tool_residency_grace = None if case == "disabled" else manager
                    if case == "pinned":
                        assert manager.retain(cache, key, now=12.)
                    started = time.thread_time_ns()
                    runtime.scheduler_step(defer_observations=True, defer_wait_predictions=True)
                    elapsed = (time.thread_time_ns() - started) / 1e3
                    if index >= 20:
                        samples["maintenance_" + case].append(elapsed)
                    runtime._tool_residency_grace = manager
            runtime._release_tool_residency_grace("benchmark_finished")
            assert leaf.component_data[0].lock_ref == 0
            assert len(writes) == 1 and not loads
        finally:
            runtime._tool_residency_grace = manager
            fixture.close()
    if sources != {name: sha256_file(ROOT / name) for name in names}:
        raise RuntimeError("benchmark sources changed")
    result = {
        "scope": "Real CPU native radix lock receipts and runtime maintenance; copy queues are stand-ins.",
        "sampling": "Alternating order; normal GC; twenty warmups; thread CPU time; microseconds.",
        "samples_per_case": args.samples,
        "cases": {name: distribution(values) for name, values in samples.items()},
        "raw_us": samples, "source_sha256": sources,
        "new_h2d_operations": 0, "new_d2h_operations": 0,
        "final_native_lock_ref": 0, "performance_accepted": False,
    }
    with args.output.open("x") as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps({name: value for name, value in result.items()
                      if name not in ("raw_us", "source_sha256")}, indent=2))


if __name__ == "__main__":
    main()
