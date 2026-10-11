#!/usr/bin/env python3
"""Compare checkpoint planning and whole observations on shared CPU radix paths."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import subprocess
import sys
import time
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime import sglang_v0520_physical as current
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from tests.test_sglang_v0520_observer import UnifiedTreeCore, _node, _static_cache


SOURCE = Path("beliefkv/runtime/sglang_v0520_physical.py")


def module_from_source(source, label):
    module = ModuleType(f"beliefkv.runtime._checkpoint_ancestry_{label}")
    sys.modules[module.__name__] = module
    exec(compile(source, label, "exec"), vars(module))
    return module


def baseline_module(revision):
    source = subprocess.run(
        ["git", "show", f"{revision}:{SOURCE.as_posix()}"], cwd=ROOT,
        check=True, text=True, capture_output=True,
    ).stdout
    return module_from_source(source, "baseline"), hashlib.sha256(source.encode()).hexdigest()


def fixture(case):
    cache = _static_cache()
    cache.token_to_kv_pool_allocator.size = 131072
    cache.token_to_kv_pool_allocator._kvcache.full_kv_pool.size = 131072
    cache.host_pool_group.entry_map["kv"].host_pool.size = 262144
    nodes = {0: _node(0)}
    nodes[0].key = ()
    parent = nodes[0]
    tokens_per_node = 16
    for offset in range(1, case["depth"] + 1):
        branches = case["leaves"] if offset == case["depth"] else 1
        for _ in range(branches):
            resident = case["mode"] == "prepare" or offset < case["depth"] // 2
            backed = case["mode"] == "h2d" or offset < case["depth"] // 2
            node_id = len(nodes)
            node = _node(
                node_id, parent, device=range(tokens_per_node) if resident else (),
                host=range(tokens_per_node) if backed else (),
                mamba_host=backed,
            )
            node.key = range(tokens_per_node)
            nodes[node_id] = node
        if branches == 1:
            parent = node
    leaves = tuple(
        (node_id, nodes[node_id].creation_time)
        for node_id in range(case["depth"], len(nodes))
    )
    cache.tree_core = UnifiedTreeCore(node_by_id=nodes.__getitem__)
    anchors = current.ContextSessionAnchors(
        PrefillCandidateKey("request", "workflow", "parent", "context", 1, 0, "session", 1),
        ((0, leaves), (2, leaves)), 1.,
        reusable_input_tokens=tokens_per_node * (case["depth"] - case["output_tail_nodes"]),
    )
    return cache, anchors


def callbacks(module, cache, anchors, mode):
    candidate = module.capture_action_local_shadow(
        cache, anchors, for_prefetch=mode == "h2d", include_non_actionable=True,
    )
    if candidate is None:
        raise AssertionError("valid fixture has no observable closure")
    if mode == "h2d":
        return {
            "plan": lambda: module.plan_prefetch_gpu_steps(candidate, max_steps=16),
            "whole_observation": lambda: module.inspect_session_h2d_opportunity(
                cache, anchors, max_steps=16,
            ),
        }

    def whole_prepare():
        observed = module.capture_action_local_shadow(
            cache, anchors, include_non_actionable=True,
        )
        return module.shadow_backup_steps(observed, max_steps=16)

    return {
        "plan": lambda: module.shadow_backup_steps(candidate, max_steps=16),
        "whole_observation": whole_prepare,
    }


def normalize(result):
    return [asdict(item) for item in result] if isinstance(result, tuple) else asdict(result)


def benchmark(baseline, optimized, *, samples):
    cases = [
        {"mode": mode, "depth": depth, "leaves": leaves, "output_tail_nodes": tail}
        for mode in ("h2d", "prepare")
        for depth in (8, 64, 256, 1024)
        for leaves in (1, 4)
        for tail in (0, min(4, depth // 2))
    ]
    results = []
    for case in cases:
        cache, anchors = fixture(case)
        variants = {
            name: callbacks(module, cache, anchors, case["mode"])
            for name, module in (("baseline", baseline), ("optimized", optimized))
        }
        for phase in ("plan", "whole_observation"):
            expected = normalize(variants["baseline"][phase]())
            if normalize(variants["optimized"][phase]()) != expected:
                raise AssertionError((case, phase, "changed checkpoint result"))
            calls = max(1, min(25, 4096 // case["depth"]))
            costs = {name: {"wall_us": [], "thread_cpu_us": []} for name in variants}
            for sample in range(samples + 3):
                order = list(variants)
                if sample % 2:
                    order.reverse()
                for name in order:
                    callback = variants[name][phase]
                    wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                    for _ in range(calls):
                        result = callback()
                    cpu_us = (time.thread_time_ns() - cpu) / 1000. / calls
                    wall_us = (time.perf_counter_ns() - wall) / 1000. / calls
                    if normalize(result) != expected:
                        raise AssertionError((case, phase, name, sample))
                    if sample >= 3:
                        costs[name]["wall_us"].append(wall_us)
                        costs[name]["thread_cpu_us"].append(cpu_us)
            summary = {
                name: {
                    clock: {"mean": mean(values), "p50": median(values), "samples": values}
                    for clock, values in clocks.items()
                }
                for name, clocks in costs.items()
            }
            results.append({
                **case, "phase": phase, "calls_per_sample": calls,
                "outputs_equal": True, "costs": summary,
                "mean_reduction": {
                    clock: 1. - summary["optimized"][clock]["mean"]
                    / summary["baseline"][clock]["mean"]
                    for clock in ("wall_us", "thread_cpu_us")
                },
            })
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-commit", required=True)
    parser.add_argument("--candidate-patch", type=Path)
    parser.add_argument("--samples", type=int, default=21)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("samples must be positive")
    baseline, digest = baseline_module(args.baseline_commit)
    candidate_patch = None
    if args.candidate_patch is not None:
        if hashlib.sha256((ROOT / SOURCE).read_bytes()).hexdigest() != digest:
            raise ValueError("candidate patch requires the pinned baseline source")
        candidate_source = subprocess.run(
            ["patch", "--silent", "--output=-",
             f"--input={args.candidate_patch.resolve()}", str(ROOT / SOURCE)],
            check=True, text=True, capture_output=True,
        ).stdout
        optimized = module_from_source(candidate_source, "candidate")
        candidate_patch = {
            "path": str(args.candidate_patch),
            "sha256": hashlib.sha256(args.candidate_patch.read_bytes()).hexdigest(),
            "applied_in_memory_only": True,
        }
    else:
        candidate_source = (ROOT / SOURCE).read_text()
        optimized = current
    results = benchmark(baseline, optimized, samples=args.samples)
    report = {
        "scope": (
            "CPU checkpoint planning and complete H2D/PREPARE observations on "
            "synthetic shared radix paths; excludes native allocation, IPC, DMA "
            "and GPU throughput. Wall and thread CPU measured independently."
        ),
        "baseline_commit": args.baseline_commit,
        "baseline_source_sha256": digest,
        "optimized_source": SOURCE.as_posix(),
        "optimized_source_sha256": hashlib.sha256(candidate_source.encode()).hexdigest(),
        "candidate_patch": candidate_patch,
        "observer_source_sha256": hashlib.sha256(
            (ROOT / "beliefkv/runtime/sglang_v0520_observer.py").read_bytes(),
        ).hexdigest(),
        "samples": args.samples, "outputs_equal": True, "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    for result in results:
        if result["depth"] == 1024 and result["output_tail_nodes"] == 0:
            before = result["costs"]["baseline"]["wall_us"]["mean"]
            after = result["costs"]["optimized"]["wall_us"]["mean"]
            print(
                f'{result["mode"]}/{result["phase"]}/{result["leaves"]} leaves: '
                f"{before:.1f} -> {after:.1f} us"
            )


if __name__ == "__main__":
    main()
