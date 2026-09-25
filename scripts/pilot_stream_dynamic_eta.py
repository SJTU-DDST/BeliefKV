#!/usr/bin/env python3
"""Causal stream-rate ETA upper bound for naturally returning children."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import median
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.audit_native_stream_shadow import _quantile, _rows, _satisfied_last_children

MILESTONES = (64, 1024, 1700, 2400)
STAGE_DELAYS_MS = {1024: 2000, 1700: 250, 2400: 0}


def samples(
    workflows: Path, *, stage_chars: int = 2400,
) -> tuple[list[dict], int]:
    if stage_chars not in STAGE_DELAYS_MS:
        raise ValueError("unsupported stream stage")
    required = tuple(chars for chars in MILESTONES if chars <= stage_chars)
    rows = []
    censored = 0
    for path in sorted(workflows.glob("*/runtime_events.deepagents.jsonl")):
        events = sorted(
            _rows(path), key=lambda event: (
                float(event["ts_ms"]), int(event.get("sequence") or 0)
            )
        )
        last_children = _satisfied_last_children(events)
        child_ids = {
            event["invocation_id"]
            for event in events
            if event["kind"] == "invocation_create"
            and event.get("relation_type") == "spawn"
        }
        per_child = defaultdict(list)
        for event in events:
            if event.get("invocation_id") in child_ids:
                per_child[event["invocation_id"]].append(event)
        for child, child_events in per_child.items():
            by_request = defaultdict(dict)
            for event in child_events:
                attrs = event.get("attributes") or {}
                request = attrs.get("request_id")
                if not request:
                    continue
                record = by_request[request]
                kind = event["kind"]
                if kind == "llm_submit":
                    record["submit"] = float(event["ts_ms"])
                elif kind == "llm_result":
                    record["result"] = event
                elif kind == "structured_action":
                    if attrs.get("beliefkv_child_first_tool_chunk_shadow"):
                        record["tool_chunk"] = float(event["ts_ms"])
                    if attrs.get("beliefkv_child_substantial_content_shadow"):
                        chars = attrs.get("content_threshold_chars")
                        if chars in MILESTONES:
                            record[chars] = float(event["ts_ms"])
                            record["join_id"] = event.get("join_id")
            for request, record in by_request.items():
                if not all(item in record for item in (*required, "submit")):
                    continue
                trigger = record[stage_chars] + STAGE_DELAYS_MS[stage_chars]
                result = record.get("result")
                if (
                    record.get("tool_chunk", float("inf")) <= trigger
                    or result is not None and trigger >= float(result["ts_ms"])
                ):
                    continue
                if result is None:
                    censored += 1
                    continue
                successor = next((
                    event for event in child_events
                    if float(event["ts_ms"]) > float(result["ts_ms"])
                    and event["kind"] in {
                        "return", "invocation_cancel", "llm_submit", "tool_start"
                    }
                ), None)
                if successor is None:
                    censored += 1
                    continue
                attrs = result.get("attributes") or {}
                final = (
                    successor["kind"] == "return"
                    and not attrs.get("runtime_internal")
                    and int(attrs.get("output_chars") or 0) > 0
                    and attrs.get("tool_call_count") == 0
                    and attrs.get("invalid_tool_call_count", 0) == 0
                    and attrs.get("finish_reason") in (None, "stop")
                )
                intervals = (
                    trigger - record["submit"],
                    record[1024] - record[64],
                    record[1700] - record[1024] if stage_chars >= 1700 else 0,
                    record[2400] - record[1700] if stage_chars >= 2400 else 0,
                )
                if any(item < 0 for item in intervals):
                    raise ValueError(f"non-monotone milestones in {path}")
                rows.append({
                    "trace_path": str(path),
                    "workflow_id": result["workflow_id"],
                    "child": child,
                    "request_id": request,
                    "join_id": record.get("join_id"),
                    "trigger_ms": trigger,
                    "final": final,
                    "lead_ms": (
                        float(successor["ts_ms"]) - trigger if final else None
                    ),
                    "last_join_child": (
                        (result["workflow_id"], child) in last_children
                    ),
                    "stage_chars": stage_chars,
                    "features": [
                        *[np.log1p(item) for item in intervals],
                        np.log1p(sum(
                            earlier["kind"] == "llm_submit"
                            and float(earlier["ts_ms"]) < trigger
                            for earlier in child_events
                        )),
                    ],
                })
    return rows, censored


def _fit(rows: list[dict]):
    matrix = np.array([row["features"] for row in rows], dtype=float)
    target = np.log1p(np.array([row["lead_ms"] for row in rows]) / 1000)
    mean = matrix.mean(axis=0)
    scale = np.maximum(matrix.std(axis=0), .1)
    x = np.column_stack((np.ones(len(rows)), (matrix - mean) / scale))
    penalty = np.diag([0.] + [10.] * matrix.shape[1])
    weights = np.linalg.solve(x.T @ x + penalty, x.T @ target)
    return mean, scale, weights


def _predict(rows: list[dict], model) -> list[float]:
    if not rows:
        return []
    mean, scale, weights = model
    matrix = np.array([row["features"] for row in rows], dtype=float)
    x = np.column_stack((np.ones(len(rows)), (matrix - mean) / scale))
    return np.clip(np.expm1(x @ weights) * 1000, 0, 60_000).tolist()


def _quality(rows: list[dict], prediction: list[float], prior: float) -> dict:
    errors = [
        abs(row["lead_ms"] - forecast)
        for row, forecast in zip(rows, prediction)
    ]
    baseline = [abs(row["lead_ms"] - prior) for row in rows]
    return {
        "natural_returns": len(rows),
        "workflow_count": len({row["trace_path"] for row in rows}),
        "eta_error_p50_ms": median(errors) if errors else None,
        "eta_error_p90_ms": _quantile(errors, .9),
        "eta_within_500ms": sum(item <= 500 for item in errors),
        "fixed_prior_error_p50_ms": median(baseline) if baseline else None,
        "fixed_prior_error_p90_ms": _quantile(baseline, .9),
        "fixed_prior_within_500ms": sum(item <= 500 for item in baseline),
        "actual_lead_at_least_500ms": sum(
            row["lead_ms"] >= 500 for row in rows
        ),
    }


def evaluate(
    train: list[dict], heldout: list[dict], censored: int,
    *, stage_chars: int = 2400,
) -> dict:
    projects_train = {
        Path(row["trace_path"]).parent.name.split("__", 1)[0] for row in train
    }
    projects_test = {
        Path(row["trace_path"]).parent.name.split("__", 1)[0] for row in heldout
    }
    if overlap := projects_train & projects_test:
        raise ValueError(f"projects overlap: {sorted(overlap)}")
    true_train = [row for row in train if row["final"]]
    true_eval = [row for row in heldout if row["final"]]
    if len(true_train) < 20:
        raise ValueError("insufficient naturally returning development children")
    model = _fit(true_train)
    prior = median(row["lead_ms"] for row in true_train)
    by_project = {}
    for project in projects_test:
        project_rows = [
            row for row in true_eval
            if Path(row["trace_path"]).parent.name.split("__", 1)[0] == project
        ]
        by_project[project] = _quality(
            project_rows, _predict(project_rows, model), prior,
        )
    return {
        "status": "read_only_oracle_return_conditioned_stream_eta",
        "stage_chars": stage_chars,
        "stage_delay_ms": STAGE_DELAYS_MS[stage_chars],
        "development_projects": sorted(projects_train),
        "heldout_projects": sorted(projects_test),
        "fixed_prior_ms": prior,
        "development": _quality(true_train, _predict(true_train, model), prior),
        "heldout": _quality(true_eval, _predict(true_eval, model), prior),
        "heldout_last_join_child": _quality(
            [row for row in true_eval if row["last_join_child"]],
            _predict(
                [row for row in true_eval if row["last_join_child"]], model,
            ),
            prior,
        ),
        "heldout_by_project": dict(sorted(by_project.items())),
        "heldout_nonfinal_candidates": len(heldout) - len(true_eval),
        "heldout_censored_candidates": censored,
        "limitation": (
            "ETA is evaluated conditional on a future confirmed natural "
            "RETURN. Nonfinal and censored candidates are counted separately; "
            "this oracle-filtered error is only an upper bound on online "
            "end-to-end predictive action precision."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, action="append", required=True)
    parser.add_argument("--evaluate-workflows", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--stage-chars", type=int, choices=sorted(STAGE_DELAYS_MS), default=2400,
    )
    args = parser.parse_args()
    train_projects = {
        path.parent.name.split("__", 1)[0]
        for directory in args.train_workflows
        for path in directory.glob("*/runtime_events.deepagents.jsonl")
    }
    eval_projects = {
        path.parent.name.split("__", 1)[0]
        for directory in args.evaluate_workflows
        for path in directory.glob("*/runtime_events.deepagents.jsonl")
    }
    if overlap := train_projects & eval_projects:
        raise ValueError(f"projects overlap: {sorted(overlap)}")
    training = []
    for directory in args.train_workflows:
        rows, _ = samples(directory, stage_chars=args.stage_chars)
        training.extend(rows)
    testing = []
    censored = 0
    for directory in args.evaluate_workflows:
        rows, missing = samples(directory, stage_chars=args.stage_chars)
        testing.extend(rows)
        censored += missing
    report = evaluate(training, testing, censored, stage_chars=args.stage_chars)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
