"""Sample scheduler phases and compute-stream spans without a CUDA barrier."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import math
import time

from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain


def _clock() -> tuple[int, int]:
    return time.monotonic_ns(), time.thread_time_ns()


@dataclass
class _Sample:
    batch_id: int
    metadata: dict
    phases: dict[str, tuple[tuple[int, int], tuple[int, int]]] = field(default_factory=dict)
    gpu_events: tuple | None = None
    gpu_status: str = "unavailable"
    processed: bool = False


class PrefillTurnoverProbe:
    """Keep one in 16 prefills, with no request/tensor ownership or synchronization."""

    def __init__(self, emit, *, every: int = 16, limit: int = 32) -> None:
        if every < 1 or limit < 1:
            raise ValueError("positive sampling interval and queue bound required")
        self.emit = emit
        self.every, self.limit = every, limit
        self.counts: Counter = Counter()
        self._selected: _Sample | None = None
        self._samples: dict[int, _Sample] = {}

    def begin_selection(self) -> tuple[int, int]:
        return _clock()

    def on_selected(self, batch, started, *, loop: str) -> None:
        if self._selected is not None:
            self.counts["selected_without_launch"] += 1
            self._selected = None
        if batch is None or not batch.forward_mode.is_extend():
            return
        self.counts["eligible_prefills"] += 1
        if (self.counts["eligible_prefills"] - 1) % self.every:
            return
        if len(self._samples) >= self.limit:
            self.counts["bounded_queue_skips"] += 1
            return
        ended = _clock()
        prefix = getattr(batch, "prefix_lens", None)
        self._selected = _Sample(id(batch), {
            "batch_size": len(batch.reqs),
            "request_ids": tuple(req.rid for req in batch.reqs),
            "extend_tokens": getattr(batch, "extend_num_tokens", None),
            "prefix_tokens": (
                sum(prefix) if isinstance(prefix, (list, tuple))
                and all(type(value) is int for value in prefix) else None
            ),
            "loop": loop,
            "sampling_interval": self.every,
        }, {"selection": (started, ended)})
        self.counts["selected_samples"] += 1

    def on_launch(self, batch) -> None:
        sample = self._selected
        if sample is None:
            return
        self._selected = None
        if (
            sample.batch_id != id(batch)
            or sample.metadata["request_ids"] != tuple(req.rid for req in batch.reqs)
            or not batch.forward_mode.is_extend()
            or type(batch.forward_iter) is not int
            or batch.forward_iter in self._samples
        ):
            self.counts["launch_identity_unknown"] += 1
            return
        sample.metadata.update({
            "forward_iter": batch.forward_iter,
            "launch_monotonic_ms": batch.launch_ts * 1000.,
        })
        self._samples[batch.forward_iter] = sample

    def begin_phase(self, batch, name: str):
        sample = self._selected
        if sample is None or sample.batch_id != id(batch):
            sample = self._samples.get(getattr(batch, "forward_iter", None))
        if sample is None:
            return None
        if sample.metadata["request_ids"] != tuple(req.rid for req in batch.reqs):
            self.counts["phase_identity_unknown"] += 1
            return None
        return sample, name, _clock()

    def end_phase(self, handle) -> None:
        if handle is None:
            return
        sample, name, started = handle
        sample.phases[name] = (started, _clock())

    def begin_forward(self, batch, device, stream):
        if self._selected is None or self._selected.batch_id != id(batch):
            return None
        handle = self.begin_phase(batch, "run_batch")
        if handle is None:
            return None
        sample = handle[0]
        try:
            if device is None:
                return handle
            if stream is None:
                stream = device.current_stream()
                sample.metadata["gpu_stream_scope"] = "current_stream"
            else:
                sample.metadata["gpu_stream_scope"] = "explicit_forward_stream"
            if device.is_current_stream_capturing():
                sample.gpu_status = "capturing"
                return handle
            start, finish = (
                device.Event(enable_timing=True), device.Event(enable_timing=True),
            )
            start.record(stream=stream)
            sample.gpu_events = start, finish, stream
            sample.gpu_status = "started"
        except (AttributeError, TypeError, ValueError, RuntimeError):
            sample.gpu_status = "unavailable"
            self.counts["gpu_event_unavailable"] += 1
        return handle

    def end_forward(self, handle) -> None:
        if handle is None:
            return
        sample = handle[0]
        if sample.gpu_events is not None:
            try:
                _, finish, stream = sample.gpu_events
                finish.record(stream=stream)
                sample.gpu_status = "pending"
            except (AttributeError, TypeError, ValueError, RuntimeError):
                sample.gpu_status = "unavailable"
                sample.gpu_events = None
                self.counts["gpu_event_unavailable"] += 1
        self.end_phase(handle)

    def on_processed(self, handle, result) -> None:
        if handle is None:
            return
        self.end_phase(handle)
        sample = handle[0]
        sample.processed = True
        sample.metadata["cuda_graph_replayed"] = getattr(result, "can_run_cuda_graph", None)
        self.poll()

    def _record(self, sample: _Sample, gpu_ms: float | None, *, complete: bool) -> None:
        phases = {
            name: {
                "start_monotonic_ms": started[0] / 1e6,
                "end_monotonic_ms": ended[0] / 1e6,
                "wall_ms": (ended[0] - started[0]) / 1e6,
                "thread_cpu_ms": (ended[1] - started[1]) / 1e6,
            }
            for name, (started, ended) in sample.phases.items()
        }
        run, result = phases.get("run_batch"), phases.get("result_processing")
        self.emit("audit", {
            "event": "prefill_turnover_sample" if complete else "prefill_turnover_incomplete",
            "ts_ms": time.time() * 1000.,
            **sample.metadata, "phases": phases,
            "monotonic_clock_domain": local_monotonic_clock_domain(),
            "gpu_run_batch_span_ms": gpu_ms,
            "gpu_span_status": sample.gpu_status,
            "result_processed": sample.processed,
            "selection_end_to_run_start_ms": (
                run["start_monotonic_ms"] - phases["selection"]["end_monotonic_ms"]
                if run is not None else None
            ),
            "run_return_to_result_start_ms": (
                result["start_monotonic_ms"] - run["end_monotonic_ms"]
                if result is not None and run is not None else None
            ),
            "semantics": (
                "Selection ends after native batch preparation. run_batch includes "
                "telemetry and CPU forward submission; GPU events enclose compute-stream "
                "work, dependency waits and dispatch gaps, not exclusive kernels. "
                "Result processing includes native callbacks and existing telemetry. "
                "Timing-event overhead is included. Phase wall minus thread CPU does "
                "not identify GIL interference. Missing phases remain unknown."
            ),
        })

    def poll(self) -> None:
        for forward, sample in tuple(self._samples.items()):
            if not sample.processed:
                continue
            gpu_ms = None
            if sample.gpu_events is not None:
                start, finish, _ = sample.gpu_events
                try:
                    if not finish.query():
                        continue
                    gpu_ms = float(start.elapsed_time(finish))
                    if not math.isfinite(gpu_ms) or gpu_ms < 0:
                        gpu_ms = None
                        sample.gpu_status = "invalid"
                    else:
                        sample.gpu_status = "observed"
                except (AttributeError, TypeError, ValueError, RuntimeError):
                    sample.gpu_status = "unavailable"
                    self.counts["gpu_event_unavailable"] += 1
            self._record(sample, gpu_ms, complete=True)
            del self._samples[forward]
            self.counts["completed_samples"] += 1

    def close(self) -> None:
        self.poll()
        for sample in self._samples.values():
            self._record(sample, None, complete=False)
        self.counts["shutdown_incomplete_samples"] += len(self._samples)
        self._samples.clear()
        if self._selected is not None:
            self.counts["selected_without_launch"] += 1
            self._selected = None

    def snapshot(self) -> dict:
        return {
            "sampling_interval": self.every, "queue_limit": self.limit,
            "pending_samples": len(self._samples),
            "counts": dict(self.counts),
        }
