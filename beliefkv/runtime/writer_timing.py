"""Sample background evidence processing separately from queue waiting."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import time


@dataclass
class _WriterCost:
    samples: int = 0
    cpu_ns: int = 0
    wall_ns: int = 0
    max_cpu_ns: int = 0
    max_wall_ns: int = 0


class SampledWriterTiming:
    """Single-writer aggregates; snapshots contain measured samples only."""

    sample_every = 64

    def __init__(self) -> None:
        self._seen: Counter[str] = Counter()
        self._costs: dict[str, _WriterCost] = {}

    def begin(self, name: str) -> tuple[str, int, int] | None:
        self._seen[name] += 1
        if (self._seen[name] - 1) % self.sample_every:
            return None
        return name, time.thread_time_ns(), time.perf_counter_ns()

    def finish(self, sample: tuple[str, int, int] | None) -> None:
        if sample is None:
            return
        name, cpu_start, wall_start = sample
        cpu = time.thread_time_ns() - cpu_start
        wall = time.perf_counter_ns() - wall_start
        cost = self._costs.setdefault(name, _WriterCost())
        cost.samples += 1
        cost.cpu_ns += cpu
        cost.wall_ns += wall
        cost.max_cpu_ns = max(cost.max_cpu_ns, cpu)
        cost.max_wall_ns = max(cost.max_wall_ns, wall)

    def snapshot(self) -> dict:
        return {
            "schema_version": 1,
            "sample_every": self.sample_every,
            "snapshot_ts_ms": time.time() * 1000.,
            "cpu_clock": "thread_time_ns",
            "wall_clock": "perf_counter_ns",
            "scope": (
                "Every 64th record per category, starting with its first record. "
                "Measured processing samples only; queue waiting and status "
                "publication are excluded. Wall includes scheduling and GIL "
                "waiting; thread CPU excludes waiting. No full-run cost, "
                "GPU stall, GIL attribution or JCT saving is inferred."
            ),
            "categories": {
                name: {
                    "records_seen": self._seen[name],
                    "samples": cost.samples,
                    "sampled_cpu_ms": cost.cpu_ns / 1_000_000,
                    "sampled_wall_ms": cost.wall_ns / 1_000_000,
                    "mean_sample_cpu_ms": cost.cpu_ns / cost.samples / 1_000_000,
                    "mean_sample_wall_ms": cost.wall_ns / cost.samples / 1_000_000,
                    "max_sample_cpu_ms": cost.max_cpu_ns / 1_000_000,
                    "max_sample_wall_ms": cost.max_wall_ns / 1_000_000,
                }
                for name, cost in tuple(self._costs.items())
            },
        }
