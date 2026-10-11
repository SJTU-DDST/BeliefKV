#!/usr/bin/env python3
"""Compare old full inference and tool-only inference with one loaded artifact."""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [str(ROOT), *(item for item in sys.path if item != str(ROOT))]

from beliefkv.predictor import structured_frontier as model_module
from beliefkv.predictor.structured_frontier import FrontierBeliefModel, LocalFrontierFeatures
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_predictor_worker import predict_tool_wait_batch
from scripts.benchmark_native_shared_path import distribution

SOURCE = Path("beliefkv/predictor/structured_frontier.py")


def baseline_predict(revision: str):
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{SOURCE.as_posix()}"], cwd=ROOT, text=True,
    )
    tree = ast.parse(source)
    original = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == "FrontierBeliefModel")
    method = next(node for node in original.body
                  if isinstance(node, ast.FunctionDef) and node.name == "predict")
    method.name = "baseline_predict"
    namespace = dict(vars(model_module))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                 f"{revision}:{SOURCE.as_posix()}", "exec"), namespace)
    return namespace["baseline_predict"], hashlib.sha256(source.encode()).hexdigest()


def batches():
    items = []
    for child in (False, True):
        for elapsed in (0., 25., 100., 500., 2500., 10000., 120000., 180001.):
            index = len(items)
            key = PrefillCandidateKey(
                f"r-{index}", "wf", f"agent-{index}", f"ctx-{index}", 1, 0, "s", 1,
            )
            features = LocalFrontierFeatures(
                f"agent-{index}", "wait_tool", agent_definition_id="general-purpose",
                tool_family="execute", backend_class="local", command_class="execute",
                observed_command_class="git", current_sequence_tokens=32768,
                boundary_history=("tool", "tool"), active_tool_count=1,
                backend_pressure="active_family:1", is_child=child,
                elapsed_wait_ms=elapsed, state_elapsed_ms=elapsed, llm_round=8,
                invocation_elapsed_ms=240000., generated_tokens=128,
            )
            items.append((key, features, 1000.))
    return {
        "single_root": tuple(items[:1]),
        "mixed_batch_16": tuple(items),
    }


def measure(model, original, items, *, iterations: int):
    baseline = SimpleNamespace(predict=original.__get__(model, FrontierBeliefModel))
    variants = {"baseline": baseline, "tool_only": model}
    for _, features, _ in items:
        if baseline.predict(features) != model.predict(features):
            raise AssertionError("full inference changed")
    expected = predict_tool_wait_batch(items, model=baseline)
    if predict_tool_wait_batch(items, model=model) != expected:
        raise AssertionError("tool hints changed")
    samples = {name: {"wall": [], "thread_cpu": []} for name in variants}
    for iteration in range(iterations + 5):
        names = list(variants)
        if iteration % 2:
            names.reverse()
        for name in names:
            wall_start = time.perf_counter_ns()
            cpu_start = time.thread_time_ns()
            result = predict_tool_wait_batch(items, model=variants[name])
            cpu_ms = (time.thread_time_ns() - cpu_start) / 1_000_000
            wall_ms = (time.perf_counter_ns() - wall_start) / 1_000_000
            if result != expected:
                raise AssertionError(f"changed hints in {name}, iteration {iteration}")
            if iteration >= 5:
                samples[name]["wall"].append(wall_ms)
                samples[name]["thread_cpu"].append(cpu_ms)
    costs = {
        name: {clock: distribution(values) for clock, values in clocks.items()}
        for name, clocks in samples.items()
    }
    return {
        "batch_size": len(items), "iterations": iterations,
        "full_predictions_equal": True, "tool_hints_equal": True,
        "returned_hint_count": len(expected),
        "inputs": [
            {"key": asdict(key), "features": asdict(features), "revision": revision}
            for key, features, revision in items
        ],
        "reply_sha256": hashlib.sha256(json.dumps(
            expected, default=lambda key: asdict(key), sort_keys=True, allow_nan=False,
        ).encode()).hexdigest(),
        "costs_per_batch": costs,
        "mean_reduction": {
            clock: 1 - costs["tool_only"][clock]["mean_ms"] / costs["baseline"][clock]["mean_ms"]
            for clock in ("wall", "thread_cpu")
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1:
        raise ValueError("positive iteration count required")
    imported = Path(model_module.__file__).resolve()
    if imported != ROOT / SOURCE:
        raise RuntimeError(f"imported a different worktree: {imported}")
    artifact = args.artifact.resolve()
    model = FrontierBeliefModel.load(artifact)
    original, original_digest = baseline_predict(args.baseline_revision)
    report = {
        "scope": (
            "Actual calibrated artifact, old and tool-only inference on the same loaded "
            "components, complete predict_tool_wait_batch with exact output equality and "
            "alternating measurements. Inputs are a synthetic elapsed-time matrix, not "
            "a workload frequency sample. Excludes worker IPC, queueing, scheduler, DMA "
            "and GPU performance; CPU savings do not prove throughput gain."
        ),
        "baseline_revision": args.baseline_revision,
        "baseline_source_sha256": original_digest,
        "optimized_source_sha256": hashlib.sha256(imported.read_bytes()).hexdigest(),
        "benchmark_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "worker_source_sha256": hashlib.sha256(
            (ROOT / "beliefkv/runtime/sglang_v0520_predictor_worker.py").read_bytes()
        ).hexdigest(),
        "artifact": str(artifact), "artifact_size": artifact.stat().st_size,
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "tool_feature_contract": model.tool_feature_contract,
        "cases": {
            name: measure(model, original, items, iterations=args.iterations)
            for name, items in batches().items()
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "artifact_sha256": report["artifact_sha256"],
        "cases": {
            name: {key: value for key, value in case.items() if key != "inputs"}
            for name, case in report["cases"].items()
        },
    }, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
