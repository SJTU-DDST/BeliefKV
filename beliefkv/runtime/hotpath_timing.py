"""Bounded scheduler-thread CPU/wall samples without I/O or CUDA synchronization."""

from __future__ import annotations

from bisect import bisect_left
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps
import time


BUCKET_UPPER_NS = (
    10_000, 25_000, 50_000, 100_000, 250_000, 500_000,
    1_000_000, 2_500_000, 5_000_000, 10_000_000, 25_000_000,
    50_000_000, 100_000_000, 250_000_000, 1_000_000_000,
)


@dataclass
class _Cost:
    count: int = 0
    total_ns: int = 0
    self_ns: int = 0
    max_ns: int = 0
    total_cpu_ns: int = 0
    self_cpu_ns: int = 0
    max_cpu_ns: int = 0
    buckets: list[int] = field(
        default_factory=lambda: [0] * (len(BUCKET_UPPER_NS) + 1)
    )


@dataclass
class _Frame:
    name: str
    start_ns: int
    start_cpu_ns: int = 0
    child_ns: int = 0
    child_cpu_ns: int = 0
    sampled: bool = True


class HotpathTiming:
    """Inclusive and exclusive aggregates for nested, single-threaded scopes."""

    def __init__(self, *, enabled: bool = True, sample_every: int = 1) -> None:
        if type(sample_every) is not int or sample_every < 1:
            raise ValueError("sample_every must be a positive integer")
        self.enabled = enabled
        self.sample_every = sample_every
        self._costs: dict[str, _Cost] = {}
        self._calls: dict[str, int] = {}
        self._root_calls: dict[str, int] = {}
        self._stack: list[_Frame] = []
        self._start_ns = time.perf_counter_ns()

    def begin(self, name: str) -> _Frame:
        self._calls[name] = self._calls.get(name, 0) + 1
        if self._stack:
            sampled = self._stack[-1].sampled
        else:
            roots = self._root_calls[name] = self._root_calls.get(name, 0) + 1
            sampled = (roots - 1) % self.sample_every == 0
        # Sample complete nesting trees so exclusive time never includes an
        # unmeasured child within a measured parent.
        frame = _Frame(
            name, time.perf_counter_ns() if sampled else 0,
            time.thread_time_ns() if sampled else 0, sampled=sampled,
        )
        self._stack.append(frame)
        return frame

    def finish(self, frame: _Frame) -> None:
        cpu = time.thread_time_ns() - frame.start_cpu_ns if frame.sampled else 0
        elapsed = time.perf_counter_ns() - frame.start_ns if frame.sampled else 0
        assert self._stack.pop() is frame
        if not frame.sampled:
            return
        if self._stack:
            self._stack[-1].child_ns += elapsed
            self._stack[-1].child_cpu_ns += cpu
        cost = self._costs.get(frame.name)
        if cost is None:
            cost = self._costs[frame.name] = _Cost()
        cost.count += 1
        cost.total_ns += elapsed
        cost.self_ns += max(0, elapsed - frame.child_ns)
        cost.max_ns = max(cost.max_ns, elapsed)
        cost.total_cpu_ns += cpu
        cost.self_cpu_ns += max(0, cpu - frame.child_cpu_ns)
        cost.max_cpu_ns = max(cost.max_cpu_ns, cpu)
        cost.buckets[bisect_left(BUCKET_UPPER_NS, elapsed)] += 1

    @contextmanager
    def measure(self, name: str):
        if not self.enabled:
            yield
            return
        frame = self.begin(name)
        try:
            yield
        finally:
            self.finish(frame)

    def snapshot(self) -> dict:
        return {
            "schema_version": 2,
            "enabled": self.enabled,
            "clock": "perf_counter_ns; CPU wall intervals, not GPU kernel time",
            "cpu_clock": "thread_time_ns",
            "sample_every": self.sample_every,
            "sampling": "every Nth root call per phase, starting with the first; descendants inherit",
            "scope": (
                "Measured completed samples only; exclusive values subtract measured children. "
                "No expansion to whole-run cost, exposed GPU idle or removable JCT."
            ),
            "elapsed_ms": (time.perf_counter_ns() - self._start_ns) / 1_000_000,
            "bucket_upper_ms": [value / 1_000_000 for value in BUCKET_UPPER_NS] + [None],
            "phases": {
                name: {
                    "count": cost.count,
                    "observed_calls": self._calls[name],
                    "root_calls": self._root_calls.get(name, 0),
                    "total_ms": cost.total_ns / 1_000_000,
                    "self_ms": cost.self_ns / 1_000_000,
                    "mean_ms": cost.total_ns / cost.count / 1_000_000,
                    "max_ms": cost.max_ns / 1_000_000,
                    "thread_cpu_total_ms": cost.total_cpu_ns / 1_000_000,
                    "thread_cpu_self_ms": cost.self_cpu_ns / 1_000_000,
                    "thread_cpu_mean_ms": cost.total_cpu_ns / cost.count / 1_000_000,
                    "thread_cpu_max_ms": cost.max_cpu_ns / 1_000_000,
                    "histogram": list(cost.buckets),
                }
                for name, cost in self._costs.items()
            },
        }


def timed_runtime(name: str):
    def decorate(function):
        @wraps(function)
        def measured(self, *args, **kwargs):
            timing = self._hotpath_timing
            if not timing.enabled:
                return function(self, *args, **kwargs)
            frame = timing.begin(name)
            try:
                return function(self, *args, **kwargs)
            finally:
                timing.finish(frame)

        return measured

    return decorate
