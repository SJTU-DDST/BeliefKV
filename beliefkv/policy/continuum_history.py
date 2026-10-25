"""Online observations for Continuum's public per-tool mean-gap estimator."""

from dataclasses import dataclass
import math


def _time(value: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("observed time must be finite nonnegative seconds")
    return float(value)


@dataclass
class _Departure:
    tool: str
    when: float


class ContinuumHistory:
    def __init__(self):
        self.departures: dict[str, _Departure] = {}
        self.tool_gap_sums: dict[str, float] = {}
        self.tool_gap_counts: dict[str, int] = {}

    def completed(self, program: str, *, tool: str | None, now: float) -> None:
        timestamp = _time(now)
        if type(program) is not str or not program:
            raise ValueError("observed program identity is required")
        if tool is not None and (type(tool) is not str or not tool):
            raise ValueError("tool identity must be observed or absent")
        if program in self.departures:
            raise ValueError("sequential program completed twice without reentry")
        if tool is not None:
            self.departures[program] = _Departure(tool, timestamp)

    def arrived(self, program: str, *, now: float) -> tuple[str, float] | None:
        timestamp = _time(now)
        previous = self.departures.get(program)
        if previous is None:
            return None
        gap = timestamp - previous.when
        if gap < 0:
            raise ValueError("reentry precedes its observed departure")
        del self.departures[program]
        self.tool_gap_sums[previous.tool] = self.tool_gap_sums.get(previous.tool, 0.) + gap
        self.tool_gap_counts[previous.tool] = self.tool_gap_counts.get(previous.tool, 0) + 1
        return previous.tool, gap

    def public_ttl(self, tool: str | None) -> float:
        if tool is None:
            return 0.
        if type(tool) is not str or not tool:
            raise ValueError("tool identity must be observed or absent")
        count = self.tool_gap_counts.get(tool, 0)
        return 0. if count and self.tool_gap_sums[tool] / count > 2. else 2.

    def forget(self, program: str) -> None:
        self.departures.pop(program, None)
