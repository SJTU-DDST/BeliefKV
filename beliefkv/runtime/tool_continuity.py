"""Bounded queue-age inheritance across observed short tool interruptions."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import math

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey


@dataclass
class _Segment:
    key: PrefillCandidateKey
    arrival: float
    rounds: int = 1
    finished_at: float | None = None
    tools: dict[str, float] = field(default_factory=dict)
    ended: set[str] = field(default_factory=set)
    first_tool_ms: float | None = None
    last_tool_ms: float | None = None
    candidate: PrefillCandidateKey | None = None


class ToolContinuationPolicy:
    """Spend earned workflow credit on at most four consecutive request rounds.

    The age is advisory ordering metadata, never a GPU-slot or KV reservation.
    Request wall time and output length are not treated as model service.
    """

    def __init__(self, *, short_gap_seconds: float = 1., max_rounds: int = 4):
        if (
            type(short_gap_seconds) not in (int, float)
            or not math.isfinite(short_gap_seconds) or short_gap_seconds <= 0
            or type(max_rounds) is not int or max_rounds < 2
        ):
            raise ValueError("invalid tool continuation bounds")
        self.short_gap_seconds = short_gap_seconds
        self.max_rounds = max_rounds
        self.segments: dict[tuple[str, str], _Segment] = {}
        self.ready: dict[tuple[str, str], _Segment] = {}
        self.credits: dict[str, int] = {}
        self.counts: Counter[str] = Counter()

    @staticmethod
    def identity(key: PrefillCandidateKey) -> tuple[str, str]:
        return key.root_workflow_id, key.invocation_id

    def discard(self, workflow: str, invocation: str) -> None:
        identity = workflow, invocation
        self.segments.pop(identity, None)
        self.ready.pop(identity, None)

    def clear(self) -> None:
        self.segments.clear()
        self.ready.clear()
        self.credits.clear()

    def retracted(self, key: PrefillCandidateKey) -> None:
        identity = self.identity(key)
        segment = self.segments.get(identity)
        self.ready.pop(identity, None)
        if segment is not None and segment.key == key:
            segment.rounds = self.max_rounds
            segment.tools.clear()
            segment.ended.clear()
            segment.candidate = None
        else:
            self.discard(*identity)

    def admitted(
        self, key: PrefillCandidateKey, *, arrival: float, promoted: bool,
    ) -> bool:
        identity = self.identity(key)
        prior = self.segments.get(identity)
        if prior is not None and prior.key == key:
            return False  # Chunked prefill and retraction do not earn new credit.
        inherited = self.inherited_arrival(key, arrival=arrival)
        continued = inherited is not None
        if promoted and not continued:
            raise ValueError("tool priority admission lost its continuation evidence")
        self.discard(*identity)
        self.segments[identity] = _Segment(
            key, inherited if continued else arrival,
            prior.rounds + 1 if continued else 1,
        )
        credit = self.credits.get(key.root_workflow_id, 0)
        self.credits[key.root_workflow_id] = (
            credit - 1 if promoted else self.max_rounds - 1
        )
        self.counts["continued_admissions" if continued else "ordinary_admissions"] += 1
        self.counts["priority_admissions"] += int(promoted)
        return True

    def completed(self, key: PrefillCandidateKey, *, now: float) -> None:
        segment = self.segments.get(self.identity(key))
        if segment is not None and segment.key == key and segment.finished_at is None:
            segment.finished_at = now
            self.counts["native_completions"] += 1
            if segment.rounds == self.max_rounds:
                self.discard(*self.identity(key))
                self.counts["segment_budget_yields"] += 1

    def observe(self, event: RuntimeEvent) -> None:
        if event.kind is Kind.WORKFLOW_END:
            for workflow, invocation in tuple(self.segments):
                if workflow == event.workflow_id:
                    self.discard(workflow, invocation)
            self.credits.pop(event.workflow_id, None)
            return
        identity = event.workflow_id, event.invocation_id
        segment = self.segments.get(identity)
        if segment is None:
            return
        if event.kind in (
            Kind.RETURN, Kind.INVOCATION_CANCEL, Kind.CONTEXT_COMPACT,
            Kind.CONTEXT_ADVANCE, Kind.JOIN_WAIT, Kind.CALL, Kind.SPAWN,
            Kind.HANDOFF,
        ) or event.kind is Kind.LLM_RESULT and event.attributes.get("terminal"):
            self.discard(*identity)
            return
        if event.kind is Kind.LLM_SUBMIT:
            if (
                event.attributes.get("runtime_internal")
                or event.attributes.get("request_id") != segment.key.request_id
                and (
                    event.context_epoch != segment.key.context_epoch + 1
                    or identity not in self.ready
                    or segment.candidate is not None
                    and event.attributes.get("request_id") != segment.candidate.request_id
                )
            ):
                self.discard(*identity)
            return
        if event.kind not in (Kind.TOOL_START, Kind.TOOL_END):
            return
        run = event.attributes.get("tool_run_id") or event.attributes.get("tool_call_id")
        if (
            type(run) is not str or not run or segment.finished_at is None
            or not math.isfinite(event.ts_ms)
            or event.context_id not in (None, segment.key.context_id)
            or event.context_epoch not in (None, segment.key.context_epoch)
        ):
            self.discard(*identity)
            self.counts["unproven_tool_rounds"] += 1
            return
        if event.kind is Kind.TOOL_START:
            if run in segment.tools or run in segment.ended:
                return
            self.ready.pop(identity, None)
            segment.candidate = None
            segment.tools[run] = event.ts_ms
            segment.first_tool_ms = (
                event.ts_ms if segment.first_tool_ms is None
                else min(segment.first_tool_ms, event.ts_ms)
            )
        else:
            if run in segment.ended:
                return
            start = segment.tools.pop(run, None)
            if start is None or event.ts_ms < start:
                self.discard(*identity)
                self.counts["unproven_tool_rounds"] += 1
                return
            segment.ended.add(run)
            segment.last_tool_ms = (
                event.ts_ms if segment.last_tool_ms is None
                else max(segment.last_tool_ms, event.ts_ms)
            )
            if (
                segment.first_tool_ms is None
                or segment.last_tool_ms - segment.first_tool_ms
                > self.short_gap_seconds * 1000.
            ):
                self.discard(*identity)
                self.counts["long_tool_yields"] += 1
            elif not segment.tools:
                self.ready[identity] = segment
                self.counts["short_tool_rounds"] += 1

    def inherited_arrival(
        self, key: PrefillCandidateKey, *, arrival: float,
    ) -> float | None:
        identity = self.identity(key)
        segment = self.ready.get(identity)
        if segment is None or not self.credits.get(key.root_workflow_id, 0):
            return None
        prior = segment.key
        if (
            segment.rounds >= self.max_rounds
            or key.request_id == prior.request_id
            or key.context_id != prior.context_id
            or key.context_epoch != prior.context_epoch + 1
            or key.session_id is None or key.session_generation is None
            or (key.session_id, key.session_generation)
            != (prior.session_id, prior.session_generation)
            or segment.candidate not in (None, key)
            or segment.finished_at is None
            or not 0 <= arrival - segment.finished_at <= self.short_gap_seconds
        ):
            return None
        if segment.candidate is None:
            segment.candidate = key
            self.counts["successor_candidates"] += 1
        return segment.arrival
