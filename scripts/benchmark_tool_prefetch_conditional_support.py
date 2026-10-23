#!/usr/bin/env python3
"""Measure conditional tool-prefetch eligibility against a pinned CPU method."""

from __future__ import annotations

import argparse
import ast
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.predictor.online_workload_timing import OnlineWorkloadToolTiming
from beliefkv.runtime import sglang_v0520_runtime as current
from scripts.benchmark_long_chat_token_cache import source_binding
from scripts.benchmark_prefetch_control_cpu import compare
from tests.test_online_tool_history_revision import completed, ready
from tests.test_tool_predictive_transfers import tool_runtime


def baseline_method(revision):
    path = "beliefkv/runtime/sglang_v0520_runtime.py"
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{path}"], cwd=ROOT, text=True,
    )
    runtime = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "NativeAdmissionRuntime"
    )
    method = next(
        node for node in runtime.body
        if isinstance(node, ast.FunctionDef) and node.name == "_tool_prefetch_remaining"
    )
    namespace = dict(vars(current))
    exec(compile(ast.Module(body=[method], type_ignores=[]), f"{revision}:{path}", "exec"), namespace)
    return namespace[method.name], {path: hashlib.sha256(source.encode()).hexdigest()}


def measure(baseline, *, scope, history, survivors, samples):
    runtime, hint = tool_runtime()
    now = hint.issued_monotonic_ms + 1.
    if scope is not None:
        model = runtime._online_tool_timing = OnlineWorkloadToolTiming()
        workflow = hint.key.root_workflow_id
        for index in range(history):
            completed(
                model, index, workflow=workflow if scope == "workflow" else "other",
                duration=20_000. if index < survivors else 1000.,
            )
        ready(model, workflow=workflow, invocation=hint.key.invocation_id)
        now = 29_500.
        estimate = model.estimate(workflow, hint.key.invocation_id, now_ms=now)
        if estimate is None or estimate.scope != scope:
            raise AssertionError("fixture lacks the intended conditional forecast")
        hint = replace(
            hint, wait_p10_ms=estimate.p10_ms, wait_p50_ms=estimate.p50_ms,
            wait_p90_ms=estimate.p90_ms, release_cdf=estimate.release_cdf,
            issued_monotonic_ms=now, expires_monotonic_ms=now + 1000.,
            residual_support_detail=f"online_{scope}_command",
        )
    try:
        callbacks = {
            "baseline": lambda: baseline(runtime, hint, .5, now_ms=now),
            "candidate": lambda: runtime._tool_prefetch_remaining(hint, .5, now_ms=now),
        }
        values = {name: callback() for name, callback in callbacks.items()}
        supported = scope is None or survivors >= (4 if scope == "workflow" else 8)
        if values["baseline"] is None or (
            values["candidate"] != values["baseline"] if supported
            else values["candidate"] is not None
        ):
            raise AssertionError("conditional support or unchanged quantile disagrees")
        costs = compare(callbacks, samples=samples)
        if runtime.physical_disabled or runtime.physical_ledger.pending_count:
            raise AssertionError("eligibility query changed physical state")
        return {
            "scope": scope or "legacy", "history_samples": history,
            "surviving_completed_samples": survivors,
            "candidate_prefetch_supported": supported, "values_ms": values,
            "costs": costs,
            "added_mean_thread_cpu_us": costs["candidate"]["mean_us"] - costs["baseline"]["mean_us"],
            "physical_actions": 0,
        }
    finally:
        runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--samples", type=int, default=301)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists():
        parser.error("use positive samples and a fresh output")
    baseline, bindings = baseline_method(args.baseline)
    sources = [
        source_binding(ROOT / path) for path in (
            "beliefkv/predictor/online_workload_timing.py",
            "beliefkv/runtime/sglang_v0520_runtime.py",
            "scripts/benchmark_tool_prefetch_conditional_support.py",
            "scripts/benchmark_prefetch_control_cpu.py",
            "tests/test_online_tool_history_revision.py",
            "tests/test_tool_predictive_transfers.py",
        )
    ]
    cases = [
        measure(baseline, scope=None, history=0, survivors=0, samples=args.samples),
        *(measure(
            baseline, scope=scope, history=count, survivors=survivors, samples=args.samples,
        ) for scope, count, survivors in (
            ("workflow", 4, 4), ("workflow", 8, 1),
            ("workflow", 64, 4), ("workflow", 64, 1),
            ("run", 8, 8), ("run", 16, 2), ("run", 64, 8), ("run", 64, 1),
        )),
    ]
    if any(source_binding(Path(row["path"])) != row for row in sources):
        raise ValueError("benchmark sources changed")
    report = {
        "at": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "Controlled CPU queries with alternating order, 20 warmup samples and "
            "50 calls per sample. The pinned baseline method uses the same live "
            "fixtures. Sparse-tail decisions intentionally differ. Frequencies "
            "are not workload weighted; no GPU, DMA or JCT benefit is measured."
        ),
        "baseline_revision": args.baseline, "baseline_source_sha256": bindings,
        "candidate_sources": sources, "samples_per_case": args.samples,
        "cases": cases, "performance_accepted": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(args.output), "cases": cases}, indent=2))


if __name__ == "__main__":
    main()
