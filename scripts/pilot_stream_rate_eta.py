#!/usr/bin/env python3
"""Test whether within-request stream speed improves actionable RETURN ETA."""

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
from scripts.audit_native_stream_shadow import _rows, _satisfied_last_children


def samples(workflows: Path) -> list[dict]:
    rows = []
    for path in sorted(workflows.glob("*/runtime_events.deepagents.jsonl")):
        events = list(_rows(path))
        last = _satisfied_last_children(events)
        per_child = defaultdict(list)
        for event in events:
            if event.get("invocation_id"):
                per_child[event["invocation_id"]].append(event)
        for child, child_events in per_child.items():
            requests = defaultdict(dict)
            for event in child_events:
                attrs = event.get("attributes") or {}
                request = attrs.get("request_id")
                if not request:
                    continue
                if event["kind"] == "llm_result":
                    requests[request]["result"] = event
                if event["kind"] == "structured_action":
                    if attrs.get("beliefkv_child_first_tool_chunk_shadow"):
                        requests[request]["tool_chunk"] = float(event["ts_ms"])
                    if attrs.get("beliefkv_child_substantial_content_shadow"):
                        threshold = attrs.get("content_threshold_chars")
                        if threshold in (1024, 1700):
                            requests[request][threshold] = float(event["ts_ms"])
            for request in requests.values():
                if not all(key in request for key in (1024, 1700, "result")):
                    continue
                result = request["result"]
                now = request[1700] + 250
                if (
                    now >= float(result["ts_ms"])
                    or request.get("tool_chunk", float("inf")) <= now
                ):
                    continue
                successor = next((
                    event for event in child_events
                    if float(event["ts_ms"]) > float(result["ts_ms"])
                    and event["kind"] in {
                        "return", "invocation_cancel", "llm_submit", "tool_start"
                    }
                ), None)
                attrs = result.get("attributes") or {}
                if successor is None or not (
                    successor["kind"] == "return"
                    and int(attrs.get("output_chars") or 0) > 0
                    and not attrs.get("runtime_internal")
                    and attrs.get("tool_call_count") == 0
                    and attrs.get("invalid_tool_call_count", 0) == 0
                    and attrs.get("finish_reason") in (None, "stop")
                ):
                    continue
                rows.append({
                    "rate_interval_ms": max(0, request[1700] - request[1024]),
                    "lead_ms": float(successor["ts_ms"]) - now,
                    "join_last_child": (result["workflow_id"], child) in last,
                })
    return rows


def evaluate(train: list[dict], holdout: list[dict]) -> dict:
    if len(train) < 10 or not holdout:
        raise ValueError("insufficient completed stream responses")
    prior = median(row["lead_ms"] for row in train)
    x = np.array([row["rate_interval_ms"] for row in train])
    y = np.array([row["lead_ms"] for row in train])
    # Scale-independent slope, capped to avoid a handful of slow streams
    # turning noisy chunk timestamps into an unbounded extrapolation.
    slope = float(np.clip(
        np.dot(x - x.mean(), y - y.mean())
        / max(np.dot(x - x.mean(), x - x.mean()), 1), 0, 8,
    ))
    intercept = float(np.median(y - slope * x))
    report = {
        "train_samples": len(train), "holdout_samples": len(holdout),
        "fixed_prior_ms": prior, "rate_slope": slope,
        "rate_intercept_ms": intercept, "cohorts": {},
    }
    for name, rows in (
        ("all_returning_children", holdout),
        ("last_child_join", [r for r in holdout if r["join_last_child"]]),
    ):
        errors = {
            "fixed": [abs(row["lead_ms"] - prior) for row in rows],
            "rate": [abs(
                row["lead_ms"] - max(0, intercept + slope * row["rate_interval_ms"])
            ) for row in rows],
        }
        report["cohorts"][name] = {
            "count": len(rows),
            "lead_p50_ms": median(r["lead_ms"] for r in rows) if rows else None,
            **{
                f"{model}_mae_p50_ms": median(values) if values else None
                for model, values in errors.items()
            },
            **{
                f"{model}_within_500ms": sum(e <= 500 for e in values)
                for model, values in errors.items()
            },
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, action="append", required=True)
    parser.add_argument("--evaluate-workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    train = [
        row for directory in args.train_workflows for row in samples(directory)
    ]
    report = evaluate(train, samples(args.evaluate_workflows))
    report["status"] = "read_only_rate_eta_holdout_diagnostic"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
