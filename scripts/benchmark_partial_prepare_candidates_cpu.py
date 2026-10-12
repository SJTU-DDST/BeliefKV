#!/usr/bin/env python3
"""Measure live PREPARE probing and settled-copy registration on CPU fixtures."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime, fixture
from scripts.benchmark_native_shared_path import distribution


def measure_case(baseline, *, depth, layout, samples):
    variants = {
        name: fixture(
            cls, workflows=8, depth=depth, backed=layout == "backed",
            host_full=1_000_000, leases=0,
        )
        for name, cls in (("baseline", baseline), ("optimized", NativeAdmissionRuntime))
    }
    timings = {
        name: {"wall_us": [], "thread_cpu_us": []} for name in variants
    }
    observed = {}
    try:
        for runtime, *_ in variants.values():
            key = runtime.context_sessions["ctx-wf-0-root"]
            if layout == "backed_state_missing_full":
                node = runtime._native_cache.tree_core.node_by_id(depth - 1)
                node.component_data[2].host_value = node.component_data[2].value
            assert key.context_id == "ctx-wf-0-root"
        for sample in range(samples + 5):
            order = list(variants)
            if sample % 2:
                order.reverse()
            steps = {}
            for name in order:
                runtime, _, reads, _, _ = variants[name]
                key = runtime.context_sessions["ctx-wf-0-root"]
                runtime._parent_pressure_candidates.clear()
                before = reads.copy()
                cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
                # Every probe captures a fresh closure. Free-list/headroom
                # serialization and publication are outside this measurement.
                step = runtime.refreshed_shadow_backup_step(
                    context_id=key.context_id, source="join_prepare",
                )
                wall_us = (time.perf_counter_ns() - wall) / 1000.
                cpu_us = (time.thread_time_ns() - cpu) / 1000.
                steps[name] = asdict(step) if step is not None else None
                probe_reads = dict(reads - before)
                runtime._publish_parent_pressure_candidates()
                observed[name] = {
                    "registered_nodes": sorted(runtime._parent_pressure_candidates),
                    "published_by_component": {
                        str(component): list(values) for component, values in
                        runtime._native_cache.beliefkv_join_pressure_candidates_by_component.items()
                    },
                    "probe_reads": probe_reads,
                    "has_remaining_backup": step is not None,
                }
                if sample >= 5:
                    timings[name]["wall_us"].append(wall_us)
                    timings[name]["thread_cpu_us"].append(cpu_us)
            if steps["baseline"] != steps["optimized"]:
                raise AssertionError("PREPARE transfer selection changed")
            if (
                observed["baseline"]["probe_reads"]["session_snapshot"] != 1
                or observed["optimized"]["probe_reads"]["session_snapshot"] != 1
                or observed["baseline"]["probe_reads"] != observed["optimized"]["probe_reads"]
            ):
                raise AssertionError("registration reread the native closure")
        registered = {name: value["registered_nodes"] for name, value in observed.items()}
        if layout == "backed":
            if registered["baseline"] != registered["optimized"]:
                raise AssertionError("fully backed registration changed")
        elif not (
            not registered["baseline"] and registered["optimized"]
            and observed["optimized"]["has_remaining_backup"]
        ):
            raise AssertionError("settled copies remain hidden behind pending backup")
        if layout == "backed_state_missing_full" and not (
            not observed["baseline"]["published_by_component"]["2"]
            and observed["optimized"]["published_by_component"]["2"]
        ):
            raise AssertionError("settled Mamba copy is not published")
        summaries = {
            name: {clock: {
                **{key.removesuffix("_ms"): value for key, value in distribution(values).items()},
                "samples": values,
            } for clock, values in clocks.items()}
            for name, clocks in timings.items()
        }
        return {
            "depth": depth, "layout": layout, "samples": samples,
            "prepare_selection_equal": True,
            "native_reads_equal": True, "observed": observed,
            "costs_us": summaries,
            "thread_cpu_delta_us": (
                summaries["optimized"]["thread_cpu_us"]["mean"]
                - summaries["baseline"]["thread_cpu_us"]["mean"]
            ),
            "thread_cpu_reduction_fraction": (
                1. - summaries["optimized"]["thread_cpu_us"]["mean"]
                / summaries["baseline"]["thread_cpu_us"]["mean"]
            ),
        }
    finally:
        for runtime, *_ in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--samples", type=int, default=51)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists():
        raise ValueError("positive samples and a new output path required")
    baseline, digests = baseline_runtime(args.baseline_revision)
    started = time.time()
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=1000.):
        cases = [
            measure_case(baseline, depth=depth, layout=layout, samples=args.samples)
            for depth in (4, 16, 64)
            for layout in ("backed", "partial_full", "backed_state_missing_full")
        ]
    paths = [
        "beliefkv/runtime/sglang_v0520_observer.py",
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "scripts/benchmark_partial_prepare_candidates_cpu.py",
        "scripts/benchmark_native_prepare_path.py",
    ]
    report = {
        "scope": (
            "CPU cache stand-ins and live probe captures; no actual reclamation, "
            "DMA, native opportunity frequency or GPU throughput proof."
        ),
        "baseline_revision": args.baseline_revision,
        "baseline_source_sha256": digests,
        "optimized_source_sha256": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in paths
        },
        "started_ts_ms": started * 1000., "finished_ts_ms": time.time() * 1000.,
        "cases": cases,
        "semantics": [
            "Only settled copies inside reusable input are registered.",
            "PREPARE selects identical transfers and retains FULL-only payloads.",
            "Registration reuses the fresh action-local closure without another native lookup.",
            "Publication still checks current native locks, transfers, ownership and device leaves.",
            "Earlier candidate visibility is not proof of actual reclaimable capacity.",
            "Added partial-copy registration costs are preserved, not reported as universal speedup.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([
        {key: case[key] for key in (
            "depth", "layout", "thread_cpu_delta_us", "thread_cpu_reduction_fraction",
        )} for case in cases
    ], indent=2))


if __name__ == "__main__":
    main()
