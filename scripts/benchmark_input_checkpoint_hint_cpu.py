#!/usr/bin/env python3
"""Measure empty restore planning on CPU trees without changing restore steps."""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from statistics import median
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime import sglang_v0520_physical as physical
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from tests.test_sglang_v0520_observer import (
    ComponentData, UnifiedTreeCore, UnifiedTreeNode, _static_cache,
)

SOURCE = Path("beliefkv/runtime/sglang_v0520_physical.py")


def baseline_inspector(source):
    names = {"inspect_session_h2d_opportunity", "has_host_backed_missing_copy"}
    functions = [
        node for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    if {node.name for node in functions} != names:
        raise ValueError("baseline does not contain both inspection functions")
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        *functions,
    ], type_ignores=[])
    namespace = dict(vars(physical))
    exec(compile(ast.fix_missing_locations(module), "<baseline-inspection>", "exec"), namespace)
    return namespace["inspect_session_h2d_opportunity"]


def fixture(depth, mode):
    cache = _static_cache()
    allocator = cache.token_to_kv_pool_allocator
    allocator.size = allocator._kvcache.full_kv_pool.size = 4096
    allocator._kvcache.mamba_pool.size = 1024
    cache.req_to_token_pool.mamba_allocator.size = 1024
    entries = cache.cache_controller.mem_pool_host.entry_map
    entries["kv"].host_pool.size = 8192
    entries["mamba"].host_pool.size = 2048
    selected = depth // 2
    nodes = {}
    for index in range(depth + 1):
        full_device = bool(index) and not (mode == "missing_full" and index == selected // 2)
        state_host = bool(index) and (
            mode == "historical_state" and index != selected
            or mode == "selected_state" and index == selected
        )
        components = {
            pool: ComponentData(
                value=value, host_value=host, lock_ref=0, host_lock_ref=0,
                session_ref=1, session_ids={"session"},
            )
            for pool, value, host in (
                (0, range(1) if full_device else None, range(1) if index else None),
                (2, (0,) if index and not state_host else None, (0,) if state_host else None),
            )
        }
        nodes[index] = UnifiedTreeNode(
            id=index, creation_time=float(index), parent=nodes.get(index - 1),
            key=range(1) if index else range(0), component_data=components,
            write_through_pending_id=None, load_back_pending_id=None,
        )
    cache.tree_core = UnifiedTreeCore(node_by_id=nodes.__getitem__)
    leaf = ((depth, float(depth)),)
    anchors = physical.ContextSessionAnchors(
        PrefillCandidateKey("r", "w", "i", "c", 0, 0, "session", 1),
        ((0, leaf), (2, leaf)), 1., reusable_input_tokens=selected,
    )
    return cache, anchors


def decision(result):
    return {
        "steps": [asdict(step) for step in result.steps],
        "headroom": asdict(result.headroom),
        "required_full_tokens": result.required_full_tokens,
        "required_mamba_slots": result.required_mamba_slots,
        "fits_current_free_lists": result.fits_current_free_lists,
    }


def measure(methods, depth, mode, iterations):
    cache, anchors = fixture(depth, mode)
    captures, decisions, costs = {}, {}, {name: [] for name in methods}
    for name, method in methods.items():
        original = method.__globals__["capture_action_local_shadow"]
        calls = []

        def capture(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        with patch.dict(method.__globals__, {"capture_action_local_shadow": capture}):
            result = method(cache, anchors, skip_empty_restore=True, max_steps=depth)
        captures[name] = len(calls)
        decisions[name] = decision(result)
    if decisions["baseline"] != decisions["optimized"]:
        raise AssertionError(f"physical restore decision differs: {depth}, {mode}")
    expected = (
        {"baseline": 1, "optimized": 0} if mode == "historical_state"
        else {"baseline": 0, "optimized": 0} if mode == "resident"
        else {"baseline": 1, "optimized": 1}
    )
    if captures != expected:
        raise AssertionError(f"fixture does not exercise expected closure path: {captures}")
    for iteration in range(iterations + 8):
        order = list(methods) if iteration % 2 else list(reversed(methods))
        for name in order:
            start = time.thread_time_ns()
            result = methods[name](cache, anchors, skip_empty_restore=True, max_steps=depth)
            elapsed = (time.thread_time_ns() - start) / 1000.
            if decision(result) != decisions[name]:
                raise AssertionError("read-only repeated inspection changed decision")
            if iteration >= 8:
                costs[name].append(elapsed)
    return {
        "depth": depth, "mode": mode,
        "restore_decisions_equal": True, "closure_captures_per_call": captures,
        "thread_cpu_us": {
            name: {"samples": len(values), "p50": median(values), "max": max(values)}
            for name, values in costs.items()
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--iterations", type=int, default=81)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1 or args.output.exists():
        parser.error("positive iterations and a new output path are required")
    previous = subprocess.check_output(
        ["git", "show", f"{args.baseline_revision}:{SOURCE}"], cwd=ROOT, text=True,
    )
    methods = {
        "baseline": baseline_inspector(previous),
        "optimized": physical.inspect_session_h2d_opportunity,
    }
    report = {
        "scope": (
            "Local CPU tree fixtures only. Real inspection and closure construction "
            "are measured without timed mocks; closure counting is a separate reference "
            "pass. Restore steps, capacity requirements and fit decisions must agree. "
            "Historical-only shortcut diagnostic/candidate presence may differ and "
            "unobserved historical Mamba counts remain unknown. No native allocator, "
            "CUDA, DMA, online occurrence rate, throughput or JCT is measured."
        ),
        "baseline_revision": args.baseline_revision,
        "baseline_source_sha256": hashlib.sha256(previous.encode()).hexdigest(),
        "optimized_source_sha256": hashlib.sha256((ROOT / SOURCE).read_bytes()).hexdigest(),
        "cases": [
            measure(methods, depth, mode, args.iterations)
            for depth in (8, 64, 512)
            for mode in ("resident", "historical_state", "selected_state", "missing_full")
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"output": str(args.output), "cases": report["cases"]}, indent=2))


if __name__ == "__main__":
    main()
