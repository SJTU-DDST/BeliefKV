#!/usr/bin/env python3
"""Causal completion-window scores on recorded online forecasts, CPU only."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
from statistics import median
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import lightgbm as lgb
import numpy as np

from scripts.audit_semantic_h2d_online import snapshot_records
from scripts.fit_child_completion_windows import select_threshold, window_metrics
from beliefkv.predictor.child_semantic_work import workflow_weights

HORIZONS = (4, 8, 16, 32)
FEATURES = (
    "phase_score", "notice", "log_output", "log_content", "log_report_estimate",
    "log_tools", "log_rounds", "log_center", "log_upper", "log_lower",
    "log_advanced", "log_projected_center", "log_projected_upper", "observation_age",
)


def forecast_features(row: dict) -> list[float]:
    observed = row["observed_output_tokens"]
    current = row["current_output_tokens"]
    advanced = max(0, current - observed)
    middle, upper = row["remaining_tokens"], row["upper_tokens"]
    return [
        row["score"], float(row["notice_active"]), math.log1p(current),
        math.log1p(row["content_chars"]), math.log1p(row["estimated_report_tokens"]),
        math.log1p(row["prior_tool_calls"]), math.log1p(row["prior_model_rounds"]),
        math.log1p(middle), math.log1p(upper), math.log1p(row["lower_tokens"]),
        math.log1p(advanced), math.log1p(max(0, middle - advanced)),
        math.log1p(max(0, upper - advanced)), row["observation_age_ms"] / 1000.,
    ]


def countdown_work(row: dict) -> float:
    advanced = max(0, row["current_output_tokens"] - row["observed_output_tokens"])
    work = row["remaining_tokens"]
    if work <= advanced:
        work = row["upper_tokens"]
    return work - advanced if work > advanced else math.inf


def collect(arm: Path, phase_threshold: float) -> tuple[list[dict], dict]:
    forecasts, clock = [], None
    for row in snapshot_records(
        arm / "opportunities/admission_opportunities.jsonl", allow_partial=False,
    ):
        if row.get("event") == "safe_point_census" and clock is None:
            clock = row["ts_ms"] - row["monotonic_ms"]
        elif row.get("event") == "semantic_child_forecast":
            forecasts.append(row)
    if clock is None:
        raise ValueError("missing paired client/native clock")
    native = {
        row["attributes"]["request_id"]: row
        for row in snapshot_records(arm / "server/runtime_events.sglang.jsonl", allow_partial=False)
        if row["kind"] == "llm_result" and row.get("attributes", {}).get("request_id")
    }
    tasks, returns, nonfinal = {}, {}, set()
    for path in sorted(arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl")):
        last, submitted = {}, defaultdict(list)
        for row in snapshot_records(path, allow_partial=False):
            attrs = row.get("attributes") or {}
            rid, invocation = attrs.get("request_id"), row.get("invocation_id")
            if row["kind"] == "llm_submit" and rid and not attrs.get("runtime_internal"):
                tasks[rid] = path.parent.name
                submitted[invocation].append(rid)
            elif row["kind"] == "llm_result" and rid and not attrs.get("runtime_internal"):
                last[invocation] = rid
                if attrs.get("tool_call_count", 0) > 0:
                    nonfinal.add(rid)
            elif (
                row["kind"] == "return" and attrs.get("source") == "deepagents_task"
                and attrs.get("outcome") == "completed" and invocation in last
            ):
                returns[last[invocation]] = row["ts_ms"] + clock
        for requests in submitted.values():
            nonfinal.update(requests[:-1])
    samples, excluded = [], Counter()
    for row in forecasts:
        rid = row["request_id"]
        result = native.get(rid)
        if rid not in tasks or result is None or rid not in returns and rid not in nonfinal:
            excluded["unresolved_label"] += 1
            continue
        # EOS is used only to identify evaluation labels; it is never a feature.
        if row["ts_ms"] >= result["ts_ms"]:
            excluded["at_or_after_native_eos"] += 1
            continue
        if (
            not row.get("notice_active") or row["score"] < phase_threshold
            or type(row.get("current_output_tokens")) is not int
            or row["current_output_tokens"] < 16
            or not 0 <= row.get("observation_age_ms", math.inf) <= 1500
            or not 0 <= row.get("last_service_age_ms", math.inf) <= 250
        ):
            excluded["not_live_runtime_candidate"] += 1
            continue
        current = row["current_output_tokens"]
        remaining = result["attributes"]["output_tokens"] - current if rid in returns else None
        if remaining is not None and remaining <= 0:
            excluded["no_positive_work_label"] += 1
            continue
        observation = SimpleNamespace(request_id=rid, ts_ms=row["ts_ms"])
        samples.append({
            "task": tasks[rid], "observation": observation, "forecast": row,
            "remaining_tokens": remaining,
            "remaining_client_wall_ms": returns[rid] - row["ts_ms"] if rid in returns else None,
            "lead_to_native_eos_ms": result["ts_ms"] - row["ts_ms"],
            "features": forecast_features(row),
        })
    return samples, {
        "forecast_records": len(forecasts), "selected_snapshots": len(samples),
        "selected_requests": len({row["observation"].request_id for row in samples}),
        "selected_workflows": len({row["task"] for row in samples}),
        "excluded": dict(excluded),
    }


def roles(samples: list[dict], held_project: str) -> dict[str, list[int]]:
    grouped = defaultdict(list)
    for index, row in enumerate(samples):
        project = row["task"].split("__", 1)[0]
        if project == held_project:
            role = "evaluation"
        else:
            digest = hashlib.sha256(row["task"].encode()).digest()
            role = "selector" if int.from_bytes(digest[:8], "big") % 4 == 0 else "training"
        grouped[role].append(index)
    if any(not grouped[role] for role in ("training", "selector", "evaluation")):
        raise ValueError("all workflow roles require observed samples")
    return dict(grouped)


def timing_summary(samples: list[dict], scores: np.ndarray, threshold: float | None) -> dict:
    first = {}
    if threshold is not None:
        for row, score in sorted(
            zip(samples, scores), key=lambda pair: pair[0]["observation"].ts_ms,
        ):
            rid = row["observation"].request_id
            if score >= threshold and rid not in first:
                first[rid] = row
    leads = [
        row["remaining_client_wall_ms"] for row in first.values()
        if row["remaining_tokens"] is not None
    ]
    return {
        "natural_trigger_count": len(leads),
        "return_lead_p50_ms": median(leads) if leads else None,
        "return_lead_0_to_500ms_count": sum(0 < value <= 500 for value in leads),
        "return_lead_500_to_1000ms_count": sum(500 < value <= 1000 for value in leads),
        "return_lead_over_2000ms_count": sum(value > 2000 for value in leads),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--held-project", default="pydata")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    artifact_report = json.loads((args.artifact.parent / "report.json").read_text())
    phase_threshold = artifact_report["calibration"]["semantic_event"]["request_operating_point"]["threshold"]
    samples, coverage = collect(args.arm, phase_threshold)
    split = roles(samples, args.held_project)
    matrix = np.asarray([row["features"] for row in samples], dtype=np.float64)
    train, selector, held = (split[role] for role in ("training", "selector", "evaluation"))
    held_samples, selector_samples = (
        [samples[index] for index in indices] for indices in (held, selector)
    )
    scores, models = [], []
    for horizon in HORIZONS:
        labels = np.asarray([
            row["remaining_tokens"] is not None and row["remaining_tokens"] <= horizon
            for row in samples
        ], dtype=np.int32)
        model = lgb.train(
            {
                "objective": "binary", "num_leaves": 7, "min_data_in_leaf": 15,
                "learning_rate": .05, "lambda_l2": 5., "num_threads": 2,
                "verbosity": -1, "seed": 21, "deterministic": True,
                "force_col_wise": True,
            },
            lgb.Dataset(
                matrix[train], label=labels[train],
                weight=workflow_weights([samples[index]["task"] for index in train]) * len(train),
                feature_name=list(FEATURES),
            ),
            num_boost_round=100,
        )
        scores.append(model.predict(matrix, num_threads=2))
        models.append(model)
    # Larger token windows contain smaller windows, including score repair.
    scores = np.maximum.accumulate(np.column_stack(scores), axis=1)
    results = {}
    for column, horizon in enumerate(HORIZONS):
        operating = select_threshold(
            selector_samples, scores[selector, column], horizon, precision=.8, minimum=3,
        )
        threshold = operating["threshold"] if operating else None
        baseline = np.asarray([
            float(countdown_work(row["forecast"]) <= horizon) for row in held_samples
        ])
        results[str(horizon)] = {
            "selector": operating,
            "evaluation": window_metrics(held_samples, scores[held, column], horizon, threshold),
            "evaluation_timing": timing_summary(held_samples, scores[held, column], threshold),
            "baseline_center_countdown": window_metrics(held_samples, baseline, horizon, .5),
            "baseline_center_countdown_timing": timing_summary(held_samples, baseline, .5),
        }
    report = {
        "scope": (
            "CPU development completion-window score experiment. Only the new work "
            "labels are project-held-out; phase/encoder were previously trained. "
            "Scores are not calibrated probabilities. No physical transfer benefit."
        ),
        "arm": str(args.arm.resolve()), "phase_artifact_sha256": hashlib.sha256(args.artifact.read_bytes()).hexdigest(),
        "coverage": coverage, "held_project": args.held_project,
        "role_snapshot_counts": {role: len(indices) for role, indices in split.items()},
        "role_workflow_counts": {
            role: len({samples[index]["task"] for index in indices}) for role, indices in split.items()
        },
        "features": FEATURES, "horizons": HORIZONS, "results": results,
    }
    args.output.mkdir(parents=True)
    for horizon, model in zip(HORIZONS, models):
        model.save_model(str(args.output / f"window_{horizon}.txt"))
    (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
