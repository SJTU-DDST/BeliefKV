#!/usr/bin/env python3
"""Read-only, task-held-out classification of child return from stream timing."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from statistics import median
import sys

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_native_stream_shadow import _rows, _satisfied_last_children


DELAY_MS = 2000
MIN_PRECISION = .95
MIN_SELECTED = 12


def samples(workflows: Path) -> tuple[list[dict], int, set[tuple[str, str]]]:
    known, censored = [], 0
    last_children = set()
    for path in sorted(workflows.glob("*/runtime_events.deepagents.jsonl")):
        by_child = defaultdict(list)
        workflow_events = list(_rows(path))
        last_children.update(
            (str(path), workflow, child)
            for workflow, child in _satisfied_last_children(workflow_events)
        )
        joins = {
            event["join_id"]: (
                float(event["ts_ms"]), set(event["member_invocation_ids"])
            )
            for event in workflow_events
            if event.get("kind") == "join_create"
            and event.get("join_id")
            and (event.get("attributes") or {}).get("mode") == "all"
            and event.get("member_invocation_ids")
        }
        returns = {
            event["invocation_id"]: float(event["ts_ms"])
            for event in workflow_events
            if event.get("kind") == "return" and event.get("invocation_id")
        }
        cancellations = {
            event["invocation_id"]: float(event["ts_ms"])
            for event in workflow_events
            if event.get("kind") == "invocation_cancel"
            and event.get("invocation_id")
        }
        for event in workflow_events:
            if event.get("invocation_id"):
                by_child[event["invocation_id"]].append(event)
        for child, events in by_child.items():
            events.sort(key=lambda item: float(item["ts_ms"]))
            first_content = {}
            submits = {}
            tool_chunks = {}
            results = {}
            for event in events:
                attrs = event.get("attributes") or {}
                request = attrs.get("request_id")
                if not request:
                    continue
                if event["kind"] == "llm_submit":
                    submits[request] = event
                elif event["kind"] == "llm_result":
                    results[request] = event
                elif event["kind"] == "structured_action":
                    if attrs.get("beliefkv_child_first_content_shadow"):
                        first_content[request] = event
                    if attrs.get("beliefkv_child_first_tool_chunk_shadow"):
                        tool_chunks[request] = event
            for index, event in enumerate(events):
                attrs = event.get("attributes") or {}
                if (
                    event["kind"] != "structured_action"
                    or not attrs.get("beliefkv_child_substantial_content_shadow")
                    or attrs.get("content_threshold_chars") != 1024
                ):
                    continue
                request = attrs.get("request_id")
                submit = submits.get(request)
                result = results.get(request)
                first = first_content.get(request)
                if submit is None or first is None or result is None:
                    censored += 1
                    continue
                now = float(event["ts_ms"]) + DELAY_MS
                chunk = tool_chunks.get(request)
                if (
                    now >= float(result["ts_ms"])
                    or chunk is not None and float(chunk["ts_ms"]) <= now
                ):
                    continue
                successor = next((
                    later for later in events[index + 1:]
                    if float(later["ts_ms"]) > float(result["ts_ms"])
                    and later["kind"] in {
                        "return", "invocation_cancel", "llm_submit", "tool_start"
                    }
                ), None)
                if successor is None:
                    censored += 1
                    continue
                current = float(event["ts_ms"])
                submitted = float(submit["ts_ms"])
                started = float(first["ts_ms"])
                if not submitted <= started <= current:
                    continue
                group = joins.get(event.get("join_id"))
                last_outstanding = False
                if group is not None:
                    created, members = group
                    last_outstanding = (
                        created <= now and child in members
                        and all(
                            member == child or returns.get(member, math.inf) < now
                            for member in members
                        )
                        and all(
                            cancellations.get(member, math.inf) >= now
                            for member in members
                        )
                    )
                previous = events[:index]
                prior_rounds = sum(
                    prior["kind"] == "llm_submit"
                    and float(prior["ts_ms"]) < submitted
                    for prior in previous
                )
                prior_tools = sum(
                    prior["kind"] == "tool_end"
                    and float(prior["ts_ms"]) < submitted
                    for prior in previous
                )
                outcome = result.get("attributes") or {}
                final = (
                    successor["kind"] == "return"
                    and not outcome.get("runtime_internal")
                    and int(outcome.get("output_chars") or 0) > 0
                    and outcome.get("tool_call_count") == 0
                    and outcome.get("invalid_tool_call_count", 0) == 0
                    and outcome.get("finish_reason") in (None, "stop")
                )
                known.append({
                    "trace_path": str(path),
                    "workflow": event["workflow_id"],
                    "child": child,
                    "trigger_ms": now,
                    "join_last_outstanding": last_outstanding,
                    "features": [
                        math.log1p((current - started) / 1000),
                        math.log1p((current - submitted) / 1000),
                        math.log1p(prior_rounds),
                        math.log1p(prior_tools),
                    ],
                    "final": final,
                    "return_lead_ms": (
                        float(successor["ts_ms"]) - now if final else None
                    ),
                })
    first_by_child = {}
    for sample in sorted(known, key=lambda row: row["trigger_ms"]):
        first_by_child.setdefault(
            (sample["trace_path"], sample["workflow"], sample["child"]), sample
        )
    return list(first_by_child.values()), censored, last_children


def _features(row: dict, *, join_aware: bool) -> list[float]:
    return row["features"] + (
        [float(row["join_last_outstanding"])] if join_aware else []
    )


def _fit(training: list[dict], *, join_aware: bool = False):
    matrix = np.array(
        [_features(row, join_aware=join_aware) for row in training], dtype=float
    )
    labels = np.array([row["final"] for row in training], dtype=float)
    if len(training) < 20 or not 0 < labels.sum() < len(labels):
        raise ValueError("insufficient positive and negative training episodes")
    mean = matrix.mean(axis=0)
    scale = np.maximum(matrix.std(axis=0), .1)
    matrix = (matrix - mean) / scale
    design = np.column_stack((np.ones(len(matrix)), matrix))

    def loss(weights):
        logits = design @ weights
        regularizer = .5 * (weights[1:] @ weights[1:])
        value = np.logaddexp(0, logits).sum() - labels @ logits + regularizer
        gradient = design.T @ (expit(logits) - labels)
        gradient[1:] += weights[1:]
        return value, gradient

    fitted = minimize(
        loss, np.zeros(design.shape[1]), jac=True, method="L-BFGS-B"
    )
    if not fitted.success:
        raise ValueError(f"optimizer failed: {fitted.message}")
    return fitted.x, mean, scale


def _scores(rows: list[dict], model, *, join_aware: bool = False) -> np.ndarray:
    if not rows:
        return np.array([])
    weights, mean, scale = model
    features = (
        np.array([_features(row, join_aware=join_aware) for row in rows])
        - mean
    ) / scale
    return expit(np.column_stack((np.ones(len(rows)), features)) @ weights)


def _quality(
    rows: list[dict], scores: np.ndarray, threshold: float,
    last_children: set[tuple[str, str, str]],
    *, eta_prior_ms: float | None = None,
) -> dict:
    selected = [row for row, score in zip(rows, scores) if score >= threshold]
    true = [row for row in selected if row["final"]]
    positives = sum(row["final"] for row in rows)
    leads = [row["return_lead_ms"] for row in true]
    selected_last_leads = [
        row["return_lead_ms"] for row in true
        if (row["trace_path"], row["workflow"], row["child"])
        in last_children
    ]
    raw_last = sum(
        row["final"]
        and (row["trace_path"], row["workflow"], row["child"])
        in last_children
        for row in rows
    )
    return {
        "evaluated_children": len(rows),
        "true_return_children": positives,
        "raw_candidate_precision": positives / len(rows) if rows else None,
        "raw_candidate_last_child_recall": (
            raw_last / len(last_children) if last_children else None
        ),
        "selected": len(selected),
        "true_selected": len(true),
        "precision": len(true) / len(selected) if selected else None,
        "return_recall": len(true) / positives if positives else None,
        "lead_at_least_2000ms": sum(
            row["return_lead_ms"] >= 2000 for row in true
        ),
        "lead_p50_ms": median(leads) if leads else None,
        "fixed_eta_prior_ms": eta_prior_ms,
        "fixed_eta_error_p50_ms": (
            median(abs(lead - eta_prior_ms) for lead in leads)
            if leads and eta_prior_ms is not None else None
        ),
        "fixed_eta_within_500ms": (
            sum(abs(lead - eta_prior_ms) <= 500 for lead in leads)
            if eta_prior_ms is not None else None
        ),
        "eligible_last_children": len(last_children),
        "selected_true_last_children": sum(
            (row["trace_path"], row["workflow"], row["child"]) in last_children
            for row in true
        ),
        "selected_last_child_lead_p50_ms": (
            median(selected_last_leads) if selected_last_leads else None
        ),
        "selected_last_child_lead_at_least_2000ms": sum(
            lead >= 2000 for lead in selected_last_leads
        ),
        "last_child_recall": (
            sum(
                (row["trace_path"], row["workflow"], row["child"])
                in last_children
                for row in true
            ) / len(last_children)
            if last_children else None
        ),
        "false_examples": [
            {"workflow": row["workflow"], "child": row["child"]}
            for row in selected if not row["final"]
        ][:12],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, action="append", required=True)
    parser.add_argument("--evaluate-workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--join-aware", action="store_true")
    args = parser.parse_args()
    training = []
    train_last_children = set()
    censored_train = 0
    for directory in args.train_workflows:
        rows, censored, last_children = samples(directory)
        training.extend(rows)
        censored_train += censored
        train_last_children.update(last_children)
    evaluation, censored_evaluation, eval_last_children = samples(
        args.evaluate_workflows
    )
    model = _fit(training, join_aware=args.join_aware)
    scores = _scores(training, model, join_aware=args.join_aware)
    thresholds = sorted(set(scores), reverse=True)
    acceptable = [
        value for value in thresholds
        if (chosen := scores >= value).sum() >= MIN_SELECTED
        and np.mean([
            row["final"] for row, flag in zip(training, chosen) if flag
        ]) >= MIN_PRECISION
    ]
    if not acceptable:
        report = {"status": "no_acceptable_development_threshold"}
    else:
        threshold = min(acceptable)
        training_quality = _quality(
            training, scores, threshold, train_last_children
        )
        report = {
            "status": "read_only_stream_pilot",
            "threshold": float(threshold),
            "training": training_quality,
            "task_holdout": _quality(
                evaluation,
                _scores(evaluation, model, join_aware=args.join_aware),
                threshold,
                eval_last_children,
                eta_prior_ms=training_quality["lead_p50_ms"],
            ),
        }
    report.update({
        "train_censored": censored_train,
        "evaluate_censored": censored_evaluation,
        "feature_names": (
            "first_content_to_1024_ms", "submit_to_1024_ms",
            "prior_model_rounds", "prior_tool_ends",
        ) + (("join_last_outstanding",) if args.join_aware else ()),
        "observation_delay_ms": DELAY_MS,
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
