#!/usr/bin/env python3
"""Compare partial wait recovery on CPU fixtures, without GPU benefit claims."""

from __future__ import annotations

import argparse
import ast
from contextlib import ExitStack
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import MethodType, SimpleNamespace as NS
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.runtime import sglang_v0520_runtime as runtime_module
from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget, TransferStartWindow
from beliefkv.runtime.sglang_v0520_physical import inspect_session_h2d_opportunity
from tests.test_wait_prefetch_batch import batch_fixture

SOURCE = "beliefkv/runtime/sglang_v0520_runtime.py"
METHODS = (
    "_wait_prefetch_opportunity", "_reclaim_wait_prefetch_capacity",
    "_issue_wait_prefetch_steps",
)


def baseline_methods(revision: str):
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{SOURCE}"], cwd=ROOT,
    )
    tree = ast.parse(source)
    klass = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                 and node.name == "NativeAdmissionRuntime")
    functions = [
        node for node in klass.body
        if isinstance(node, ast.FunctionDef) and node.name in METHODS
    ]
    if {node.name for node in functions} != set(METHODS):
        raise ValueError("baseline does not contain all wait recovery methods")
    namespace = dict(vars(runtime_module))
    exec(compile(ast.Module(body=functions, type_ignores=[]), SOURCE, "exec"), namespace)
    return {name: namespace[name] for name in METHODS}, hashlib.sha256(source).hexdigest()


def run_case(source: str, free_full: int, free_mamba: int, methods=None) -> dict:
    runtime, key, opportunity, cache, _ = batch_fixture(source)
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    cache.reclaim_beliefkv_handoff_capacity = Mock(return_value={})
    headroom = replace(
        opportunity.headroom, device_full_free_tokens=free_full,
        device_mamba_free_slots=free_mamba,
    )

    def inspect(**kwargs):
        return inspect_session_h2d_opportunity(
            cache, opportunity.anchors, max_steps=kwargs["max_steps"],
            fit_current_capacity=kwargs.get("fit_current_capacity", False),
        )

    try:
        with ExitStack() as stack:
            for name, method in (methods or {}).items():
                stack.enter_context(patch.object(runtime, name, MethodType(method, runtime)))
            stack.enter_context(patch.object(
                runtime, "inspect_context_h2d_opportunity", side_effect=inspect,
            ))
            stack.enter_context(patch.object(runtime, "_publish_parent_pressure_candidates"))
            stack.enter_context(patch.object(
                runtime, "_current_residency_budget",
                return_value=PrefetchResidencyBudget(
                    1, 1024, evictable_full_tokens=16, evictable_mamba_slots=2,
                ),
            ))
            stack.enter_context(patch.object(
                runtime, "_h2d_start_window",
                return_value=TransferStartWindow(200., 0., 500.),
            ))
            stack.enter_context(patch(
                "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
                return_value=NS(
                    observable=True, nodes=tuple(reversed(opportunity.candidate.nodes)),
                ),
            ))
            stack.enter_context(patch(
                "beliefkv.runtime.sglang_v0520_physical.observe_static_full_mamba_headroom",
                return_value=headroom,
            ))
            if source == "tool_wait":
                runtime._roll_tool_prefetch()
            plan = runtime._reclaim_wait_prefetch_capacity(
                key, replace(opportunity, fits_current_free_lists=False), source=source,
            )
            commands = (
                runtime._issue_wait_prefetch_steps(key, plan.steps, source=source)
                if plan.fits_current_free_lists is True else ()
            )
        submitted = next(
            (row for row in records if row["event"] == "wait_prefetch_plan"
             and row["reason"] == "submitted"), None,
        )
        return {
            "planned_nodes": len(plan.steps),
            "planned_full_tokens": plan.required_full_tokens,
            "planned_mamba_slots": plan.required_mamba_slots,
            "deferred_mamba_slots": plan.deferred_mamba_slots,
            "fits_current_capacity": plan.fits_current_free_lists,
            "submitted_node_commands": len(commands),
            "submitted_pool_bytes": submitted["planned_pool_bytes"] if submitted else {},
        }
    finally:
        runtime.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    methods, baseline_sha = baseline_methods(args.baseline)
    cases = []
    for source in ("join_ticket", "tool_wait"):
        for free_full, free_mamba in ((10, 0), (16, 0), (20, 1), (3, 1)):
            cases.append({
                "source": source,
                "device_free_full_tokens": free_full,
                "device_free_mamba_slots": free_mamba,
                "baseline": run_case(source, free_full, free_mamba, methods),
                "updated": run_case(source, free_full, free_mamba),
            })
    report = {
        "baseline_revision": args.baseline,
        "baseline_source_sha256": baseline_sha,
        "updated_source_sha256": hashlib.sha256((ROOT / SOURCE).read_bytes()).hexdigest(),
        "scope": (
            "CPU fixtures with a real physical prefix planner and runtime action ledger; "
            "native allocation, enqueue and closure observations are stubs. The whole "
            "checkpoint needs 16 FULL units and one Mamba slot; global evictable capacity "
            "exceeds the bounded reclaimer, which releases nothing. JOIN fixtures use "
            "a confirmed ticket. No real transfer, pre-boundary timing, throughput, "
            "recoverable trace bytes or JCT saving is measured."
        ),
        "cases": cases,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "cases": len(cases)}))


if __name__ == "__main__":
    main()
