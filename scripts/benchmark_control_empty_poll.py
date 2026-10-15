#!/usr/bin/env python3
"""Compare empty and busy control drains using real CPU-only UNIX datagrams."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import gc
import hashlib
import json
from pathlib import Path
import random
import socket
import subprocess
import sys
import tempfile
import time
import tracemalloc
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.runtime.event_channel import RuntimeEventDatagramServer, _encode_event_message
from beliefkv.runtime.hotpath_timing import HotpathTiming
from scripts.benchmark_native_shared_path import distribution


def baseline_server(revision: str):
    path = "beliefkv/runtime/event_channel.py"
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{path}"], cwd=ROOT, text=True,
    )
    module = ModuleType("beliefkv.runtime._control_poll_baseline")
    sys.modules[module.__name__] = module
    exec(compile(source, f"{revision}:{path}", "exec"), vars(module))
    return module.RuntimeEventDatagramServer, hashlib.sha256(source.encode()).hexdigest()


def idle_allocation(server_class) -> dict:
    peaks = {}
    with tempfile.TemporaryDirectory(prefix="bkv-control-alloc-") as temporary:
        for name, cls in (
            ("baseline", server_class), ("candidate", RuntimeEventDatagramServer),
        ):
            with cls(Path(temporary) / f"{name}.sock", lambda _: None) as server:
                server.drain()
                values = []
                tracemalloc.start()
                try:
                    for _ in range(16):
                        before, _ = tracemalloc.get_traced_memory()
                        tracemalloc.reset_peak()
                        server.drain()
                        _, peak = tracemalloc.get_traced_memory()
                        values.append(max(0, peak - before))
                finally:
                    tracemalloc.stop()
                peaks[name] = values
    return {
        "scope": "Separate unprofiled empty drains; tracemalloc is off during CPU comparisons.",
        "peak_extra_bytes_per_call": peaks,
    }


def benchmark(server_class, *, iterations: int, profiling: bool, kind: str) -> dict:
    samples = {name: {"thread_cpu_ms": [], "wall_ms": []}
               for name in ("baseline", "candidate")}
    observations = {name: [] for name in samples}
    handler_counts = {name: 0 for name in samples}
    variants = {}
    warmup = 32
    order = random.Random(19)
    active = [None]
    collections = {name: {"count": 0, "thread_cpu_ns": 0} for name in samples}
    gc_start = {}

    def observe_gc(phase, info):
        if phase == "start":
            if active[0] is not None:
                gc_start[info["generation"]] = active[0], time.thread_time_ns()
        else:
            started = gc_start.pop(info["generation"], None)
            if started is not None:
                name, cpu = started
                collections[name]["count"] += 1
                collections[name]["thread_cpu_ns"] += time.thread_time_ns() - cpu

    gc.callbacks.append(observe_gc)
    try:
        with tempfile.TemporaryDirectory(prefix="bkv-control-cpu-") as temporary:
            for name, cls in (
                ("baseline", server_class), ("candidate", RuntimeEventDatagramServer),
            ):
                timing = HotpathTiming(enabled=profiling)

                def handle(events, *, name=name):
                    if events[0].event_id.endswith("-reject"):
                        raise ValueError("fixture rejected")
                    observations[name].extend(events)
                    handler_counts[name] += len(events)

                server = cls(Path(temporary) / f"{name}.sock", handle, timing=timing)
                sender = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                sender.bind(str(Path(temporary) / f"{name}-client.sock"))
                sender.settimeout(1.)
                variants[name] = server, sender, timing
            for iteration in range(iterations + warmup):
                size = (
                    8 if kind == "burst" else
                    2 if kind == "duplicate_rejection" else
                    1 if kind == "one_message" or (
                        kind == "sparse" and iteration % 10 == 0
                    ) else 0
                )
                payloads = []
                for index in range(size):
                    identity = f"{iteration}-{index}"
                    if kind == "duplicate_rejection":
                        identity = f"{iteration}-reject"
                    payloads.append(_encode_event_message(identity, ((
                        RuntimeEvent(
                            identity, float(iteration), RuntimeEventKind.WORKFLOW_START, "wf",
                        ),
                    ),)))
                names = list(variants)
                if order.getrandbits(1):
                    names.reverse()
                outputs = {}
                for name in names:
                    server, sender, timing = variants[name]
                    for payload in payloads:
                        sender.sendto(payload, str(server.path))
                    active[0] = name
                    wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                    with timing.measure("control_drain"):
                        deliveries = server.drain(max_messages=16)
                    cpu_elapsed = time.thread_time_ns() - cpu
                    wall_elapsed = time.perf_counter_ns() - wall
                    active[0] = None
                    cost = {
                        "thread_cpu_ms": cpu_elapsed / 1_000_000.,
                        "wall_ms": wall_elapsed / 1_000_000.,
                    }
                    if iteration >= warmup:
                        for metric, value in cost.items():
                            samples[name][metric].append(value)
                    outputs[name] = (
                        [asdict(item) for item in deliveries],
                        [sender.recv(60_000) for _ in deliveries],
                        tuple(observations[name]),
                    )
                    observations[name].clear()
                if outputs["baseline"] != outputs["candidate"]:
                    raise AssertionError("delivery, rejection, exact ACK bytes or handler order changed")
            if handler_counts["baseline"] != handler_counts["candidate"]:
                raise AssertionError("handler event count changed")
            if variants["baseline"][0]._receive_polls != variants["candidate"][0]._receive_polls:
                raise AssertionError("receive profiling cadence changed")
            costs = {
                name: {metric: distribution(values) for metric, values in clocks.items()}
                for name, clocks in samples.items()
            }
            return {
                "kind": kind, "profiling_enabled": profiling,
                "iterations": iterations, "warmup_iterations": warmup,
                "costs": costs,
                "thread_cpu_mean_reduction_fraction": 1. - (
                    costs["candidate"]["thread_cpu_ms"]["mean_ms"]
                    / costs["baseline"]["thread_cpu_ms"]["mean_ms"]
                ),
                "exact_delivery_ack_and_handler_order_equal": True,
                "receive_profiling_cadence_equal": True,
                "handler_event_count": handler_counts["candidate"],
                "order_random_seed": 19,
                "gc_inside_timed_calls_including_warmup": collections,
                "timings": {
                    name: timing.snapshot() for name, (_, _, timing) in variants.items()
                },
            }
    finally:
        gc.callbacks.remove(observe_gc)
        for server, sender, _ in variants.values():
            sender.close()
            server.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1 or args.output.exists():
        raise ValueError("positive iterations and a new evidence path are required")
    paths = (
        "beliefkv/runtime/event_channel.py", "beliefkv/runtime/hotpath_timing.py",
        "beliefkv/core/events.py", "scripts/benchmark_control_empty_poll.py",
    )
    sources = {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths
    }
    baseline, baseline_sha = baseline_server(args.baseline_revision)
    cases = [
        benchmark(baseline, iterations=args.iterations, profiling=profiling, kind=kind)
        for profiling in (False, True)
        for kind in ("idle", "sparse", "one_message", "burst", "duplicate_rejection")
    ]
    allocations = idle_allocation(baseline)
    if any(
        hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest
        for path, digest in sources.items()
    ):
        raise ValueError("measured source changed during comparison")
    report = {
        "status": "control_poll_cpu_comparison_not_gpu_or_jct_acceptance",
        "observed_at": datetime.now().astimezone().isoformat(),
        "baseline_revision": args.baseline_revision,
        "baseline_event_channel_sha256": baseline_sha,
        "candidate_sources": sources, "cases": cases,
        "idle_allocation": allocations,
        "limitations": [
            "Real UNIX receive/ACK I/O and deterministic handler; no GPU or full scheduler measurement.",
            "Deterministic randomized variant order, identical datagrams and exact ACK/handler checks.",
            "Sparse traffic is one message per ten drains, a scenario rather than a replay of V59.",
            "Thread CPU is separate from wall time; historical wall sums are not expected JCT savings.",
            "The existing receive-sample cadence and full parent control_drain timing remain enabled.",
            "GC remains enabled; collections inside timed calls are recorded and remain in reported costs.",
            "Handler observations are compared and released after each drain, without a growing artificial history.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([
        {key: case[key] for key in (
            "kind", "profiling_enabled", "thread_cpu_mean_reduction_fraction",
        )} for case in cases
    ], indent=2), flush=True)


if __name__ == "__main__":
    main()
