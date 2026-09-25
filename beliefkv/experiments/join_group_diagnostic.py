"""Read-only, episode-weighted diagnostics for JOIN timing hints."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
import math
from typing import Any

from beliefkv.predictor.structured_frontier import (
    FrontierBeliefModel,
    _local_features_from_row,
)

DEFAULT_HORIZONS_MS = (300_000, 60_000, 10_000, 2_000, 500)


def _summarize_snapshots(
    model: FrontierBeliefModel,
    groups: Iterable[Mapping[str, Any]],
    *,
    snapshot_field: str,
) -> dict[str, Any]:
    counters: Counter[str] = Counter()
    absolute_errors = []
    signed_errors = []
    envelope_covered = 0
    envelope_widths = []
    child_counts = []
    for group in groups:
        snapshot = group[snapshot_field]
        if snapshot is None:
            counters["no_wait_join_snapshot"] += 1
            continue
        row, timestamp, pending = snapshot
        if any(child is None for child in pending.values()):
            counters["missing_pending_child"] += 1
            continue
        predictions = [
            model.predict(_local_features_from_row(row, child))
            for child in pending.values()
        ]
        if any(
            not prediction.remaining_to_return_ms.values
            or prediction.support_for("child_completion") == "unavailable"
            or prediction.ood_reasons
            for prediction in predictions
        ):
            counters["unavailable_child_prediction"] += 1
            continue
        p10 = max(pred.remaining_to_return_ms.quantile(0.1) for pred in predictions)
        p50 = max(pred.remaining_to_return_ms.quantile(0.5) for pred in predictions)
        p90 = max(pred.remaining_to_return_ms.quantile(0.9) for pred in predictions)
        actual = group["reentry_ts"] - timestamp
        absolute_errors.append(abs(actual - p50))
        signed_errors.append(p50 - actual)
        envelope_covered += p10 <= actual <= p90
        envelope_widths.append(p90 - p10)
        child_counts.append(len(predictions))
    errors = sorted(absolute_errors)
    return {
        "counts": dict(sorted(counters.items())),
        "groups_with_timing_hint": len(errors),
        "mean_absolute_error_ms": sum(errors) / len(errors) if errors else None,
        "median_absolute_error_ms": errors[(len(errors) - 1) // 2] if errors else None,
        "p90_absolute_error_ms": errors[math.ceil(len(errors) * 0.9) - 1] if errors else None,
        "within_500ms_rate": sum(value <= 500 for value in errors) / len(errors)
        if errors else None,
        "mean_signed_error_ms": sum(signed_errors) / len(errors) if errors else None,
        "marginal_envelope_coverage": envelope_covered / len(errors) if errors else None,
        "mean_marginal_envelope_width_ms": (
            sum(envelope_widths) / len(errors) if errors else None
        ),
        "mean_pending_children": sum(child_counts) / len(errors) if errors else None,
    }


def diagnose_join_groups(
    model: FrontierBeliefModel,
    decision_rows: Iterable[Mapping[str, Any]],
    reentries: Iterable[Mapping[str, Any]],
    *,
    horizons_ms: tuple[int, ...] = DEFAULT_HORIZONS_MS,
) -> dict[str, Any]:
    """Evaluate ALL-compatible observed joins, one earliest complete snapshot each.

    The trace has no join mode field; only reentries whose observed timestamp
    agrees with the last member RETURN can be diagnosed as ALL-compatible.
    Pointwise marginal quantile envelopes are not joint calibrated intervals.
    """
    if not horizons_ms or any(value <= 0 for value in horizons_ms):
        raise ValueError("horizons must be positive")
    if len(set(horizons_ms)) != len(horizons_ms):
        raise ValueError("duplicate horizons")
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    counters: Counter[str] = Counter()
    for item in reentries:
        if item.get("reentry_kind") != "join" or item.get("training_eligible") is not True:
            continue
        counters["eligible_join_events"] += 1
        if item.get("terminal_status") != "satisfied":
            counters["not_satisfied"] += 1
            continue
        members = item.get("member_outcomes") or ()
        try:
            returns = {
                str(child["invocation_id"]): float(child["return_ts_ms"])
                for child in members
            }
            reentry_ts = float(item["reentry_ts_ms"])
        except (KeyError, TypeError, ValueError):
            counters["missing_member_return"] += 1
            continue
        if (
            not returns
            or len(returns) != len(members)
            or not all(returns)
            or not all(math.isfinite(ts) for ts in returns.values())
            or not math.isfinite(reentry_ts)
        ):
            counters["invalid_member_return"] += 1
            continue
        if not math.isclose(max(returns.values()), reentry_ts, abs_tol=1.0):
            counters["not_all_compatible"] += 1
            continue
        key = (str(item.get("workflow_id") or ""), str(item.get("invocation_id") or ""))
        if not all(key):
            counters["missing_identity"] += 1
            continue
        group_id = str(item.get("reentry_id") or "")
        if not group_id:
            counters["missing_join_identity"] += 1
            continue
        groups[(key[0], group_id)] = {
            "parent": key[1], "reentry_ts": reentry_ts, "returns": returns,
            "wait_start": float(item.get("wait_start_ts_ms") or 0.0),
            "snapshot": None,
            "horizon_snapshots": {limit: None for limit in horizons_ms},
        }

    by_workflow: dict[str, list[dict[str, Any]]] = {}
    for (workflow, _), group in groups.items():
        by_workflow.setdefault(workflow, []).append(group)
    for row in decision_rows:
        workflow = str(row.get("workflow_id") or "")
        candidate_groups = by_workflow.get(workflow)
        if not candidate_groups:
            continue
        timestamp = float(row.get("timestamp_ms") or 0)
        features = {
            str(item.get("invocation_id") or ""): item
            for item in row.get("invocations", ())
        }
        for group in candidate_groups:
            if (
                timestamp < group["wait_start"]
                or timestamp >= group["reentry_ts"]
                or features.get(group["parent"], {}).get("state") != "wait_join"
            ):
                continue
            pending = {
                child_id: features.get(child_id)
                for child_id, return_ts in group["returns"].items()
                if return_ts > timestamp
            }
            if not pending:
                continue
            # A partial child snapshot is a coverage failure, not a fabricated
            # JOIN timing prediction; keep the first eligible decision.
            snapshot = (row, timestamp, pending)
            if group["snapshot"] is None or timestamp < group["snapshot"][1]:
                group["snapshot"] = snapshot
            for limit, previous in group["horizon_snapshots"].items():
                if group["reentry_ts"] - timestamp <= limit and (
                    previous is None or timestamp < previous[1]
                ):
                    group["horizon_snapshots"][limit] = snapshot

    counters["all_compatible_join_groups"] = len(groups)
    first = _summarize_snapshots(model, groups.values(), snapshot_field="snapshot")
    counters.update(first.pop("counts"))
    counters["groups_with_timing_hint"] = first.pop("groups_with_timing_hint")
    horizon_metrics = {}
    for limit in horizons_ms:
        for group in groups.values():
            group["selected_horizon_snapshot"] = group["horizon_snapshots"][limit]
        metrics = _summarize_snapshots(
            model, groups.values(), snapshot_field="selected_horizon_snapshot"
        )
        metrics["join_group_coverage"] = (
            metrics["groups_with_timing_hint"] / len(groups) if groups else None
        )
        horizon_metrics[str(limit)] = metrics
    return {
        "evidence": "read_only_group_diagnostic"
        if counters["groups_with_timing_hint"] else "no_group_hints",
        "semantics": (
            "one earliest WAIT_JOIN snapshot per JOIN/horizon, selected retrospectively "
            "using observed reentry; empirical p10/p90 "
            "envelope is NOT a calibrated joint JOIN interval"
        ),
        "counts": dict(sorted(counters.items())),
        **first,
        "by_horizon_ms": horizon_metrics,
    }
