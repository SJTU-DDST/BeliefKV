"""Bounded scheduler-thread CPU timings; no per-call I/O or CUDA synchronization."""

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
    buckets: list[int] = field(
        default_factory=lambda: [0] * (len(BUCKET_UPPER_NS) + 1)
    )


@dataclass
class _Frame:
    name: str
    start_ns: int
    child_ns: int = 0


class HotpathTiming:
    """Inclusive and exclusive aggregates for nested, single-threaded scopes."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self._costs: dict[str, _Cost] = {}
        self._stack: list[_Frame] = []
        self._start_ns = time.perf_counter_ns()

    def begin(self, name: str) -> _Frame:
        frame = _Frame(name, time.perf_counter_ns())
        self._stack.append(frame)
        return frame

    def finish(self, frame: _Frame) -> None:
        elapsed = time.perf_counter_ns() - frame.start_ns
        assert self._stack.pop() is frame
        if self._stack:
            self._stack[-1].child_ns += elapsed
        cost = self._costs.get(frame.name)
        if cost is None:
            cost = self._costs[frame.name] = _Cost()
        cost.count += 1
        cost.total_ns += elapsed
        cost.self_ns += max(0, elapsed - frame.child_ns)
        cost.max_ns = max(cost.max_ns, elapsed)
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
            "schema_version": 1,
            "enabled": self.enabled,
            "clock": "perf_counter_ns; CPU wall intervals, not GPU kernel time",
            "scope": "cumulative completed calls; exclusive totals avoid nested double counting",
            "elapsed_ms": (time.perf_counter_ns() - self._start_ns) / 1_000_000,
            "bucket_upper_ms": [value / 1_000_000 for value in BUCKET_UPPER_NS] + [None],
            "phases": {
                name: {
                    "count": cost.count,
                    "total_ms": cost.total_ns / 1_000_000,
                    "self_ms": cost.self_ns / 1_000_000,
                    "mean_ms": cost.total_ns / cost.count / 1_000_000,
                    "max_ms": cost.max_ns / 1_000_000,
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
