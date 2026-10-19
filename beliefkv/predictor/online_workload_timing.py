"""Causal timing adaptation scoped to one live workload collection."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
import math
from statistics import median

from beliefkv.core.events import RuntimeEventKind
from beliefkv.predictor.tool_survival import KaplanMeierCurve


@dataclass(frozen=True)
class OnlineToolEstimate:
    p10_ms: float
    p50_ms: float
    p90_ms: float
    release_cdf: tuple[tuple[float, float], ...]
    samples: int
    scope: str


class OnlineWorkloadToolTiming:
    def __init__(self, *, window: int = 64, max_keys: int = 256) -> None:
        self.window, self.max_keys = window, max_keys
        self.open: dict[tuple, tuple[tuple, float]] = {}
        self.samples: OrderedDict[tuple, deque] = OrderedDict()
        self._curves: dict[tuple, KaplanMeierCurve] = {}
        self.revision = 0

    def observe(self, event) -> None:
        attrs = event.attributes
        identity = (
            event.workflow_id, event.invocation_id,
            str(attrs.get("tool_run_id") or attrs.get("tool_call_id") or "_legacy"),
        )
        if event.kind is RuntimeEventKind.TOOL_START:
            name = attrs.get("tool_name", "")
            if name in {"announce_completion_intent", "ChildCompletion", "WorkflowCompletion"}:
                return
            command = attrs.get("observed_command_shape")
            if not command or command == "unknown":
                command = attrs.get("observed_command_class") or name
            signature = (str(attrs.get("backend_class") or "unknown"), str(command))
            self.open.setdefault(identity, (signature, event.ts_ms))
        elif event.kind is RuntimeEventKind.TOOL_END:
            started = self.open.pop(identity, None)
            if started is None:
                return
            signature, start = started
            duration = attrs.get("duration_ms", event.ts_ms - start)
            if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
                return
            completed = not (
                attrs.get("censored") is True or attrs.get("tool_error_class") == "timeout"
            )
            for key in ((event.workflow_id, *signature), ("*", *signature)):
                history = self.samples.setdefault(key, deque(maxlen=self.window))
                history.append((float(duration), completed))
                self.samples.move_to_end(key)
                self._curves.pop(key, None)
            while len(self.samples) > self.max_keys:
                key, _ = self.samples.popitem(last=False)
                self._curves.pop(key, None)
            self.revision += 1
        elif event.kind in (
            RuntimeEventKind.RETURN, RuntimeEventKind.INVOCATION_CANCEL,
            RuntimeEventKind.WORKFLOW_END,
        ):
            self.open = {
                key: value for key, value in self.open.items()
                if key[0] != event.workflow_id or (
                    event.kind is not RuntimeEventKind.WORKFLOW_END
                    and key[1] != event.invocation_id
                )
            }

    def estimate(self, workflow: str, invocation: str, *, now_ms: float) -> OnlineToolEstimate | None:
        active = [
            value for key, value in self.open.items()
            if key[:2] == (workflow, invocation)
        ]
        # A parallel group's completion is a maximum, not one tool's duration.
        if len(active) != 1:
            return None
        signature, started = active[0]
        elapsed = max(0., now_ms - started)
        for prefix, minimum, scope in ((workflow, 4, "workflow"), ("*", 8, "run")):
            key = (prefix, *signature)
            history = self.samples.get(key)
            if history is None or sum(completed for _, completed in history) < minimum:
                continue
            curve = self._curves.get(key)
            if curve is None:
                curve = self._curves[key] = KaplanMeierCurve().fit(list(history))
            values = tuple(curve.remaining_quantile(elapsed, q) for q in (.1, .5, .9))
            if not all(math.isfinite(value) for value in values):
                continue
            times = sorted({duration - elapsed for duration, _ in history if duration > elapsed})
            cdf = ((0., 0.),) + tuple(
                (future, 1. - curve.conditional_survival(elapsed, future))
                for future in times
            )
            return OnlineToolEstimate(*values, cdf, len(history), scope)
        return None


class OnlineReportWork:
    def __init__(self) -> None:
        self.ratios: deque[float] = deque(maxlen=64)

    def complete(self, *, expected: int, generated: int) -> None:
        if expected > 0 and generated > 0:
            self.ratios.append(generated / expected)

    def remaining(self, *, expected: int, generated: int) -> float | None:
        endpoints = (
            [expected * ratio for ratio in self.ratios]
            if len(self.ratios) >= 4 else [float(expected)]
        )
        survivors = [endpoint for endpoint in endpoints if endpoint > generated]
        return max(0., float(median(survivors)) - generated) if survivors else None
