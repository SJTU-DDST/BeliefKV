#!/usr/bin/env python3
"""Project-held-out child RETURN ETA after causally observed GPU service."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

import numpy as np
import orjson

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_child_return_intent_timing import _metrics


DECODE_THRESHOLDS = (128, 512, 1024)
CHECKPOINTS = ("first_service_complete",) + tuple(
    f"decode_{count}" for count in DECODE_THRESHOLDS
)


def _notice_hints(client: Path) -> dict[tuple[str, str], int]:
    hints = {}
    for path in sorted(client.glob("workflows/*/runtime_events.deepagents.jsonl")):
        with path.open("rb") as stream:
            for line in stream:
                row = orjson.loads(line)
                attrs = row.get("attributes") or {}
                if (
                    row.get("kind") == "structured_action"
                    and attrs.get("child_completion_signal_kind") == "stage"
                    and attrs.get("beliefkv_child_completion_intent") is True
                    and type(attrs.get("estimated_final_report_tokens")) is int
                ):
                    hints[(path.parent.name, row["invocation_id"])] = attrs[
                        "estimated_final_report_tokens"
                    ]
    return hints


def collect(run: Path) -> tuple[list[dict], dict]:
    labels = run / "final_stage_service_labels.jsonl"
    audit = run / "server/runtime_audit.jsonl"
    client = run / "client_36"
    if not client.is_dir():
        candidates = list(run.glob("client_*"))
        if len(candidates) != 1:
            raise ValueError("expected exactly one client directory")
        client = candidates[0]
    hints = _notice_hints(client)
    targets = {}
    with labels.open("rb") as stream:
        for line in stream:
            label = orjson.loads(line)
            rid = label["final_request_id"]
            if rid in targets:
                raise ValueError(f"duplicate final request: {rid}")
            targets[rid] = label
    rows, first, last, last_tokens, seen = [], {}, {}, {}, defaultdict(set)
    rejected = Counter()
    with audit.open("rb") as stream:
        for line in stream:
            event = orjson.loads(line)
            if event.get("event") != "gpu_service_sample":
                continue
            when = event.get("complete_ts_ms")
            start = event.get("service_start_ts_ms")
            if type(when) not in (int, float) or type(start) not in (int, float):
                continue
            if when < start:
                continue
            for sample in event.get("request_samples") or ():
                rid = sample.get("request_id")
                label = targets.get(rid)
                if label is None:
                    continue
                if (
                    sample.get("workflow_id") != label["workflow_id"]
                    or sample.get("invocation_id") != label["child_id"]
                ):
                    rejected["identity_mismatch"] += 1
                    continue
                result = label["server_result_ts_ms"]
                if when > result:
                    continue
                first.setdefault(rid, label["server_first_service_ts_ms"])
                if start < first[rid]:
                    continue
                previous = last.get(rid)
                if previous is not None and when < previous:
                    rejected["nonmonotonic_service"] += 1
                    continue
                token_count = sample.get("output_tokens_before")
                delta = sample.get("token_delta")
                if (
                    type(token_count) is not int or token_count < 0
                    or type(delta) is not int or delta < 0
                ):
                    rejected["missing_output_progress"] += 1
                    continue
                token_count += delta if event.get("phase") == "decode" else 0
                prior_tokens = last_tokens.get(rid, 0)
                rate = (
                    max(0.0, token_count - prior_tokens) * 1000.0
                    / max(1.0, when - previous)
                    if previous is not None and event.get("phase") == "decode"
                    else 0.0
                )
                last[rid], last_tokens[rid] = when, token_count
                reached = ["first_service_complete"]
                if event.get("phase") == "decode":
                    reached.extend(
                        f"decode_{limit}" for limit in DECODE_THRESHOLDS
                        if token_count >= limit
                    )
                task = label["workflow_id"].split(":")[2]
                estimate = hints.get((task, label["child_id"]))
                for checkpoint in reached:
                    if checkpoint in seen[rid]:
                        continue
                    seen[rid].add(checkpoint)
                    elapsed = max(0.0, when - first[rid])
                    remaining = result - when + label["client_result_to_return_ms"]
                    if remaining <= 0:
                        continue
                    rows.append({
                        "request_id": rid,
                        "project": task.split("__", 1)[0],
                        "task": task,
                        "checkpoint": checkpoint,
                        "remaining_ms_lower_bound": remaining,
                        "elapsed_since_first_ms": elapsed,
                        "features": [
                            label["notice_to_first_service_lower_bound_ms"],
                            elapsed,
                            token_count,
                            max(0.0, start - (previous or start)),
                            estimate or 0,
                            int(estimate is not None),
                            max(0, (estimate or 0) - token_count),
                            rate,
                        ],
                    })
    return rows, {
        "paired_final_requests": len(targets),
        "observed_requests": len({row["request_id"] for row in rows}),
        "notice_hints": sum(
            (label["workflow_id"].split(":")[2], label["child_id"]) in hints
            for label in targets.values()
        ),
        "checkpoint_counts": dict(Counter(row["checkpoint"] for row in rows)),
        "rejected": dict(rejected),
    }


def _predict(
    train: list[dict], test: list[dict], *, feature_count: int,
) -> list[float]:
    x = np.log1p(np.asarray([
        row["features"][:feature_count] for row in train
    ], dtype=float))
    z = np.log1p(np.asarray([
        row["features"][:feature_count] for row in test
    ], dtype=float))
    center = x.mean(axis=0)
    scale = np.maximum(x.std(axis=0), 1.0)
    x, z = (x - center) / scale, (z - center) / scale
    y = np.log1p([row["remaining_ms_lower_bound"] for row in train])
    prior = float(np.median(y))
    weights = np.asarray([
        1.0 / sum(other["task"] == row["task"] for other in train)
        for row in train
    ])
    ridge = 8.0 * np.eye(x.shape[1])
    fitted = np.linalg.solve(
        x.T @ (weights[:, None] * x) + ridge,
        x.T @ (weights * (y - prior)),
    )
    return list(np.maximum(0.0, np.expm1(prior + z @ fitted)))


def evaluate(run: Path) -> dict:
    rows, coverage = collect(run)
    results = {}
    for checkpoint in CHECKPOINTS:
        candidate = [row for row in rows if row["checkpoint"] == checkpoint]
        predictions = {
            "train_median": [], "causal_ridge": [], "progress_hint_ridge": [],
        }
        actual, evaluated, folds = [], [], []
        for project in sorted({row["project"] for row in candidate}):
            train = [row for row in candidate if row["project"] != project]
            test = [row for row in candidate if row["project"] == project]
            if len(train) < 3 or len({r["project"] for r in train}) < 2:
                continue
            prior = float(np.median([
                row["remaining_ms_lower_bound"] for row in train
            ]))
            predictions["train_median"].extend([prior] * len(test))
            predictions["causal_ridge"].extend(
                _predict(train, test, feature_count=6)
            )
            predictions["progress_hint_ridge"].extend(
                _predict(train, test, feature_count=8)
            )
            actual.extend(row["remaining_ms_lower_bound"] for row in test)
            evaluated.extend(test)
            folds.append({"project": project, "train": len(train), "test": len(test)})
        results[checkpoint] = {
            "observed": len(candidate),
            "evaluated": len(evaluated),
            "projects": folds,
            "actual_lead_at_least_500ms": sum(value >= 500 for value in actual),
            "train_median": _metrics(actual, predictions["train_median"]),
            "causal_ridge": _metrics(actual, predictions["causal_ridge"]),
            "progress_hint_ridge": _metrics(
                actual, predictions["progress_hint_ridge"]
            ),
        }
    return {
        "status": "offline_project_loo_development_not_online_eligible",
        "scope": (
            "Only successful child notices uniquely paired with a natural final "
            "request and GPU service are included. The outcome is a lower bound "
            "on child RETURN time; server-result to client-result delivery is "
            "missing. Signals are taken at service completion, never at service "
            "start or from future output. Training excludes the held-out project. "
            "This does not measure notification-time ETA, all child RETURNs, "
            "false terminal signals, or physical prefetch benefit."
        ),
        "coverage": coverage,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(args.run)
    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
