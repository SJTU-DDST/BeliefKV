"""CPU reference for program service inheritance, not an Agentix scheduler port.

The caller supplies measured LLM executor service. External waits, token counts,
MLFQ quanta, running-call preemption and anti-starvation are outside this core.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import math
from typing import Mapping, Sequence


def _service(value: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("finite nonnegative executor service required")
    return float(value)


@dataclass(frozen=True)
class ProgramCall:
    program_id: str
    inherited_service_ms: float


class ProgramServiceReference:
    def __init__(self, mode: str):
        if mode not in ("plas", "atlas"):
            raise ValueError("service inheritance must be plas or atlas")
        self.mode = mode
        self.service_ms: dict[str, float] = {}
        self.calls: dict[str, ProgramCall] = {}

    def arrive(self, call_id: str, program_id: str) -> ProgramCall:
        if type(call_id) is not str or type(program_id) is not str or not call_id or not program_id or call_id in self.calls:
            raise ValueError("unique call and nonempty program identities required")
        call = ProgramCall(program_id, self.service_ms.get(program_id, 0.))
        self.service_ms.setdefault(program_id, 0.)
        self.calls[call_id] = call
        return call

    def complete(self, call_id: str, *, executor_service_ms: float) -> float:
        elapsed = _service(executor_service_ms)
        call = self.calls[call_id]
        current = self.service_ms[call.program_id]
        updated = (
            current + elapsed if self.mode == "plas"
            else max(current, call.inherited_service_ms + elapsed)
        )
        if not math.isfinite(updated):
            raise ValueError("program service overflow")
        self.service_ms[call.program_id] = updated
        del self.calls[call_id]
        return updated

    def forget(self, program_id: str) -> None:
        if any(call.program_id == program_id for call in self.calls.values()):
            raise ValueError("cannot retire a program with outstanding calls")
        self.service_ms.pop(program_id, None)

    def order_ready(
        self, ready: Sequence[str], *, band_edges_ms: Sequence[float],
        exposed_restore_ms: Mapping[str, float] | None = None,
    ) -> list[str]:
        """Explore setup costs only inside a complete, measured service band.

        This does not grant capacity, alter running requests, or infer future
        tool durations. Missing restore evidence preserves FIFO in that band.
        """
        edges = tuple(_service(edge) for edge in band_edges_ms)
        if (edges and edges[0] == 0) or any(a >= b for a, b in zip(edges, edges[1:])):
            raise ValueError("strictly increasing positive service band edges required")
        if len(set(ready)) != len(ready):
            raise ValueError("duplicate ready call")
        bands: dict[int, list[str]] = {}
        for identity in ready:
            call = self.calls[identity]
            band = bisect_right(edges, call.inherited_service_ms)
            bands.setdefault(band, []).append(identity)
        result = []
        for band in sorted(bands):
            calls = bands[band]
            if exposed_restore_ms is not None and all(
                identity in exposed_restore_ms for identity in calls
            ):
                costs = {identity: _service(exposed_restore_ms[identity]) for identity in calls}
                calls = sorted(calls, key=costs.__getitem__)
            result.extend(calls)
        return result
