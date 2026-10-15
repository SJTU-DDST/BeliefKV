"""Sample scheduler phases and compute-stream spans without a CUDA barrier."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import math
import os
from threading import get_native_id
import time

from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain


def _clock() -> tuple[int, int]:
    return time.monotonic_ns(), time.thread_time_ns()


def _phase_records(phases):
    return {
        name: {
            "start_monotonic_ms": started[0] / 1e6,
            "end_monotonic_ms": ended[0] / 1e6,
            "start_thread_cpu_ns": started[1],
            "end_thread_cpu_ns": ended[1],
            "wall_ms": (ended[0] - started[0]) / 1e6,
            "thread_cpu_ms": (ended[1] - started[1]) / 1e6,
        }
        for name, (started, ended) in phases.items()
    }


def _adjacent_attribution(sample):
    run = sample.phases.get("run_batch")
    observed = sample.successor_observed
    if run is None or observed is None:
        return {"status": "run_return_or_successor_unobserved"}
    if sample.adjacent_metadata.get("predecessor_status") != "observed":
        return {"status": "predecessor_identity_unknown"}
    stages = {
        name: values.get(name) for values, name in (
            (sample.phases, "war_barrier_and_queue_publish"),
            (sample.adjacent_phases, "predecessor_result_processing"),
            (sample.phases, "post_launch_physical"),
            (sample.adjacent_phases, "successor_selection"),
        )
    }
    missing = [name for name, span in stages.items() if span is None]
    if missing:
        return {"status": "missing_phases", "missing": missing}
    begin, cursor, previous = run[1], run[1], "run_return"
    segments, wall, cpu = [], 0, 0
    for name, (started, ended) in sorted(stages.items(), key=lambda item: item[1][0][0]):
        if any(
            not cursor[index] <= started[index] <= ended[index] <= observed[index]
            for index in (0, 1)
        ):
            return {"status": "invalid_or_overlapping_clock_order"}
        segments.extend((
            {
                "kind": "between_phases", "from": previous, "to": name,
                "wall_ms": (started[0] - cursor[0]) / 1e6,
                "thread_cpu_ms": (started[1] - cursor[1]) / 1e6,
            },
            {
                "kind": "phase", "name": name,
                "wall_ms": (ended[0] - started[0]) / 1e6,
                "thread_cpu_ms": (ended[1] - started[1]) / 1e6,
            },
        ))
        wall += ended[0] - started[0]
        cpu += ended[1] - started[1]
        cursor, previous = ended, name
    segments.append({
        "kind": "between_phases", "from": previous, "to": "successor_launch_observer",
        "wall_ms": (observed[0] - cursor[0]) / 1e6,
        "thread_cpu_ms": (observed[1] - cursor[1]) / 1e6,
    })
    return {
        "status": "observed",
        "wall_ms": (observed[0] - begin[0]) / 1e6,
        "thread_cpu_ms": (observed[1] - begin[1]) / 1e6,
        "measured_phase_wall_ms": wall / 1e6,
        "measured_phase_thread_cpu_ms": cpu / 1e6,
        "between_phases_wall_ms": (observed[0] - begin[0] - wall) / 1e6,
        "between_phases_thread_cpu_ms": (observed[1] - begin[1] - cpu) / 1e6,
        "segments": segments,
    }


@dataclass
class _Sample:
    batch_id: int
    metadata: dict
    phases: dict[str, tuple[tuple[int, int], tuple[int, int]]] = field(default_factory=dict)
    gpu_events: tuple | None = None
    gpu_status: str = "unavailable"
    processed: bool = False
    adjacent_phases: dict = field(default_factory=dict)
    adjacent_metadata: dict = field(default_factory=dict)
    successor_batch_id: int | None = None
    successor_observed: tuple[int, int] | None = None


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
        self._adjacent: _Sample | None = None
        self._thread_cpu_clock_domain = f"{os.getpid()}:{get_native_id()}:thread_time_ns"

    def begin_selection(self) -> tuple[int, int]:
        return _clock()

    def on_selected(self, batch, started, *, loop: str) -> None:
        adjacent = self._adjacent
        if adjacent is not None:
            if "successor_selection" in adjacent.adjacent_phases:
                adjacent.adjacent_metadata["successor_status"] = "ambiguous_selection"
                self.counts["adjacent_selection_ambiguous"] += 1
            else:
                adjacent.adjacent_phases["successor_selection"] = (started, _clock())
                adjacent.successor_batch_id = id(batch) if batch is not None else None
                adjacent.adjacent_metadata.update({
                    "successor_status": "selected" if batch is not None else "no_batch",
                    "successor_request_ids": (
                        tuple(req.rid for req in batch.reqs) if batch is not None else ()
                    ),
                    "successor_selection_loop": loop,
                })
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
        if self._adjacent is not None:
            self._observe_successor(batch)
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

    def _observe_successor(self, batch) -> None:
        sample, self._adjacent = self._adjacent, None
        observed = _clock()
        if (
            sample.adjacent_metadata.get("successor_status") != "selected"
            or sample.successor_batch_id != id(batch)
            or type(batch.forward_iter) is not int
            or batch.forward_iter != sample.metadata["forward_iter"] + 1
            or sample.adjacent_metadata["successor_request_ids"] != tuple(req.rid for req in batch.reqs)
        ):
            sample.adjacent_metadata["successor_status"] = "identity_or_order_unknown"
            self.counts["adjacent_successor_unknown"] += 1
            return
        sample.successor_observed = observed
        sample.adjacent_metadata.update({
            "successor_status": "observed",
            "successor_forward_iter": batch.forward_iter,
            "successor_launch_monotonic_ms": batch.launch_ts * 1000.,
        })

    def begin_phase(self, batch, name: str):
        sample = self._selected
        if sample is None or sample.batch_id != id(batch):
            sample = self._samples.get(getattr(batch, "forward_iter", None))
        if sample is not None and sample.metadata["request_ids"] != tuple(req.rid for req in batch.reqs):
            self.counts["phase_identity_unknown"] += 1
            return None
        adjacent = self._adjacent
        if (
            name == "result_processing" and adjacent is not None
            and type(getattr(batch, "forward_iter", None)) is int
            and batch.forward_iter == adjacent.metadata["forward_iter"] - 1
        ):
            if "predecessor" in adjacent.adjacent_metadata:
                adjacent.adjacent_metadata["predecessor_status"] = "ambiguous"
                self.counts["adjacent_predecessor_ambiguous"] += 1
                adjacent = None
            else:
                adjacent.adjacent_metadata.update({
                    "predecessor_status": "observed",
                    "predecessor": {
                        "forward_iter": batch.forward_iter,
                        "request_ids": tuple(req.rid for req in batch.reqs),
                        "batch_size": len(batch.reqs),
                    },
                })
        else:
            adjacent = None
        if sample is None:
            return (
                (adjacent, "predecessor_result_processing", _clock())
                if adjacent is not None else None
            )
        return sample, name, _clock(), adjacent

    def end_phase(self, handle) -> None:
        if handle is None:
            return
        sample, name, started = handle[:3]
        span = started, _clock()
        if name == "predecessor_result_processing":
            sample.adjacent_phases[name] = span
        else:
            sample.phases[name] = span
            if len(handle) == 4 and handle[3] is not None:
                handle[3].adjacent_phases["predecessor_result_processing"] = span

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
        if (
            sample.metadata["loop"] == "overlap"
            and self._samples.get(sample.metadata.get("forward_iter")) is sample
        ):
            self._adjacent = sample

    def on_processed(self, handle, result) -> None:
        if handle is None:
            return
        self.end_phase(handle)
        if handle[1] == "predecessor_result_processing":
            return
        sample = handle[0]
        sample.processed = True
        if self._adjacent is sample:
            self._adjacent = None
            sample.adjacent_metadata["successor_status"] = "not_observed_before_result"
        sample.metadata["cuda_graph_replayed"] = getattr(result, "can_run_cuda_graph", None)
        self.poll()

    def _record(self, sample: _Sample, gpu_ms: float | None, *, complete: bool) -> None:
        phases = _phase_records(sample.phases)
        run, result = phases.get("run_batch"), phases.get("result_processing")
        transition = {
            **sample.adjacent_metadata,
            "phases": _phase_records(sample.adjacent_phases),
            "successor_observer_monotonic_ms": (
                sample.successor_observed[0] / 1e6
                if sample.successor_observed is not None else None
            ),
            "successor_observer_thread_cpu_ns": (
                sample.successor_observed[1]
                if sample.successor_observed is not None else None
            ),
            "attribution": _adjacent_attribution(sample),
            "boundary_semantics": (
                "Sampled prefill run_batch return to the exact successor's "
                "launch-telemetry observer, not CUDA launch. Predecessor result "
                "processing and successor selection retain their own identities. "
                "Absolute thread CPU endpoints belong only to this scheduler thread. "
                "Between-phase CPU is measured, not proportionally allocated. "
                "Callback identities still require comparison with native service rows. "
                "Wall minus thread CPU does not identify GPU idle or GIL interference."
            ),
        }
        self.emit("audit", {
            "event": "prefill_turnover_sample" if complete else "prefill_turnover_incomplete",
            "ts_ms": time.time() * 1000.,
            **sample.metadata, "phases": phases,
            "adjacent_transition": transition,
            "monotonic_clock_domain": local_monotonic_clock_domain(),
            "thread_cpu_clock_domain": self._thread_cpu_clock_domain,
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
        self._adjacent = None
        if self._selected is not None:
            self.counts["selected_without_launch"] += 1
            self._selected = None

    def snapshot(self) -> dict:
        return {
            "sampling_interval": self.every, "queue_limit": self.limit,
            "pending_samples": len(self._samples),
            "counts": dict(self.counts),
        }
