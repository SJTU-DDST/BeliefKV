#!/usr/bin/env python3
"""Replay sealed historical event prefixes through CPU control delivery."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent
from beliefkv.runtime.event_channel import (
    MAX_DATAGRAM_BYTES, RuntimeEventDatagramServer, _encode_event_message,
)
from beliefkv.runtime.hotpath_timing import HotpathTiming
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_shared_path import distribution

SOURCE = "beliefkv/runtime/event_channel.py"


def baseline_encoder(revision):
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{SOURCE}"], cwd=ROOT, text=True,
    )
    module = ModuleType("beliefkv.runtime._compact_wire_baseline")
    sys.modules[module.__name__] = module
    exec(compile(source, f"{revision}:{SOURCE}", "exec"), module.__dict__)
    return module._encode_event_message, hashlib.sha256(source.encode()).hexdigest()


def read_prefix(path, limit):
    digest = hashlib.sha256()
    size = 0
    events = []
    with path.open("rb") as stream:
        for _ in range(limit):
            line = stream.readline()
            if not line:
                break
            digest.update(line)
            size += len(line)
            events.append(RuntimeEvent.from_dict(json.loads(line)))
    if not events:
        raise ValueError(f"empty event prefix: {path}")
    return tuple(events), {
        "path": str(path.resolve()), "read_prefix_bytes": size,
        "read_prefix_sha256": digest.hexdigest(), "events": len(events),
        "event_kinds": dict(Counter(event.kind.value for event in events)),
    }


def measure(events, baseline, *, iterations, profiling):
    encoders = {"baseline": baseline, "candidate": _encode_event_message}
    samples = {
        name: {
            phase: {"wall_ms": [], "thread_cpu_ms": []}
            for phase in ("encode", "server", "combined")
        }
        for name in encoders
    }
    payload_sizes = {}
    warmup = 5
    with tempfile.TemporaryDirectory(prefix="bkv-wire-prefix-") as temporary:
        for iteration in range(iterations + warmup):
            names = list(encoders)
            if iteration % 2:
                names.reverse()
            outputs = {}
            for name in names:
                runtime = NativeAdmissionRuntime()
                runtime._hotpath_timing = HotpathTiming(enabled=profiling)
                timing = runtime._hotpath_timing
                phases = Counter()
                acks = []
                event_bytes = 0
                try:
                    with RuntimeEventDatagramServer(
                        Path(temporary) / f"{name}.sock", runtime.on_events, timing=timing,
                    ) as server:
                        for index, event in enumerate(events):
                            wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                            payload = encoders[name](str(index), ((event,),))
                            after_encode_cpu = time.thread_time_ns()
                            after_encode_wall = time.perf_counter_ns()
                            if len(payload) > MAX_DATAGRAM_BYTES:
                                raise ValueError(
                                    f"event {index} exceeds the existing datagram bound",
                                )
                            delivery, ack = server._handle(
                                payload, profiled=profiling and index % 64 == 0,
                            )
                            finish_cpu = time.thread_time_ns()
                            finish_wall = time.perf_counter_ns()
                            if not delivery.accepted:
                                raise AssertionError(f"historical event rejected: {delivery}")
                            event_bytes += len(payload)
                            acks.append(ack)
                            phases["encode_cpu"] += after_encode_cpu - cpu
                            phases["encode_wall"] += after_encode_wall - wall
                            phases["server_cpu"] += finish_cpu - after_encode_cpu
                            phases["server_wall"] += finish_wall - after_encode_wall
                            phases["combined_cpu"] += finish_cpu - cpu
                            phases["combined_wall"] += finish_wall - wall
                        if iteration >= warmup:
                            for phase in ("encode", "server", "combined"):
                                samples[name][phase]["thread_cpu_ms"].append(
                                    phases[f"{phase}_cpu"] / len(events) / 1_000_000.,
                                )
                                samples[name][phase]["wall_ms"].append(
                                    phases[f"{phase}_wall"] / len(events) / 1_000_000.,
                                )
                        outputs[name] = (
                            runtime.graph.snapshot(), dict(runtime.counts), acks,
                            {
                                phase: stats["count"] for phase, stats in
                                timing.snapshot()["phases"].items()
                            },
                        )
                        payload_sizes[name] = event_bytes
                        if runtime.physical_ledger.pending_count:
                            raise AssertionError("CPU event replay issued a physical command")
                finally:
                    runtime.close()
            if outputs["baseline"] != outputs["candidate"]:
                raise AssertionError("causal graph, counters, exact ACKs or timing counts changed")
    costs = {
        name: {
            phase: {
                field: distribution(values) for field, values in clocks.items()
            }
            for phase, clocks in phases.items()
        }
        for name, phases in samples.items()
    }
    return {
        "events": len(events), "iterations": iterations,
        "profiling_enabled": profiling, "payload_bytes": payload_sizes,
        "payload_reduction_fraction": 1. - payload_sizes["candidate"] / payload_sizes["baseline"],
        "graph_counters_exact_acks_and_timing_counts_equal": True,
        "costs_per_event": costs,
        "thread_cpu_mean_reduction": {
            phase: 1. - (
                costs["candidate"][phase]["thread_cpu_ms"]["mean_ms"]
                / costs["baseline"][phase]["thread_cpu_ms"]["mean_ms"]
            )
            for phase in ("encode", "server", "combined")
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--trace", type=Path, action="append", required=True)
    parser.add_argument("--prefix-events", type=int, default=128)
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.prefix_events, args.iterations) < 1 or args.output.exists():
        raise ValueError("positive bounds and a fresh report path are required")
    baseline, digest = baseline_encoder(args.baseline_revision)
    cases = []
    for path in args.trace:
        events, source = read_prefix(path, args.prefix_events)
        for profiling in (False, True):
            cases.append({
                "source": source,
                **measure(events, baseline, iterations=args.iterations, profiling=profiling),
            })
    paths = (
        SOURCE, "beliefkv/core/events.py", "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/hotpath_timing.py", "scripts/benchmark_compact_event_wire.py",
    )
    report = {
        "status": "same_event_prefix_cpu_control_replay_not_gpu_or_jct_acceptance",
        "observed_at_local": datetime.now().astimezone().isoformat(),
        "baseline_revision": args.baseline_revision, "baseline_encoder_sha256": digest,
        "candidate_sources": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths
        },
        "cases": cases,
        "limits": [
            "Each raw record is delivered as its own batch; original wire batching is not recorded.",
            "Both arms retain identical current runtime graph handling and parent timing.",
            "Server timing includes JSON decode, event validation, graph handling and exact ACK encoding.",
            "These are synchronous CPU handler calls; socket waiting, model work and native KV are absent.",
            "No field is removed from authoritative logs or decoded events; nested null values remain.",
            "Prefix hashes bind the sampled historical data; elapsed CPU is not GPU idle or JCT saving.",
            "The frozen V60, qualified d925278 candidate and reservation waiter remain unchanged.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([
        {
            "trace": Path(case["source"]["path"]).parent.name,
            **{field: case[field] for field in (
                "events", "profiling_enabled", "payload_reduction_fraction",
                "thread_cpu_mean_reduction",
            )},
        }
        for case in cases
    ], indent=2), flush=True)


if __name__ == "__main__":
    main()
