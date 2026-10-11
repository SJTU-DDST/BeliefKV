#!/usr/bin/env python3
"""Compare tool timing, reclaim and enqueue on identical CPU prefix fixtures."""

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
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beliefkv.runtime import sglang_v0520_runtime as runtime_module
from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from tests.test_wait_prefetch_batch import batch_fixture

SOURCE = Path("beliefkv/runtime/sglang_v0520_runtime.py")
METHOD = "_h2d_start_window"


def load_baseline(revision):
    source = subprocess.check_output(["git", "show", f"{revision}:{SOURCE}"], cwd=ROOT)
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "NativeAdmissionRuntime")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                  and node.name == METHOD)
    namespace = dict(vars(runtime_module))
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        method,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), namespace)
    return namespace[METHOD], hashlib.sha256(source).hexdigest()


def case(remaining, lower, capacity, *, baseline=None):
    runtime, key, opportunity, cache, _ = batch_fixture("tool_wait")
    hint = replace(
        runtime.tool_wait_hints[key.context_id],
        wait_p10_ms=lower, wait_p50_ms=remaining, wait_p90_ms=remaining + 600.,
    )
    runtime.tool_wait_hints[key.context_id] = hint
    runtime._h2d_samples.extend(tuple(
        (size, 15.) for size in (40, 80, 160, 180) for _ in range(3)
    ))
    if baseline is not None:
        runtime._h2d_start_window = MethodType(baseline, runtime)
    full, mamba = {
        "free": (24, 2), "reclaimable": (20, 1),
        "partial": (12, 1), "unavailable": (4, 0),
    }[capacity]
    headroom = [replace(
        opportunity.headroom, device_full_free_tokens=full,
        device_mamba_free_slots=mamba,
    )]
    budget = PrefetchResidencyBudget(
        1, 1024, reserve_full_tokens=8, reserve_mamba_slots=1,
        evictable_full_tokens=16, evictable_mamba_slots=1,
    )
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)

    def reclaim(**kwargs):
        if capacity != "reclaimable":
            return {}
        headroom[0] = replace(
            headroom[0], device_full_free_tokens=kwargs["full_tokens"],
            device_mamba_free_slots=kwargs["mamba_slots"],
        )
        return {0: 4, 2: 1}

    cache.reclaim_beliefkv_handoff_capacity = Mock(side_effect=reclaim)
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
                   return_value=hint.issued_monotonic_ms / 1000.), \
            patch.object(runtime, "inspect_context_h2d_opportunity", side_effect=lambda **kwargs:
                          replace(opportunity, headroom=headroom[0])), \
            patch.object(runtime, "_current_residency_budget", return_value=budget), \
            patch.object(runtime, "_publish_parent_pressure_candidates"):
            timings = []
            for _ in range(200):
                started = time.thread_time_ns()
                window = runtime._h2d_start_window(opportunity)
                timings.append((time.thread_time_ns() - started) / 1e6)
            runtime.dispatch_tool_prefetch()
        submitted = next((row for row in records if row["event"] == "wait_prefetch_plan"
                          and row["reason"] == "submitted"), None)
        final_nodes = (
            cache.prefetch_gpu_session_nodes.call_args.kwargs["nodes"]
            if cache.prefetch_gpu_session_nodes.called else ()
        )
        return {
            "initial_horizon_ms": window.horizon_ms,
            "prediction_early_ms": window.prediction_early_ms,
            "reclaim_calls": cache.reclaim_beliefkv_handoff_capacity.call_count,
            "submitted_node_commands": runtime.physical_ledger.pending_count,
            "submitted_pool_bytes": submitted["planned_pool_bytes"] if submitted else {},
            "planned_rejections": [row["reason"] for row in records
                                   if row["event"] == "wait_prefetch_plan"],
            "selected_node_ids": [node[0] for node in final_nodes],
            "selected_mamba_nodes": [node[0] for node in final_nodes if node[2]],
            "window_thread_cpu_p50_ms": statistics.median(timings),
            "window_thread_cpu_mean_ms": statistics.mean(timings),
        }
    finally:
        runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    baseline, source_sha = load_baseline(args.baseline)
    cases = []
    for remaining, lower in ((300., 100.), (380., 100.), (300., 300.), (80., 60.)):
        for capacity in ("free", "reclaimable", "partial", "unavailable"):
            result = {
                "remaining_p50_ms": remaining, "remaining_p10_ms": lower,
                "capacity": capacity,
                "baseline": case(remaining, lower, capacity, baseline=baseline),
                "updated": case(remaining, lower, capacity),
            }
            expected = {"free": 4, "reclaimable": 4, "partial": 1, "unavailable": 0}
            if remaining == lower:
                expected = dict.fromkeys(expected, 0)
            assert result["updated"]["submitted_node_commands"] == expected[capacity]
            if remaining == lower or remaining == 80.:
                for field in ("submitted_node_commands", "submitted_pool_bytes",
                              "selected_node_ids", "selected_mamba_nodes"):
                    assert result["baseline"][field] == result["updated"][field]
            if capacity == "unavailable":
                assert result["baseline"]["submitted_node_commands"] == 0
            cases.append(result)
    report = {
        "scope": (
            "Identical causal tool hints and real CPU prefix planner/action ledger. "
            "Native allocation, headroom and enqueue are mocked; reclaimable capacity "
            "is supplied by the fixture. No GPU, DMA, ACK, early reuse, eviction cost "
            "or throughput/JCT improvement is measured."
        ),
        "baseline_revision": args.baseline,
        "baseline_source_sha256": source_sha,
        "updated_runtime_sha256": hashlib.sha256((ROOT / SOURCE).read_bytes()).hexdigest(),
        "updated_policy_sha256": hashlib.sha256(
            (ROOT / "beliefkv/runtime/native_transfer_policy.py").read_bytes(),
        ).hexdigest(),
        "max_lead_ms": 500., "observation_spacing_ms": 100.,
        "policy_plan_expectations_checked": True,
        "cases": cases,
    }
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"output": str(args.output), "cases": len(cases)}, indent=2))


if __name__ == "__main__":
    main()
