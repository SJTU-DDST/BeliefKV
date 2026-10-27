"""Causal timing adaptation scoped to one live workload collection."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
import math
from statistics import median

from beliefkv.core.events import RuntimeEventKind
from beliefkv.predictor.tool_survival import KaplanMeierCurve
from beliefkv.predictor.tool_timing_prior import tool_signature


_MINIMUM_HISTORY = {"workflow": 4, "run": 8}


@dataclass(frozen=True)
class OnlineToolEstimate:
    p10_ms: float
    p50_ms: float
    p90_ms: float
    release_cdf: tuple[tuple[float, float], ...]
    samples: int
    scope: str


class OnlineWorkloadToolTiming:
    def __init__(self, *, window: int = 64, max_keys: int = 256, prior=None) -> None:
        self.window, self.max_keys = window, max_keys
        self.prior = prior
        self.open: dict[tuple, tuple[tuple, float]] = {}
        self._open_by_invocation: dict[tuple, dict[tuple, tuple[tuple, float]]] = {}
        self.samples: OrderedDict[tuple, deque] = OrderedDict()
        self._curves: dict[tuple, KaplanMeierCurve] = {}
        self._history_revisions: dict[tuple, int] = {}
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
            signature = tool_signature(attrs)
            started = self.open.setdefault(identity, (signature, event.ts_ms))
            self._open_by_invocation.setdefault(identity[:2], {})[identity] = started
        elif event.kind is RuntimeEventKind.TOOL_END:
            started = self.open.pop(identity, None)
            if started is None:
                return
            active = self._open_by_invocation[identity[:2]]
            active.pop(identity)
            if not active:
                del self._open_by_invocation[identity[:2]]
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
                self._history_revisions[key] = self.revision + 1
            while len(self.samples) > self.max_keys:
                key, _ = self.samples.popitem(last=False)
                self._curves.pop(key, None)
                self._history_revisions.pop(key, None)
            self.revision += 1
        elif event.kind in (
            RuntimeEventKind.RETURN, RuntimeEventKind.INVOCATION_CANCEL,
            RuntimeEventKind.WORKFLOW_END,
        ):
            if event.kind is RuntimeEventKind.WORKFLOW_END:
                for owner in tuple(self._open_by_invocation):
                    if owner[0] == event.workflow_id:
                        self._discard_open_invocation(owner)
            else:
                self._discard_open_invocation((event.workflow_id, event.invocation_id))

    def _discard_open_invocation(self, owner: tuple) -> None:
        for identity in self._open_by_invocation.pop(owner, ()):
            self.open.pop(identity, None)

    def estimate_revision(self, workflow: str, invocation: str) -> tuple | None:
        active = self._open_by_invocation.get((workflow, invocation), {})
        if len(active) != 1:
            return None
        identity, (signature, started) = next(iter(active.items()))
        # Both histories can affect fallback. An unrelated completion can also
        # evict either history, so absence is part of this bounded witness.
        return (
            identity, started,
            self._history_revisions.get((workflow, *signature)),
            self._history_revisions.get(("*", *signature)),
            self.prior.sha256 if self.prior is not None else None,
        )

    def prefetch_supported(
        self, workflow: str, invocation: str, *, now_ms: float, scope: str,
    ) -> bool:
        """Require support among calls that survived the current wait."""
        active = self._open_by_invocation.get((workflow, invocation), {})
        if len(active) != 1:
            return False
        signature, started = next(iter(active.values()))
        elapsed = max(0., now_ms - started)
        if scope.startswith("hybrid_") or scope == "offline_prior":
            prior = self.prior.curves.get(signature) if self.prior is not None else None
            if prior is not None and prior.sample_count * prior.survival(elapsed) >= 8:
                return True
            scope = scope.removeprefix("hybrid_")
        minimum = _MINIMUM_HISTORY.get(scope)
        if minimum is None:
            return False
        prefix = workflow if scope == "workflow" else "*"
        history = self.samples.get((prefix, *signature), ())
        return sum(
            completed and duration > elapsed for duration, completed in history
        ) >= minimum

    def estimate(self, workflow: str, invocation: str, *, now_ms: float) -> OnlineToolEstimate | None:
        active = self._open_by_invocation.get((workflow, invocation), {})
        # A parallel group's completion is a maximum, not one tool's duration.
        if len(active) != 1:
            return None
        signature, started = next(iter(active.values()))
        elapsed = max(0., now_ms - started)
        for prefix, scope in ((workflow, "workflow"), ("*", "run")):
            minimum = _MINIMUM_HISTORY[scope]
            key = (prefix, *signature)
            history = self.samples.get(key)
            if history is None:
                continue
            curve = self._curves.get(key)
            if curve is None:
                if sum(completed for _, completed in history) < minimum:
                    continue
                curve = self._curves[key] = KaplanMeierCurve().fit(list(history))
            if curve.event_count < minimum:
                continue
            values = tuple(curve.remaining_quantile(elapsed, q) for q in (.1, .5, .9))
            if not all(math.isfinite(value) for value in values):
                continue
            times = sorted({duration - elapsed for duration, _ in history if duration > elapsed})
            # Finite quantiles share one positive conditioning population.
            base = curve.survival(elapsed)
            cdf = tuple(
                (future, 1. - max(
                    0., min(1., curve.survival(elapsed + future) / base),
                ))
                for future in times
            )
            estimate = OnlineToolEstimate(*values, cdf, len(history), scope)
            return self._blend_prior(signature, elapsed, estimate)
        return self._blend_prior(signature, elapsed, None)

    def _blend_prior(self, signature, elapsed, online):
        prior = self.prior.curves.get(signature) if self.prior is not None else None
        if prior is None or prior.survival(elapsed) <= 0:
            return online
        base = prior.survival(elapsed)
        times = {t - elapsed for t, _ in prior._steps if t > elapsed}
        weight = 0.
        if online is not None:
            weight = online.samples / (online.samples + self.prior.strength)
            times.update(t for t, _ in online.release_cdf)
        points = dict(online.release_cdf) if online is not None else {}
        last, cdf = 0., []
        for future in sorted(times):
            last = points.get(future, last)
            probability = (
                weight * last
                + (1. - weight) * (1. - prior.survival(elapsed + future) / base)
            )
            cdf.append((future, max(0., min(1., probability))))
        values = tuple(next(
            (t for t, probability in cdf if probability >= q), math.inf,
        ) for q in (.1, .5, .9))
        if not all(math.isfinite(value) for value in values):
            return online
        return OnlineToolEstimate(
            *values, tuple(cdf), online.samples if online is not None else 0,
            f"hybrid_{online.scope}" if online is not None else "offline_prior",
        )


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
