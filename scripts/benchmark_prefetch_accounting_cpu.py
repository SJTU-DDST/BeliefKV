#!/usr/bin/env python3
"""Compare native prefetch accounting using pinned CPU controller methods."""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import MethodType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.sglang_v0520_physical import prefetch_expectation_from_native_op
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.benchmark_prefetch_control_cpu import compare
from tests.test_sglang_v0520_physical import Pool, native_prefetch


def native_accounting(engine):
    path = engine / "python/sglang/srt/mem_cache/hybrid_cache/hybrid_cache_controller.py"
    source = path.read_text()
    controller = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "HybridCacheController"
    )
    methods = [
        node for node in controller.body
        if isinstance(node, ast.FunctionDef)
        and node.name in ("_num_tokens_by_pool", "_transfer_num_bytes")
    ]
    if len(methods) != 2:
        raise ValueError("native CPU accounting methods are unavailable")
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        *methods,
    ], type_ignores=[])
    namespace = {}
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace, {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(source.encode()).hexdigest(),
    }


def measure_case(baseline, methods, *, full, mamba, sidecar, samples):
    names = baseline._roll_tool_prefetch.__globals__
    variants = {}
    outputs = {}
    for name in ("baseline", "candidate"):
        step, operation, controller = native_prefetch(full=full, mamba=mamba, sidecar=sidecar)
        group = controller.mem_pool_host
        for pool, entry in group.entry_map.items():
            entry.name = pool
        group.anchor_entry = group.entry_map[Pool.KV]
        for method in ("_num_tokens_by_pool", "_transfer_num_bytes"):
            setattr(controller, method, MethodType(methods[method], controller))
        if name == "baseline":
            step = names["PrefetchLoadStep"](
                step.key, step.leaf_node_id, step.leaf_creation_time,
                step.node_id, step.creation_time, step.include_mamba,
            )
            account = names["prefetch_expectation_from_native_op"]
        else:
            account = prefetch_expectation_from_native_op
        callback = lambda account=account, step=step, operation=operation, controller=controller: (
            account("prefetch-1", step, operation, controller)
        )
        variants[name] = callback
        outputs[name] = asdict(callback())
    if outputs["baseline"] != outputs["candidate"]:
        raise AssertionError("native counts, per-pool bytes or total bytes changed")
    costs = compare(variants, samples=samples, calls=100)
    return {
        "full_tokens": full, "mamba_slots": mamba,
        "sidecar_source": sidecar.value if sidecar is not None else None,
        "costs": costs, "accounting_equal": True,
        "expectation": outputs["candidate"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--samples", type=int, default=151)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists():
        parser.error("use positive samples and a fresh output path")
    paths = (
        "beliefkv/runtime/sglang_v0520_physical.py",
        "scripts/benchmark_prefetch_accounting_cpu.py",
        "scripts/benchmark_prefetch_control_cpu.py",
        "scripts/benchmark_native_prepare_path.py",
        "tests/test_sglang_v0520_physical.py",
    )
    sources = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}
    baseline, baseline_sources = baseline_runtime(args.baseline)
    methods, native_source = native_accounting(args.engine)
    cases = [
        measure_case(
            baseline, methods, full=full, mamba=mamba, sidecar=sidecar,
            samples=args.samples,
        )
        for full, mamba, sidecar in (
            (2, 0, None), (4096, 0, None), (4096, 1, None),
            (0, 2, None), (4096, 1, Pool.KV), (0, 2, Pool.MAMBA),
        )
    ]
    if any(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest
           for path, digest in sources.items()):
        raise RuntimeError("accounting benchmark sources changed")
    if hashlib.sha256(Path(native_source["path"]).read_bytes()).hexdigest() != native_source["sha256"]:
        raise RuntimeError("native accounting source changed")
    report = {
        "scope": (
            "CPU prefetch expectation construction with exact pinned native accounting "
            "methods and tuple index stand-ins; no allocator, DMA, GPU or throughput."
        ),
        "at": datetime.now().astimezone().isoformat(),
        "baseline_revision": args.baseline,
        "baseline_source_sha256": baseline_sources,
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip(),
        "candidate_source_sha256": sources,
        "native_controller_source": native_source,
        "samples": args.samples, "warmup_samples": 20, "calls_per_sample": 100,
        "cases": cases,
        "limitations": [
            "Token counts and pool shapes are controlled; this is not an online frequency estimate.",
            "Native helpers operate on CPU tuples, not CUDA tensors or native transfer operations.",
            "No transfer completion, reuse, prediction accuracy, throughput or JCT is measured.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(cases, indent=2))


if __name__ == "__main__":
    main()
