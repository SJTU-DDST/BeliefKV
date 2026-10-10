#!/usr/bin/env python3
"""Measure sparse control profiling with identical packets and ACK bytes."""

from __future__ import annotations

import argparse
from dataclasses import asdict
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

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.runtime.event_channel import RuntimeEventDatagramServer
from beliefkv.runtime.hotpath_timing import HotpathTiming
from scripts.benchmark_native_shared_path import distribution


class SocketFixture:
    def reset(self, packets):
        self.packets = iter(packets)
        self.acks = []

    def recvfrom(self, _limit):
        try:
            return next(self.packets), "fixture-client"
        except StopIteration:
            raise BlockingIOError from None

    def sendto(self, ack, _reply_to):
        self.acks.append(ack)

    def close(self):
        pass


def measure_case(baseline, *, event_count, text_chars, samples, packet_count):
    packets = [
        json.dumps({
            "schema_version": 1, "message_id": f"packet-{packet}",
            "events": [RuntimeEvent(
                event_id=f"event-{packet}-{index}", workflow_id="fixture",
                kind=RuntimeEventKind.STRUCTURED_ACTION, ts_ms=float(packet),
                attributes={"semantic_text": "x" * text_chars},
            ).to_dict() for index in range(event_count)],
        }).encode()
        for packet in range(packet_count)
    ]
    costs = {
        name: {"wall": [], "thread_cpu": []}
        for name in ("baseline", "updated_without_profile", "updated_with_profile")
    }
    with tempfile.TemporaryDirectory(prefix="bkv-wire-cpu-") as temporary:
        variants = {}
        for index, name in enumerate(costs):
            delivered = []
            options = {"timing": HotpathTiming()} if name == "updated_with_profile" else {}
            cls = baseline if name == "baseline" else RuntimeEventDatagramServer
            server = cls(Path(temporary) / f"{index}.sock", delivered.extend, **options)
            server._socket.close()
            server._socket = SocketFixture()
            variants[name] = (server, delivered)
        try:
            for iteration in range(samples + 5):
                order = list(variants)
                if iteration % 2:
                    order.reverse()
                outputs = {}
                for name in order:
                    server, delivered = variants[name]
                    delivered.clear()
                    server._acks.clear()
                    server._socket.reset(packets)
                    wall_start, cpu_start = time.perf_counter_ns(), time.thread_time_ns()
                    deliveries = server.drain(max_messages=packet_count + 1)
                    cpu_ms = (time.thread_time_ns() - cpu_start) / 1_000_000 / packet_count
                    wall_ms = (time.perf_counter_ns() - wall_start) / 1_000_000 / packet_count
                    if len(delivered) != packet_count * event_count:
                        raise AssertionError("changed delivered event count")
                    outputs[name] = (
                        [asdict(item) for item in deliveries],
                        [event.to_dict() for event in delivered],
                        server._socket.acks,
                    )
                    if iteration >= 5:
                        costs[name]["wall"].append(wall_ms)
                        costs[name]["thread_cpu"].append(cpu_ms)
                if any(value != outputs["baseline"] for value in outputs.values()):
                    raise AssertionError("changed event delivery, ACK bytes or packet order")
            summaries = {
                name: {clock: distribution(values) for clock, values in clocks.items()}
                for name, clocks in costs.items()
            }
            profile = variants["updated_with_profile"][0]._timing.snapshot()
            return {
                "events_per_packet": event_count, "text_chars_per_event": text_chars,
                "max_packet_bytes": max(map(len, packets)),
                "packets_per_sample": packet_count, "samples": samples,
                "delivery_ack_bytes_and_order_equal": True,
                "costs_per_packet": summaries,
                "sparse_profile": profile,
                "mean_overhead_us": {
                    name: {
                        clock: 1000 * (
                            summaries[name][clock]["mean_ms"]
                            - summaries["baseline"][clock]["mean_ms"]
                        )
                        for clock in ("wall", "thread_cpu")
                    }
                    for name in summaries if name != "baseline"
                },
            }
        finally:
            for server, _ in variants.values():
                server.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", default="f57527f")
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--packets", type=int, default=64)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or not 1 <= args.packets <= 256:
        parser.error("positive samples and 1..256 packets required")
    path = "beliefkv/runtime/event_channel.py"
    source = subprocess.check_output(
        ["git", "show", f"{args.baseline_revision}:{path}"], cwd=ROOT, text=True,
    )
    module = ModuleType("beliefkv.runtime._event_channel_baseline")
    sys.modules[module.__name__] = module
    exec(compile(source, f"{args.baseline_revision}:{path}", "exec"), vars(module))
    report = {
        "scope": "receiver codec and drain on CPU socket fixtures; no real socket syscall, GPU or throughput claim",
        "baseline_revision": args.baseline_revision,
        "baseline_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "updated_source_sha256": hashlib.sha256((ROOT / path).read_bytes()).hexdigest(),
        "benchmark_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cases": [
            measure_case(
                module.RuntimeEventDatagramServer, event_count=count, text_chars=text,
                samples=args.samples, packet_count=args.packets,
            )
            for count, text in ((1, 256), (32, 256))
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "cases": [
            {key: case[key] for key in (
                "events_per_packet", "max_packet_bytes",
                "delivery_ack_bytes_and_order_equal", "mean_overhead_us",
            )}
            for case in report["cases"]
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
