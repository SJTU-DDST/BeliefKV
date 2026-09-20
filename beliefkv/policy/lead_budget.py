from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class LeadActionBudget:
    fallback_ms: float
    offline_ms: float
    minimum_ms: float
    maximum_ms: float
    quantile: float
    minimum_samples: int


class _AdaptiveQuantile:
    def __init__(
        self,
        *,
        offline_ms: float,
        minimum_ms: float,
        maximum_ms: float,
        quantile: float,
        minimum_samples: int,
    ) -> None:
        self.minimum_ms = minimum_ms
        self.maximum_ms = maximum_ms
        self.quantile = quantile
        self.minimum_samples = minimum_samples
        self.offline_ms = self._clamp(offline_ms)
        self.samples: deque[float] = deque(maxlen=256)
        self.cached_lead_ms = self.offline_ms

    def observe(self, value_ms: float) -> None:
        if not math.isfinite(value_ms):
            return
        # Keep one outlier bin outside the cap instead of allowing a single
        # stalled command to dominate bounded online adaptation.
        clipped = min(max(0.0, value_ms), self.maximum_ms * 2.0)
        self.samples.append(clipped)
        self._refresh()

    def lead_ms(self) -> float:
        return self.cached_lead_ms

    def _refresh(self) -> None:
        if len(self.samples) < self.minimum_samples:
            self.cached_lead_ms = self.offline_ms
            return
        ordered = sorted(self.samples)
        rank = min(
            len(ordered) - 1,
            max(0, int(math.ceil(self.quantile * len(ordered))) - 1),
        )
        self.cached_lead_ms = self._clamp(ordered[rank])

    def _clamp(self, value: float) -> float:
        return min(self.maximum_ms, max(self.minimum_ms, value))


class PredictiveLeadBudgetModel:
    """Action-specific deadline lead budgets with bounded online adaptation."""

    def __init__(self, raw: Mapping[str, object]) -> None:
        if int(raw.get("schema_version", 0)) != 1:
            raise ValueError("unsupported predictive lead budget schema")
        actions_raw = raw.get("actions")
        if not isinstance(actions_raw, Mapping):
            raise ValueError("predictive lead budget omits actions")
        self.provenance = dict(raw.get("provenance", {}))
        self._estimators: dict[str, _AdaptiveQuantile] = {}
        self._budgets: dict[str, LeadActionBudget] = {}
        for name, value in actions_raw.items():
            if not isinstance(value, Mapping):
                raise ValueError(f"invalid lead budget action: {name}")
            budget = LeadActionBudget(
                fallback_ms=float(value["fallback_ms"]),
                offline_ms=float(value["offline_ms"]),
                minimum_ms=float(value["minimum_ms"]),
                maximum_ms=float(value["maximum_ms"]),
                quantile=float(value.get("quantile", 0.95)),
                minimum_samples=int(value.get("minimum_samples", 8)),
            )
            if not 0.0 < budget.quantile < 1.0:
                raise ValueError("lead quantile must be in (0, 1)")
            if budget.minimum_samples <= 0:
                raise ValueError("lead minimum_samples must be positive")
            if not (
                0.0
                <= budget.minimum_ms
                <= budget.maximum_ms
            ):
                raise ValueError("lead bounds are invalid")
            self._budgets[str(name)] = budget
            self._estimators[str(name)] = _AdaptiveQuantile(
                offline_ms=budget.offline_ms,
                minimum_ms=budget.minimum_ms,
                maximum_ms=budget.maximum_ms,
                quantile=budget.quantile,
                minimum_samples=budget.minimum_samples,
            )

    @classmethod
    def load(cls, path: Path) -> "PredictiveLeadBudgetModel":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def observe_dispatch(self, action: str, delay_ms: float) -> None:
        estimator = self._estimators.get(f"{action}_dispatch")
        if estimator is not None:
            estimator.observe(delay_ms)

    def observe_service_readiness(self, delay_ms: float) -> None:
        estimator = self._estimators.get("prefetch_service_readiness")
        if estimator is not None:
            estimator.observe(delay_ms)

    def prepare_control_lead_ms(self, *, fallback_ms: float) -> float:
        return self._lead("prepare_dispatch", fallback_ms)

    def prefetch_desired_lead_ms(self, *, fallback_ms: float) -> float:
        dispatch = self._lead("prefetch_dispatch", fallback_ms * 0.5)
        service = self._lead(
            "prefetch_service_readiness",
            fallback_ms * 0.5,
        )
        dispatch_budget = self._budgets.get("prefetch_dispatch")
        service_budget = self._budgets.get("prefetch_service_readiness")
        maximum = (
            dispatch_budget.maximum_ms + service_budget.maximum_ms
            if dispatch_budget is not None and service_budget is not None
            else fallback_ms
        )
        return min(maximum, dispatch + service)

    def sample_count(self, action: str) -> int:
        estimator = self._estimators.get(action)
        return len(estimator.samples) if estimator is not None else 0

    def lead_source(self, action: str, *, fallback_ms: float) -> tuple[float, str, int]:
        estimator = self._estimators.get(action)
        if estimator is None:
            return fallback_ms, "config_fallback", 0
        if len(estimator.samples) < estimator.minimum_samples:
            return estimator.lead_ms(), "offline_prior", len(estimator.samples)
        return estimator.lead_ms(), "online_quantile", len(estimator.samples)

    def _lead(self, action: str, fallback_ms: float) -> float:
        estimator = self._estimators.get(action)
        if estimator is None:
            return fallback_ms
        return estimator.lead_ms()
