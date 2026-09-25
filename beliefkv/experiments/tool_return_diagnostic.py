"""Episode-level, read-only evaluation of tool-return lead time."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
import math
from typing import Any

from beliefkv.experiments.join_group_diagnostic import DEFAULT_HORIZONS_MS
from beliefkv.predictor.structured_frontier import (
    FrontierBeliefModel,
    WaitBeliefKind,
    _local_features_from_row,
)


def _summarize(
    model: FrontierBeliefModel,
    episodes: Iterable[Mapping[str, Any]],
    field: str,
) -> dict[str, Any]:
    counters: Counter[str] = Counter()
    errors: list[float] = []
    pairs: list[tuple[float, float]] = []
    covered = 0
    for episode in episodes:
        snapshot = episode[field]
        if snapshot is None:
            counters["no_wait_tool_snapshot"] += 1
            continue
        row, features, ts = snapshot
        prediction = model.predict(_local_features_from_row(row, features))
        wait = prediction.wait_belief
        if wait.kind is not WaitBeliefKind.TOOL or not wait.residual_duration.values:
            counters["wait_head_unavailable"] += 1
            continue
        actual = episode["release_ts"] - ts
        error = abs(actual - wait.residual_duration.quantile(0.5))
        errors.append(error)
        pairs.append((actual, error))
        covered += (
            wait.residual_duration.quantile(0.1)
            <= actual
            <= wait.residual_duration.quantile(0.9)
        )
    errors.sort()
    actuals = sorted(actual for actual, _ in pairs)
    longer_waits = {}
    for minimum in (500, 2_000, 10_000):
        subset = sorted(error for actual, error in pairs if actual >= minimum)
        longer_waits[str(minimum)] = {
            "episode_count": len(subset),
            "model_median_absolute_error_ms": (
                subset[(len(subset) - 1) // 2] if subset else None
            ),
            "model_within_500ms_rate": (
                sum(error <= 500 for error in subset) / len(subset)
                if subset else None
            ),
        }
    return {
        "episode_count": len(errors),
        "missing": dict(sorted(counters.items())),
        "actual_remaining_ms_p50": actuals[(len(actuals) - 1) // 2]
        if actuals else None,
        "actual_remaining_ms_p90": actuals[math.ceil(.9 * len(actuals)) - 1]
        if actuals else None,
        "zero_baseline_mean_absolute_error_ms": (
            sum(actuals) / len(actuals) if actuals else None
        ),
        "zero_baseline_within_500ms_rate": (
            sum(item <= 500 for item in actuals) / len(actuals)
            if actuals else None
        ),
        "longer_actual_waits": longer_waits,
        "median_absolute_error_ms": errors[(len(errors) - 1) // 2] if errors else None,
        "p90_absolute_error_ms": errors[math.ceil(.9 * len(errors)) - 1]
        if errors else None,
        "mean_absolute_error_ms": sum(errors) / len(errors) if errors else None,
        "within_500ms_rate": sum(item <= 500 for item in errors) / len(errors)
        if errors else None,
        "marginal_p10_p90_coverage": covered / len(errors) if errors else None,
    }


def diagnose_tool_returns(
    model: FrontierBeliefModel,
    decision_rows: Iterable[Mapping[str, Any]],
    external_waits: Iterable[Mapping[str, Any]],
    *,
    horizons_ms: tuple[int, ...] = DEFAULT_HORIZONS_MS,
) -> dict[str, Any]:
    """Select at most one decision per observed episode/horizon.

    Horizons are selected retrospectively using the actual release timestamp.
    This measures forecast quality conditional on reaching the window, not
    whether an online scheduler can recognize the window in advance.
    """
    if not horizons_ms or len(set(horizons_ms)) != len(horizons_ms) or any(
        limit <= 0 for limit in horizons_ms
    ):
        raise ValueError("horizons must be distinct positive milliseconds")
    waits: defaultdict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for tool in external_waits:
        key = (str(tool.get("workflow_id") or ""), str(tool.get("invocation_id") or ""))
        if all(key) and tool.get("start_ts_ms") is not None:
            waits[key].append(tool)
    episodes: dict[tuple[str, str, str], dict[str, Any]] = {}
    counters: Counter[str] = Counter()
    for row in decision_rows:
        workflow = str(row.get("workflow_id") or "")
        ts = float(row.get("timestamp_ms") or 0)
        labels = {
            str(label.get("invocation_id") or ""): label
            for label in row.get("labels", ())
        }
        for features in row.get("invocations", ()):
            if features.get("state") != "wait_tool":
                continue
            invocation = str(features.get("invocation_id") or "")
            label = labels.get(invocation) or {}
            if not (label.get("target_training_eligible") or {}).get("external_wait"):
                counters["ineligible_decision"] += 1
                continue
            active = [
                item for item in waits.get((workflow, invocation), ())
                if float(item["start_ts_ms"]) <= ts
                and (
                    item.get("terminal_ts_ms") is None
                    or ts <= float(item["terminal_ts_ms"])
                )
            ]
            if not active:
                counters["no_active_tool"] += 1
                continue
            tool_ids = [str(item.get("tool_call_id") or "") for item in active]
            if not all(tool_ids) or len(set(tool_ids)) != len(tool_ids):
                counters["invalid_tool_identity"] += 1
                continue
            if any(
                item.get("terminal_ts_ms") is None
                or item.get("censored") is True
                or item.get("training_eligible_survival") is not True
                for item in active
            ):
                counters["censored_or_ineligible_tool"] += 1
                continue
            release_ts = max(float(item["terminal_ts_ms"]) for item in active)
            if not math.isfinite(release_ts) or release_ts < ts:
                counters["invalid_tool_release"] += 1
                continue
            key = (workflow, invocation, "+".join(sorted(tool_ids)))
            episode = episodes.setdefault(key, {
                "release_ts": release_ts,
                "first": None,
                "horizons": {limit: None for limit in horizons_ms},
            })
            if not math.isclose(episode["release_ts"], release_ts, abs_tol=1e-6):
                raise ValueError(f"conflicting release timestamp for tool episode: {key}")
            snapshot = (row, features, ts)
            if episode["first"] is None or ts < episode["first"][2]:
                episode["first"] = snapshot
            for limit, previous in episode["horizons"].items():
                if release_ts - ts <= limit and (
                    previous is None or ts < previous[2]
                ):
                    episode["horizons"][limit] = snapshot
    first = _summarize(model, episodes.values(), "first")
    by_horizon = {}
    for limit in horizons_ms:
        for episode in episodes.values():
            episode["selected"] = episode["horizons"][limit]
        metrics = _summarize(model, episodes.values(), "selected")
        metrics["episode_coverage"] = (
            metrics["episode_count"] / len(episodes) if episodes else None
        )
        by_horizon[str(limit)] = metrics
    return {
        "semantics": (
            "completed tool-call sets; earliest eligible WAIT_TOOL snapshot per "
            "episode/horizon; retrospective selection is not online detection"
        ),
        "completed_episode_count": len(episodes),
        "exclusions": dict(sorted(counters.items())),
        "first_snapshot": first,
        "by_horizon_ms": by_horizon,
    }
