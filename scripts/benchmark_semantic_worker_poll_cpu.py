#!/usr/bin/env python3
"""Compare semantic result polling over real bounded multiprocessing queues."""

from __future__ import annotations

import argparse
import ast
from collections import OrderedDict
from dataclasses import asdict
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import select
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [str(ROOT), *(item for item in sys.path if item != str(ROOT))]

from beliefkv.runtime import semantic_report_worker as worker_module
from beliefkv.runtime.semantic_report_worker import (
    SemanticReportInput, SemanticReportReply, SemanticReportWorker,
)
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from scripts.benchmark_native_shared_path import distribution


def echo_worker(inputs, outputs, release):
    outputs.put(("ready", ()))
    while (items := inputs.get()) is not None:
        release.wait()
        outputs.put(("result", tuple(
            SemanticReportReply(item, .9, 10., 20., 40., 0.) for item in items
        )))


def baseline_worker(revision):
    path = "beliefkv/runtime/semantic_report_worker.py"
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{path}"], cwd=ROOT, text=True,
    )
    tree = ast.parse(source)
    original = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == "SemanticReportWorker")
    original.name = "BaselineWorker"
    original.bases = [ast.Name(id="SemanticReportWorker", ctx=ast.Load())]
    original.body = [node for node in original.body
                     if isinstance(node, ast.FunctionDef) and node.name == "poll"]
    namespace = dict(vars(worker_module))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[original], type_ignores=[])),
                 f"{revision}:{path}", "exec"), namespace)
    return namespace["BaselineWorker"], hashlib.sha256(source.encode()).hexdigest()


def fixture(worker_class):
    context = mp.get_context("spawn")
    worker = object.__new__(worker_class)
    worker._inputs = context.Queue(maxsize=1)
    worker._outputs = context.Queue(maxsize=2)
    worker._result_poller = select.poll()
    worker._result_poller.register(worker._outputs._reader, select.POLLIN)
    release = context.Event()
    release.set()
    worker._process = context.Process(
        target=echo_worker, args=(worker._inputs, worker._outputs, release), daemon=True,
    )
    worker._pending = OrderedDict()
    worker._active = False
    worker._started = time.monotonic()
    worker.ready = False
    worker.disabled = False
    worker.error = ""
    worker.dropped = 0
    worker._process.start()
    if not select.select([worker.fileno()], [], [], 20)[0]:
        worker.close()
        raise RuntimeError("echo worker startup timed out")
    worker.poll()
    if not worker.ready or worker.disabled:
        worker.close()
        raise RuntimeError("echo worker failed")
    return worker, release


def queue_batch(worker, sequence):
    items = tuple(SemanticReportInput(
        PrefillCandidateKey(f"r-{index}", "wf", f"child-{index}", f"ctx-{index}", 1, 0, "s", 1),
        float(sequence * 100 + index), 20 + sequence, 128 + sequence,
        f"Report section {sequence}.", bool(index % 2), 100, 1, 1,
    ) for index in range(4))
    worker._pending.update((item.key.request_id, item) for item in items)
    worker._dispatch()
    return items


def measure(baseline, *, scenario, iterations):
    variants = {
        "baseline": fixture(baseline),
        "optimized": fixture(SemanticReportWorker),
    }
    samples = {name: {"wall": [], "thread_cpu": []} for name in variants}
    evidence = []
    try:
        if scenario == "inflight_empty":
            for worker, release in variants.values():
                release.clear()
                queue_batch(worker, 0)
        for iteration in range(iterations + 5):
            names = list(variants)
            if iteration % 2:
                names.reverse()
            replies = {}
            for name in names:
                worker, _ = variants[name]
                if scenario == "result_ready":
                    queue_batch(worker, iteration)
                    if not select.select([worker.fileno()], [], [], 5)[0]:
                        raise RuntimeError("echo result timed out")
                worker._started = time.monotonic()
                wall_started = time.perf_counter_ns()
                cpu_started = time.thread_time_ns()
                replies[name] = worker.poll()
                cpu_ms = (time.thread_time_ns() - cpu_started) / 1_000_000
                wall_ms = (time.perf_counter_ns() - wall_started) / 1_000_000
                if worker.disabled:
                    raise RuntimeError(worker.error)
                if worker._active != (scenario == "inflight_empty"):
                    raise AssertionError("changed inference lifecycle")
                if iteration >= 5:
                    samples[name]["wall"].append(wall_ms)
                    samples[name]["thread_cpu"].append(cpu_ms)
            if replies["baseline"] != replies["optimized"]:
                raise AssertionError("changed result or input")
            if scenario == "result_ready" and len(replies["optimized"]) != 4:
                raise AssertionError("lost result")
            if iteration >= 5 and scenario == "result_ready":
                evidence.append([asdict(reply) for reply in replies["optimized"]])
        costs = {
            name: {clock: distribution(values) for clock, values in clocks.items()}
            for name, clocks in samples.items()
        }
        return {
            "scenario": scenario, "iterations": iterations,
            "inputs_replies_and_lifecycle_equal": True,
            "reply_sha256": hashlib.sha256(json.dumps(
                evidence, sort_keys=True, allow_nan=False,
            ).encode()).hexdigest(),
            "costs": costs,
            "mean_reduction": {
                clock: 1 - costs["optimized"][clock]["mean_ms"] / costs["baseline"][clock]["mean_ms"]
                for clock in ("wall", "thread_cpu")
            },
        }
    finally:
        for worker, release in variants.values():
            release.set()
            worker.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--iterations", type=int, default=5000)
    parser.add_argument("--result-iterations", type=int, default=300)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1 or args.result_iterations < 1:
        raise ValueError("positive iteration counts required")
    path = Path(worker_module.__file__).resolve()
    if path != ROOT / "beliefkv/runtime/semantic_report_worker.py":
        raise RuntimeError(f"imported a different worktree: {path}")
    baseline, baseline_digest = baseline_worker(args.baseline_revision)
    report = {
        "scope": (
            "real spawned CPU processes, bounded multiprocessing queues and the actual "
            "parent worker poll/dispatch; deterministic inference replies; excludes "
            "neural inference, DMA, GPU stalls and end-to-end performance"
        ),
        "baseline_revision": args.baseline_revision,
        "baseline_source_sha256": baseline_digest,
        "optimized_source": str(path),
        "optimized_source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "benchmark_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cases": [
            measure(baseline, scenario=scenario,
                    iterations=args.result_iterations if scenario == "result_ready" else args.iterations)
            for scenario in ("idle_empty", "inflight_empty", "result_ready")
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
