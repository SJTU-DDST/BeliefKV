"""Sample actual GPU-stream layer dependency waits without synchronizing CUDA."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
import time


@dataclass
class _Sample:
    metadata: dict
    device: object
    counts: Counter
    layers: dict = field(default_factory=dict)

    def wait(self, loading_event, layer: int, consumer: int) -> None:
        if layer in self.layers or self.device.is_current_stream_capturing():
            self.counts["duplicate_or_captured_wait"] += 1
            loading_event.wait(layer)
            return
        stream = self.device.current_stream()
        start = self.device.Event(enable_timing=True)
        finish = self.device.Event(enable_timing=True)
        cpu_start = time.monotonic()
        start.record(stream=stream)
        loading_event.wait(layer)
        finish.record(stream=stream)
        self.layers[layer] = (consumer, start, finish, cpu_start, time.monotonic())


class RestoreWaitProbe:
    """One in 16 load-consuming prefills; bounded event pairs, never a barrier."""

    def __init__(self, *, every: int = 16, limit: int = 32) -> None:
        if every < 1 or limit < 1:
            raise ValueError("positive sampling interval and queue bound required")
        self.every = every
        self.limit = limit
        self.counts: Counter = Counter()
        self._pending: deque[_Sample] = deque()

    def begin(self, batch, device) -> _Sample | None:
        if not batch.forward_mode.is_extend() or getattr(batch, "hicache_consumer_index", -1) < 0:
            return None
        self.counts["eligible_batches"] += 1
        if (self.counts["eligible_batches"] - 1) % self.every:
            return None
        if len(self._pending) >= self.limit:
            self.counts["bounded_queue_skips"] += 1
            return None
        sample = _Sample({
            "event": "gpu_restore_dependency_wait",
            "ts_ms": time.time() * 1000.,
            "forward_iter": batch.forward_iter,
            "request_ids": [req.rid for req in batch.reqs],
            "batch_size": len(batch.reqs),
            "hicache_consumer_index": batch.hicache_consumer_index,
            "sampling_interval": self.every,
            "semantics": (
                "CUDA compute-stream events around native per-layer load waits; "
                "includes event overhead; sampled batch stall, not per-request "
                "queue wait or an oracle end-to-end speedup"
            ),
        }, device, self.counts)
        self._pending.append(sample)
        self.counts["sampled_batches"] += 1
        return sample

    def poll(self) -> list[dict]:
        records = []
        retained = deque()
        while self._pending:
            sample = self._pending.popleft()
            if not sample.layers:
                self.counts["samples_without_python_layer_wait"] += 1
                continue
            if not all(pair[2].query() for pair in sample.layers.values()):
                retained.append(sample)
                continue
            layers = [{
                "layer": layer, "consumer_index": consumer,
                "gpu_wait_ms": max(0., start.elapsed_time(finish)),
                "cpu_wait_enqueue_ms": max(0., (cpu_finish - cpu_start) * 1000.),
            } for layer, (consumer, start, finish, cpu_start, cpu_finish)
                in sample.layers.items()]
            first, last = next(iter(sample.layers.values())), next(
                reversed(sample.layers.values()),
            )
            records.append({
                **sample.metadata, "layers": layers,
                "gpu_layer_dependency_wait_ms": sum(row["gpu_wait_ms"] for row in layers),
                "gpu_layer_dependency_wait_max_ms": max(row["gpu_wait_ms"] for row in layers),
                "gpu_observed_layer_span_ms": max(0., first[1].elapsed_time(last[2])),
                "cpu_observed_layer_span_ms": max(0., (last[4] - first[3]) * 1000.),
                "cpu_layer_wait_enqueue_ms": sum(row["cpu_wait_enqueue_ms"] for row in layers),
                "span_semantics": (
                    "first observed layer-wait start through last observed wait end; "
                    "GPU span includes kernels and CPU submission gaps between layers; "
                    "CPU span includes dispatch between waits; neither is a complete "
                    "forward or exclusive kernel duration"
                ),
            })
            self.counts["completed_samples"] += 1
        self._pending = retained
        return records

    def snapshot(self) -> dict:
        return {
            "sampling_interval": self.every, "queue_limit": self.limit,
            "pending_samples": len(self._pending), "counts": dict(self.counts),
        }
