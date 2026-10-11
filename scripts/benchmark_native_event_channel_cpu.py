#!/usr/bin/env python3
"""Measure control-channel CPU costs and already-queued batch delivery."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.runtime.event_channel import (
    QueuedRuntimeEventSink,
    RuntimeEventDatagramServer,
    _RuntimeEventBatchRejection,
    _encode_event_message,
)
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


def measure_queued_case(
    baseline, *, pending_batches, text_chars, ack_delay_ms, samples,
):
    batches = tuple(
        tuple(RuntimeEvent(
            event_id=f"event-{index}-{offset}", workflow_id="fixture",
            kind=kind, ts_ms=float(index * 2 + offset),
            attributes={"text": "x" * text_chars},
        ) for offset, kind in enumerate((
            RuntimeEventKind.LLM_RESULT, RuntimeEventKind.LLM_SUBMIT,
        )))
        for index in range(pending_batches + 1)
    )
    variants = {"baseline": baseline, "updated": QueuedRuntimeEventSink}
    costs = {
        name: {"wall": [], "worker_thread_cpu": [], "queue_p95": []}
        for name in variants
    }
    receipts = {}

    class TransportFixture:
        def __init__(self, server):
            self.server = server
            self.first_started = threading.Event()
            self.first_release = threading.Event()
            self.message_count = 0
            self.payload_bytes = []

        def emit_batch(self, events):
            self.emit_batches((events,))

        def emit_batches(self, group):
            self.message_count += 1
            payload = _encode_event_message(f"{self.message_count:032x}", group)
            self.payload_bytes.append(len(payload))
            if self.message_count == 1:
                self.first_started.set()
                if not self.first_release.wait(timeout=5):
                    raise TimeoutError("benchmark did not release the first ACK")
            _, ack_bytes = self.server._handle(payload)
            ack = json.loads(ack_bytes)
            if ack_delay_ms:
                threading.Event().wait(ack_delay_ms / 1000)
            if ack["status"] != "accepted":
                raise _RuntimeEventBatchRejection(
                    ack["error"], ack.get("accepted_batch_count", 0),
                )

        def close(self):
            pass

    for iteration in range(samples + 2):
        order = list(variants)
        if iteration % 2:
            order.reverse()
        for name in order:
            applied = []

            class MeasuredQueue(variants[name]):
                def _deliver(self):
                    start = time.thread_time_ns()
                    try:
                        super()._deliver()
                    finally:
                        self.worker_thread_cpu_ms = (time.thread_time_ns() - start) / 1e6

            with tempfile.TemporaryDirectory(prefix="bkv-queued-cpu-") as temporary:
                with RuntimeEventDatagramServer(
                    Path(temporary) / "server.sock", applied.append,
                ) as server:
                    transport = TransportFixture(server)
                    queue = MeasuredQueue(transport, max_pending=max(64, pending_batches))
                    try:
                        first = queue.submit_batch(batches[0])
                        if not transport.first_started.wait(timeout=2):
                            raise TimeoutError("benchmark worker did not start")
                        began = time.perf_counter_ns()
                        submitted = [queue.submit_batch(batch) for batch in batches[1:]]
                        transport.first_release.set()
                        queue.flush()
                        wall_ms = (time.perf_counter_ns() - began) / 1e6
                        for delivery in (first, *submitted):
                            delivery.wait()
                    finally:
                        transport.first_release.set()
                        queue.close()
                    if applied != list(batches):
                        raise AssertionError("changed event order or original batch boundaries")
                    timing = queue.timing_summary()
                    receipts[name] = {
                        "transport_messages": transport.message_count,
                        "max_payload_bytes": max(transport.payload_bytes),
                        "applied_batches": len(applied),
                        "timing_summary": timing,
                    }
                    if iteration >= 2:
                        costs[name]["wall"].append(wall_ms)
                        costs[name]["worker_thread_cpu"].append(queue.worker_thread_cpu_ms)
                        costs[name]["queue_p95"].append(timing["reentry_queue_p95_ms"])
    return {
        "pending_batches": pending_batches, "events_per_batch": 2,
        "text_chars_per_event": text_chars, "simulated_ack_delay_ms": ack_delay_ms,
        "samples": samples, "batch_order_and_boundaries_equal": True,
        "costs": {
            name: {clock: distribution(values) for clock, values in clocks.items()}
            for name, clocks in costs.items()
        },
        "receipts": receipts,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", default="f57527f")
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--packets", type=int, default=64)
    parser.add_argument(
        "--queued-delivery", action="store_true",
        help="Compare delivery of already queued batches with simulated per-message ACK delay.",
    )
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
    started_at = datetime.now(timezone.utc).isoformat()
    if args.queued_delivery:
        report = {
            "scope": (
                "already-queued control delivery on CPU with fixture receiver and simulated "
                "ACK delay; no real socket syscall, GPU or throughput claim"
            ),
            "baseline_revision": args.baseline_revision,
            "baseline_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "updated_source_sha256": hashlib.sha256((ROOT / path).read_bytes()).hexdigest(),
            "benchmark_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "started_at_utc": started_at,
            "cases": [
                measure_queued_case(
                    module.QueuedRuntimeEventSink, pending_batches=count, text_chars=text,
                    ack_delay_ms=delay, samples=args.samples,
                )
                for count, text, delay in (
                    (1, 256, 0), (32, 256, 0), (4, 256, 20),
                    (32, 256, 20), (32, 2048, 20),
                )
            ],
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps([
            {
                key: case[key] for key in (
                    "pending_batches", "text_chars_per_event", "simulated_ack_delay_ms",
                    "costs", "receipts",
                )
            } for case in report["cases"]
        ], indent=2))
        return
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
