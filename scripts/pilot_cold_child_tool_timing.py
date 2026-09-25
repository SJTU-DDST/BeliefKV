#!/usr/bin/env python3
"""Project-held-out cold child tool duration pilot; never enables actions."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
from dataclasses import replace
import heapq
import json
import math
from pathlib import Path
import sys
from statistics import median

import lightgbm as lgb
import numpy as np
import orjson

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.predictor.structured_frontier import (
    FrontierBeliefModel, WaitBeliefKind, _local_features_from_row,
)
from scripts.compare_qwen35_tool_timing_models import _metrics, _start_trigger_quality


DEV_PROJECTS = frozenset(("pydata/xarray", "pytest-dev/pytest"))
NUMERIC = (
    "input_chars", "active_tool_count", "current_sequence_tokens",
    "observed_output_tokens", "llm_round", "invocation_elapsed_ms",
    "state_elapsed_ms", "child_count", "unfinished_child_count",
    "project_class_inflight_other_workflow_2s_peers",
    "project_long_completed_median_ms", "project_long_completed_support",
)
CATEGORICAL = (
    "observed_command_class", "backend_class", "tool_family",
    "agent_definition_id", "state", "backend_pressure",
)


def _rows(path: Path):
    with path.open("rb") as stream:
        for line in stream:
            if line.strip():
                yield orjson.loads(line)


def _cold(attrs: dict) -> bool:
    return (
        attrs.get("tool_name") is not None
        and attrs.get("is_child") is True
        and not (
            attrs.get("tool_name") == "execute"
            and attrs.get("previous_same_input_status") == "success"
        )
        and not (
            attrs.get("tool_name") == "execute"
            and
            type(attrs.get("project_class_duration_median_ms")) in (int, float)
            and int(attrs.get("project_class_completed_support") or 0) >= 16
        )
    )


def _as_of_project_signals(rows: list[dict]) -> dict[tuple[str, str], dict]:
    active = defaultdict(dict)
    completed_long = defaultdict(lambda: deque(maxlen=64))
    closing = []
    signals = {}
    for index, row in sorted(
        enumerate(rows), key=lambda pair: (float(pair[1]["start_ts_ms"]), pair[0])
    ):
        start = float(row["start_ts_ms"])
        while closing and closing[0][0] < start:
            _, old_index, key, previous = heapq.heappop(closing)
            active[key].pop(old_index, None)
            duration = (
                float(previous["terminal_ts_ms"])
                - float(previous["start_ts_ms"])
            )
            if previous.get("status") == "success" and duration >= 2_000:
                completed_long[key].append(duration)
        key = row["project"], row["observed_command_class"]
        peers = sum(
            other_workflow != row["workflow_id"] and peer_start <= start - 2_000
            for peer_start, other_workflow in active[key].values()
        )
        prior = completed_long[key]
        signals[row["workflow_id"], row["tool_call_id"]] = {
            "project_class_inflight_other_workflow_2s_peers": peers,
            "project_long_completed_median_ms": median(prior) if prior else 0,
            "project_long_completed_support": len(prior),
        }
        active[key][index] = (start, row["workflow_id"])
        heapq.heappush(
            closing, (float(row["terminal_ts_ms"]), index, key, row)
        )
    return signals


def _inflight_peers(rows: list[dict]) -> dict[tuple[str, str], int]:
    return {
        key: value["project_class_inflight_other_workflow_2s_peers"]
        for key, value in _as_of_project_signals(rows).items()
    }


def _collect(dataset: Path, reference: FrontierBeliefModel) -> list[dict]:
    child_waits = [
        row for row in _rows(dataset / "external_waits.jsonl")
        if row.get("is_child") is True
        and type(row.get("start_ts_ms")) in (int, float)
        and type(row.get("terminal_ts_ms")) in (int, float)
        and row["terminal_ts_ms"] >= row["start_ts_ms"]
        and row.get("project") and row.get("observed_command_class")
    ]
    project_signals = _as_of_project_signals(child_waits)
    waits = {
        (row["workflow_id"], row["tool_call_id"]): row
        for row in child_waits
        if row.get("training_eligible_survival") is True
        and row.get("censored") is not True
    }
    seen = set()
    samples = []
    for row in _rows(dataset / "frontier_decision_points.jsonl"):
        if row.get("trigger_kind") != "tool_start":
            continue
        attrs = row.get("trigger_attributes") or {}
        key = row.get("workflow_id"), attrs.get("tool_call_id")
        wait = waits.get(key)
        if wait is None or key in seen or not _cold(attrs):
            continue
        invocation = next((
            item for item in row.get("invocations") or ()
            if item.get("invocation_id") == wait.get("invocation_id")
            and item.get("state") == "wait_tool" and item.get("is_child") is True
        ), None)
        if invocation is None or row.get("trigger_invocation_id") != wait["invocation_id"]:
            continue
        seen.add(key)
        elapsed = float(row["timestamp_ms"]) - float(wait["start_ts_ms"])
        remaining = float(wait["terminal_ts_ms"]) - float(row["timestamp_ms"])
        if elapsed < 0 or remaining < 0:
            continue
        local = _local_features_from_row(
            row, invocation, tool_feature_contract=reference.tool_feature_contract,
        )
        belief = reference.predict(local).wait_belief
        if belief is None or belief.kind is not WaitBeliefKind.TOOL:
            continue
        baseline = belief.residual_duration.quantile(.5)
        if not math.isfinite(baseline):
            continue
        signals = project_signals.get(key, {})
        features = {
            **{key: (
                signals.get(key, 0)
                if key in {
                    "project_class_inflight_other_workflow_2s_peers",
                    "project_long_completed_median_ms",
                    "project_long_completed_support",
                }
                else attrs.get(key) if key == "input_chars"
                else invocation.get(key)
            )
               for key in NUMERIC},
            **{key: attrs.get(key) if key in {
                "observed_command_class", "backend_class", "tool_family"
            } else invocation.get(key) for key in CATEGORICAL},
        }
        samples.append({
            "project": row["project"], "workflow": key[0],
            "tool_name": attrs.get("tool_name"),
            "features": features, "actual_ms": remaining,
            "baseline_ms": baseline,
            "total_duration_ms": float(wait["terminal_ts_ms"])
            - float(wait["start_ts_ms"]),
            "start_features": local,
            "long_history_ms": (
                signals["project_long_completed_median_ms"]
                if signals.get("project_long_completed_support", 0) >= 3
                else None
            ),
        })
    return samples


def _vocabulary(samples: list[dict]) -> dict[str, dict[str, int]]:
    return {
        field: {value: index for index, value in enumerate(sorted({
            str(sample["features"].get(field) or "unknown")
            for sample in samples
        }))}
        for field in CATEGORICAL
    }


def _matrix(samples: list[dict], vocabulary: dict) -> np.ndarray:
    matrix = np.empty((len(samples), len(NUMERIC) + len(CATEGORICAL)),
                      dtype=np.float32)
    for index, sample in enumerate(samples):
        features = sample["features"]
        for column, field in enumerate(NUMERIC):
            value = features.get(field)
            matrix[index, column] = (
                math.log1p(max(0, float(value)))
                if type(value) in (int, float) and math.isfinite(value) else 0
            )
        for offset, field in enumerate(CATEGORICAL, len(NUMERIC)):
            matrix[index, offset] = vocabulary[field].get(
                str(features.get(field) or "unknown"), -1,
            )
    return matrix


def _threshold(scores: np.ndarray, labels: np.ndarray,
               *, min_precision: float = .7, min_positive: int = 5) -> float | None:
    # Threshold selection uses development projects only, never calibration.
    candidates = sorted(set(float(score) for score in scores), reverse=True)
    eligible = [
        cut for cut in candidates
        if (selected := scores >= cut).sum() >= min_positive
        and labels[selected].mean() >= min_precision
    ]
    return min(eligible) if eligible else None


def _evaluate(samples: list[dict], scores: np.ndarray, duration: np.ndarray,
              threshold: float | None) -> dict:
    rows = []
    for sample, score, estimate in zip(samples, scores, duration):
        actual = sample["actual_ms"]
        gated = bool(threshold is not None and score >= threshold)
        candidate = max(0., float(estimate)) if gated else sample["baseline_ms"]
        floor = max(2_000., sample["baseline_ms"]) if gated else sample["baseline_ms"]
        history = (
            max(floor, sample["long_history_ms"])
            if gated and sample.get("long_history_ms") is not None else floor
        )
        rows.append({
            "workflow": sample["workflow"], "actual_ms": actual,
            "reference": abs(actual - sample["baseline_ms"]),
            "candidate": abs(actual - candidate),
            "long_floor": abs(actual - floor),
            "long_history": abs(actual - history),
            "reference_forecast_ms": sample["baseline_ms"],
            "candidate_forecast_ms": candidate,
            "long_floor_forecast_ms": floor,
            "long_history_forecast_ms": history,
            "selected": gated,
        })
    long = [item for item in rows if item["actual_ms"] >= 2_000]
    return {
        "count": len(rows),
        "long_count": len(long),
        "selected": sum(item["selected"] for item in rows),
        "true_long_selected": sum(
            item["selected"] for item in long
        ),
        "selection_precision": (
            sum(item["selected"] for item in long)
            / sum(item["selected"] for item in rows)
            if any(item["selected"] for item in rows) else None
        ),
        "long_recall": (
            sum(item["selected"] for item in long) / len(long)
            if long else None
        ),
        "all": {
            side: _metrics(rows, side)
            for side in ("reference", "candidate", "long_floor", "long_history")
        },
        "long": {
            side: _metrics(long, side)
            for side in ("reference", "candidate", "long_floor", "long_history")
        },
        "trigger_quality": {
            side: _start_trigger_quality(rows, side)
            for side in ("reference", "candidate", "long_floor", "long_history")
        },
    }


def _fixed_clock(samples: list[dict], scores: np.ndarray,
                 threshold: float | None, model: FrontierBeliefModel) -> dict:
    reports = {}
    for clock in (500, 2_000, 4_000):
        live = []
        for sample, score in zip(samples, scores):
            if sample["total_duration_ms"] <= clock:
                continue
            belief = model.predict(replace(
                sample["start_features"], elapsed_wait_ms=float(clock)
            )).wait_belief
            if belief is None or belief.kind is not WaitBeliefKind.TOOL:
                continue
            reference = belief.residual_duration.quantile(.5)
            if not math.isfinite(reference):
                continue
            forecast = reference
            if threshold is not None and score >= threshold:
                forecast = max(forecast, 2_000. - clock)
                history = sample["long_history_ms"]
                if history is not None and history > clock:
                    forecast = max(forecast, history - clock)
            actual = sample["total_duration_ms"] - clock
            live.append({
                "workflow": sample["workflow"], "actual_ms": actual,
                "reference": abs(actual - reference),
                "candidate": abs(actual - forecast),
                "reference_forecast_ms": reference,
                "candidate_forecast_ms": forecast,
                "total_duration_ms": sample["total_duration_ms"],
            })
        long = [row for row in live if row["total_duration_ms"] >= 2_000]
        reports[str(clock)] = {
            "alive": len(live),
            "long_total": len(long),
            "all": {
                side: _metrics(live, side)
                for side in ("reference", "candidate")
            },
            "long": {
                side: _metrics(long, side)
                for side in ("reference", "candidate")
            },
            "false_imminent_with_over_2s_remaining": {
                side: sum(
                    row[f"{side}_forecast_ms"] <= 500
                    and row["actual_ms"] > 2_000 for row in live
                )
                for side in ("reference", "candidate")
            },
        }
    return {
        "semantics": (
            "counterfactual fixed-clock wait survival; TOOL_START features frozen "
            "except elapsed time; not an observed scheduler safe point"
        ),
        "by_elapsed_ms": reports,
    }


def _scheduled_long_call_trigger(samples: list[dict], *, min_peers: int,
                                 lead_ms: float = 1_000.) -> dict:
    if min_peers < 0 or lead_ms <= 0:
        raise ValueError("peer count and lead must be non-negative/positive")
    selected = [
        row for row in samples
        if row.get("long_history_ms") is not None
        and row["features"]["project_class_inflight_other_workflow_2s_peers"]
        >= min_peers
    ]
    leads = [
        (
            row["total_duration_ms"]
            - max(0., row["long_history_ms"] - lead_ms)
        )
        for row in selected
    ]
    long = [
        {
            "workflow": row["workflow"],
            "reference": abs(row["total_duration_ms"] - row["baseline_ms"]),
            "candidate": abs(
                row["total_duration_ms"] - row["long_history_ms"]
            ),
        }
        for row in selected if row["total_duration_ms"] >= 2_000
    ]
    return {
        "eligible": len(selected),
        "true_long": len(long),
        "expired_before_trigger": sum(value < 0 for value in leads),
        "late_under_500ms": sum(0 <= value < 500 for value in leads),
        "useful_500_to_2000ms": sum(500 <= value <= 2_000 for value in leads),
        "early_over_2000ms": sum(value > 2_000 for value in leads),
        "long_total_duration_error": {
            side: _metrics(long, side) for side in ("reference", "candidate")
        },
    }


def pilot(train: Path, calibration: Path, reference: FrontierBeliefModel) -> dict:
    if reference.tool_feature_contract != "observed_command_child_project_v3":
        raise ValueError("expected v9 timing contract")
    training = _collect(train, reference)
    calibration_samples = _collect(calibration, reference)
    fit = [row for row in training if row["project"] not in DEV_PROJECTS]
    development = [row for row in training if row["project"] in DEV_PROJECTS]
    if not fit or not development or not calibration_samples:
        raise ValueError("empty train, development, or calibration cohort")
    if {row["project"] for row in training} & {
        row["project"] for row in calibration_samples
    }:
        raise ValueError("calibration projects overlap training projects")
    vocabulary = _vocabulary(fit)
    matrix = _matrix(fit, vocabulary)
    labels = np.asarray([row["actual_ms"] >= 2_000 for row in fit],
                        dtype=np.float32)
    workflow_counts = defaultdict(int)
    for row in fit:
        workflow_counts[row["workflow"]] += 1
    weights = np.asarray([
        1 / math.sqrt(workflow_counts[row["workflow"]]) for row in fit
    ], dtype=np.float32)
    weights *= len(weights) / weights.sum()
    params = {
        "learning_rate": .035, "num_leaves": 7,
        "min_data_in_leaf": 75, "lambda_l2": 10,
        "max_bin": 127, "seed": 42, "num_threads": 4,
        "verbosity": -1,
    }
    classifier = lgb.train(
        {**params, "objective": "binary"},
        lgb.Dataset(matrix, label=labels, weight=weights,
                    categorical_feature=list(range(len(NUMERIC), matrix.shape[1]))),
        num_boost_round=90,
    )
    long_indices = np.flatnonzero(labels)
    regressor = lgb.train(
        {**params, "objective": "regression_l1", "min_data_in_leaf": 20},
        lgb.Dataset(
            matrix[long_indices],
            label=np.log1p([fit[index]["actual_ms"] for index in long_indices]),
            weight=weights[long_indices],
            categorical_feature=list(range(len(NUMERIC), matrix.shape[1])),
        ),
        num_boost_round=90,
    )

    def predict(samples):
        values = _matrix(samples, vocabulary)
        return (
            classifier.predict(values, num_threads=4),
            np.expm1(regressor.predict(values, num_threads=4)),
        )

    dev_scores, dev_durations = predict(development)
    threshold = _threshold(
        dev_scores, np.asarray([row["actual_ms"] >= 2_000 for row in development])
    )
    cal_scores, cal_durations = predict(calibration_samples)
    def peer_rule(samples):
        return _evaluate(
            samples,
            np.asarray([
                row["features"]["project_class_inflight_other_workflow_2s_peers"]
                for row in samples
            ]),
            np.full(len(samples), 2_000.),
            4.,
        )

    exploratory = {}
    for precision in (.25, .5, .7):
        cut = _threshold(
            dev_scores,
            np.asarray([row["actual_ms"] >= 2_000 for row in development]),
            min_precision=precision,
        )
        exploratory[str(precision)] = {
            "development_threshold": cut,
            "development": _evaluate(
                development, dev_scores, dev_durations, cut,
            ),
            "calibration": _evaluate(
                calibration_samples, cal_scores, cal_durations, cut,
            ),
        }
    return {
        "status": "offline_project_disjoint_pilot_not_online_eligible",
        "threshold_selected_on": sorted(DEV_PROJECTS),
        "threshold": threshold,
        "fit_projects": sorted({row["project"] for row in fit}),
        "calibration_projects": sorted({
            row["project"] for row in calibration_samples
        }),
        "training": _evaluate(fit, *predict(fit), threshold),
        "development": _evaluate(development, dev_scores, dev_durations, threshold),
        "calibration": _evaluate(
            calibration_samples, cal_scores, cal_durations, threshold,
        ),
        "fixed_clock": {
            "development": _fixed_clock(
                development, dev_scores, threshold, reference,
            ),
            "calibration": _fixed_clock(
                calibration_samples, cal_scores, threshold, reference,
            ),
        },
        "long_call_first_trigger": {
            str(peers): {
                "development": _scheduled_long_call_trigger(
                    development, min_peers=peers,
                ),
                "calibration": _scheduled_long_call_trigger(
                    calibration_samples, min_peers=peers,
                ),
            }
            for peers in (0, 1, 4)
        },
        "exploratory_four_peer_rule": {
            "development": peer_rule(development),
            "calibration": peer_rule(calibration_samples),
            "qualification": (
                "Four-peer threshold inspected on calibration; new projects "
                "are required before any accuracy or action claim."
            ),
        },
        "exploratory_development_thresholds": exploratory,
        "calibration_long_by_tool": {
            tool: _evaluate(
                selected,
                np.asarray([score for sample, score
                            in zip(calibration_samples, cal_scores)
                            if sample["tool_name"] == tool]),
                np.asarray([duration for sample, duration
                            in zip(calibration_samples, cal_durations)
                            if sample["tool_name"] == tool]),
                threshold,
            ) for tool in sorted({
                row["tool_name"] for row in calibration_samples
                if row["actual_ms"] >= 2_000
            }) if (selected := [
                row for row in calibration_samples if row["tool_name"] == tool
            ])
        },
        "limitations": (
            "Only completed, eligible cold child tool calls at first TOOL_START; "
            "includes errors but excludes censored calls. "
            "Validation projects are already used for other timing diagnostics. "
            "No physical transfer or action eligibility."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--v9-model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = pilot(
        args.train, args.calibration, FrontierBeliefModel.load(args.v9_model),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "threshold": result["threshold"],
        "development": result["development"],
        "calibration": result["calibration"],
    }, indent=2))


if __name__ == "__main__":
    main()
