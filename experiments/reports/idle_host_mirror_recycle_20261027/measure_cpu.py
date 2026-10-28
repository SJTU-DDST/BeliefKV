"""Measure the whole idle callback using qualified native CPU fixtures."""

from collections import OrderedDict
import json
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.v0520_opportunity_telemetry import NativeOpportunityTelemetry
from tests.test_full_only_native_lifecycle import full_native
from tests.test_idle_host_mirror_recycle import mirror_runtime


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve the original callback measurement")
    manifest_path = output.parent / "shared_cpu.sources.json"
    qualified = json.loads(manifest_path.read_bytes())
    paths = (
        "beliefkv/runtime/idle_host_mirror_recycle.py",
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/v0520_opportunity_telemetry.py",
        "tests/test_idle_host_mirror_recycle.py",
        "tests/test_full_only_native_lifecycle.py",
    )
    sources = {name: sha256_file(ROOT / name) for name in paths}
    if any(sources[name] != qualified[name] for name in paths):
        raise ValueError("measured callback differs from shared CPU qualification")
    results = {}
    for label in (
        "disabled", "existing_host_free", "backoff", "no_cold",
        "bounded_no_cold", "locked_mirror", "successful_prepare",
    ):
        writer = NativeOpportunityTelemetry(output.parent / "cpu_journals" / label)
        samples, checks = [], 0
        try:
            for index in range(550):
                with pytest.MonkeyPatch.context() as patch:
                    native = full_native.__wrapped__(patch, NS(param=64))
                    fixture = mirror_runtime.__wrapped__(
                        native, patch, NS(param=label != "disabled"),
                    )
                    runtime, cache, allocator, cold, hot, writes, host_slots = next(fixture)
                    runtime._opportunity_writer = writer
                    try:
                        if label == "existing_host_free":
                            host_slots["free"] = 8
                        elif label == "backoff":
                            runtime._idle_host_mirror_recycler.next_ms = float("inf")
                        elif label == "no_cold":
                            runtime.graph.invocations["tool"].state = InvocationState.READY
                        elif label == "bounded_no_cold":
                            stale = [(1000 + i, NS(
                                id=1000 + i, creation_time=0.,
                                component_data={0: NS(value=[1], host_value=None)},
                            )) for i in range(32)]
                            cache._beliefkv_unbacked_full = OrderedDict(
                                [*stale, (cold.id, cold)],
                            )
                        elif label == "locked_mirror":
                            hot.component_data[0].host_lock_ref = 1
                        started = time.thread_time_ns()
                        runtime.dispatch_idle_prepare()
                        elapsed = (time.thread_time_ns() - started) / 1000.
                        if allocator.available_size() != 0:
                            raise ValueError("callback unexpectedly released Device capacity")
                        expected_copy = label in ("existing_host_free", "successful_prepare")
                        if bool(writes) != expected_copy:
                            raise ValueError("callback did not exercise its declared path")
                        if label == "bounded_no_cold" and runtime.counts[
                            "idle_host_mirror_recycle_cold_checks"
                        ] != 32:
                            raise ValueError("bounded rejection fixture did not scan 32 nodes")
                        checks += runtime.counts["idle_host_mirror_recycle_checks"]
                        if index >= 50:
                            samples.append(elapsed)
                    finally:
                        runtime._opportunity_writer = None
                        fixture.close()
            results[label] = {
                "samples": len(samples), "warmup": 50,
                "cold_probe_rounds_including_warmup": checks,
                "whole_idle_callback_thread_cpu_us": {
                    "median": statistics.median(samples),
                    "p99": statistics.quantiles(samples, n=100, method="inclusive")[98],
                    "max": max(samples),
                },
            }
        finally:
            writer.close()
        status = json.loads(writer.status_path.read_bytes())
        if not status["complete"] or status["error"] is not None:
            raise ValueError("CPU callback journal did not drain")
        counts = status["enqueued"]
        if counts.get("idle_host_mirror_recycled", 0) != (
            550 if label == "successful_prepare" else 0
        ) or counts.get("idle_cold_full_prepare", 0) != (
            550 if label in ("existing_host_free", "successful_prepare") else 0
        ):
            raise ValueError("journal does not retain every expected callback action")
        results[label].update(
            journal_enqueued=counts, journal_sha256=sha256_file(writer.path),
            journal_status_sha256=sha256_file(writer.status_path),
        )
    if any(sha256_file(ROOT / name) != digest for name, digest in sources.items()):
        raise ValueError("measured sources changed")
    result = {
        "fixtures": results, "sources_sha256": sources,
        "qualified_manifest_sha256": sha256_file(manifest_path),
        "operator_sha256": sha256_file(Path(__file__)),
        "scope": "Whole dispatch_idle_prepare callback thread CPU with real native CPU tree/Device allocator.",
        "limitations": [
            "Fixture construction, mutation and destruction occur outside timing.",
            "Host slots, copies and GPU events are CPU stand-ins; the idle batch is scaled to four tokens.",
            "The successful fixture has one cold target and one mirror; it is not the maximum mirror window.",
            "The bounded rejection fixture checks 32 stale dirty nodes, not a worst-case shared ownership subtree.",
            "A real async journal is enabled; its background thread CPU is excluded.",
            "No GPU interference, numerical model check, complete JCT or throughput gain is measured.",
        ],
        "performance_accepted": False,
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
