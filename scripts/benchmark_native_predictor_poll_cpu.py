#!/usr/bin/env python3
"""Compare native predictor polling with real spawned bounded queues."""

from __future__ import annotations

import argparse
import ast
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
sys.path[:] = [str(ROOT), *(entry for entry in sys.path if entry != str(ROOT))]

from beliefkv.runtime import sglang_v0520_predictor_worker as worker_module
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_predictor_worker import NativePredictorWorker
from scripts.benchmark_native_shared_path import distribution


def echo_worker(inputs, outputs, release):
    outputs.put("ready")
    while True:
        request = inputs.get()
        release.wait()
        sequence, *payload = request
        if len(payload) == 1:
            outputs.put((sequence, tuple(
                (key, 128 + index, revision)
                for index, (key, _, revision) in enumerate(payload[0])
            )))
        else:
            kind, items = payload
            if kind == "tool_wait":
                values = tuple((key, 10., 20., 40., revision)
                               for key, _, revision in items)
            else:
                values = tuple(
                    (key, revision, join, mode, members,
                     tuple((child, rev, "running", epoch)
                           for child, _, rev, epoch in children), 10., 20., 40.)
                    for key, revision, join, mode, members, _, children in items
                )
            outputs.put((sequence, kind, values))


def baseline_worker(revision):
    path = "beliefkv/runtime/sglang_v0520_predictor_worker.py"
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{path}"], cwd=ROOT, text=True,
    )
    tree = ast.parse(source)
    original = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                    and node.name == "NativePredictorWorker")
    original.name = "BaselineWorker"
    original.bases = [ast.Name(id="NativePredictorWorker", ctx=ast.Load())]
    original.body = [node for node in original.body
                     if isinstance(node, ast.FunctionDef) and node.name == "poll"]
    namespace = dict(vars(worker_module))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[original], type_ignores=[])),
                 f"{revision}:{path}", "exec"), namespace)
    return namespace["BaselineWorker"], hashlib.sha256(source.encode()).hexdigest()


def fixture(worker_class):
    context = mp.get_context("spawn")
    worker = object.__new__(worker_class)
    worker.predictor_sha256 = "a" * 64
    worker._timeout_s = 10.
    worker._input_queue = context.Queue(maxsize=1)
    worker._output_queue = context.Queue(maxsize=1)
    worker._result_poller = select.poll()
    worker._result_poller.register(worker._output_queue._reader, select.POLLIN)
    release = context.Event()
    release.set()
    worker._process = context.Process(
        target=echo_worker, args=(worker._input_queue, worker._output_queue, release),
        daemon=True,
    )
    worker._active_sequence = None
    worker._active_kind = None
    worker._started_at = None
    worker._latest_sequence = 0
    worker._pending = {"tool_wait": {}, "join_wait": {}, "admission": {}}
    worker._last_wait_kind = "join_wait"
    worker._wait_streak = 0
    worker.failure_count = 0
    worker.disabled = False
    worker._closed = False
    worker._process.start()
    if not select.select([worker.fileno()], [], [], 20)[0]:
        worker.close()
        raise RuntimeError("echo worker startup timed out")
    if worker._output_queue.get_nowait() != "ready":
        worker.close()
        raise RuntimeError("unexpected echo worker handshake")
    return worker, release


def queue_batch(worker, sequence):
    kind = ("admission", "tool_wait", "join_wait")[sequence % 3]
    keys = tuple(PrefillCandidateKey(
        f"r-{index}", "wf", f"agent-{index}", f"ctx-{index}", 1, 0, "s", 1,
    ) for index in range(4))
    if kind == "join_wait":
        worker.submit_join_wait(tuple(
            (key, float(sequence), f"join-{index}", "all", ("child",), (),
             (("child", None, float(sequence), 1),))
            for index, key in enumerate(keys)
        ))
    else:
        items = tuple((key, None, float(sequence)) for key in keys)
        (worker.submit if kind == "admission" else worker.submit_tool_wait)(items)


def comparable(replies):
    result = []
    for reply in replies:
        value = asdict(reply)
        issued = value.pop("issued_monotonic_ms")
        expires = value.pop("expires_monotonic_ms")
        if abs(expires - issued - 5000.) > .001:
            raise AssertionError("changed result expiry")
        result.append(value)
    return result


def lifecycle(worker):
    return (
        worker._active_sequence, worker._active_kind, worker._latest_sequence,
        worker._pending, worker._last_wait_kind, worker._wait_streak,
        worker.failure_count, worker.disabled, worker._closed,
    )


def measure(baseline, *, scenario, iterations):
    variants = {
        "baseline": fixture(baseline),
        "optimized": fixture(NativePredictorWorker),
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
            replies, states = {}, {}
            for name in names:
                worker, _ = variants[name]
                if scenario == "result_ready":
                    queue_batch(worker, iteration)
                    if not select.select([worker.fileno()], [], [], 5)[0]:
                        raise RuntimeError("echo result timed out")
                if worker._active_sequence is not None:
                    worker._started_at = time.monotonic()
                wall_started = time.perf_counter_ns()
                cpu_started = time.thread_time_ns()
                output = worker.poll()
                cpu_ms = (time.thread_time_ns() - cpu_started) / 1_000_000
                wall_ms = (time.perf_counter_ns() - wall_started) / 1_000_000
                if worker.disabled:
                    raise RuntimeError("worker failed")
                if (worker._active_sequence is not None) != (scenario == "inflight_empty"):
                    raise AssertionError("unexpected inference lifecycle")
                replies[name], states[name] = comparable(output), lifecycle(worker)
                if iteration >= 5:
                    samples[name]["wall"].append(wall_ms)
                    samples[name]["thread_cpu"].append(cpu_ms)
            if replies["baseline"] != replies["optimized"] or states["baseline"] != states["optimized"]:
                raise AssertionError("changed result, input or lifecycle")
            if scenario == "result_ready" and len(replies["optimized"]) != 4:
                raise AssertionError("lost result")
            if iteration >= 5 and scenario == "result_ready":
                evidence.append(replies["optimized"])
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
                clock: 1 - costs["optimized"][clock]["mean_ms"]
                / costs["baseline"][clock]["mean_ms"]
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
    if path != ROOT / "beliefkv/runtime/sglang_v0520_predictor_worker.py":
        raise RuntimeError(f"imported a different worktree: {path}")
    baseline, baseline_digest = baseline_worker(args.baseline_revision)
    report = {
        "scope": (
            "real spawned CPU processes and bounded queues, actual parent worker "
            "poll/dispatch and deterministic admission/tool/JOIN replies; excludes "
            "model inference, DMA, GPU stalls and end-to-end performance"
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
