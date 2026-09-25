#!/usr/bin/env python3
"""Causal replay of same-input tool timing, with project-held-out priors."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
from statistics import median

import orjson


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * q) - 1)]


def _summarize(rows: list[dict]) -> dict:
    errors = [row["error_ms"] for row in rows]
    projects = defaultdict(list)
    workflows = defaultdict(list)
    for row in rows:
        projects[row["project"]].append(row)
        workflows[row["workflow"]].append(row["error_ms"])
    counts = Counter(row["workflow"] for row in rows)
    return {
        "count": len(rows),
        "p50_error_ms": _quantile(errors, .5),
        "p90_error_ms": _quantile(errors, .9),
        "p95_error_ms": _quantile(errors, .95),
        "within_500ms": sum(e <= 500 for e in errors) / len(errors) if errors else None,
        "workflow_weighted_p50_ms": _quantile(
            [median(values) for values in workflows.values()], .5
        ),
        "workflow_count": len(workflows),
        "top_5_workflow_share": (
            sum(value for _, value in counts.most_common(5)) / len(rows)
            if rows else None
        ),
        "by_project": {
            project: {
                "count": len(items),
                "p50_error_ms": _quantile([item["error_ms"] for item in items], .5),
                "p90_error_ms": _quantile([item["error_ms"] for item in items], .9),
            }
            for project, items in sorted(projects.items())
        },
    }


def _read_workflow(
    path: Path, *, allow_legacy_origin: bool = False,
    history_scope: str = "invocation",
) -> list[dict]:
    if history_scope not in ("invocation", "workflow"):
        raise ValueError("unsupported history scope")
    events = []
    with path.open("rb") as stream:
        for line in stream:
            if line.strip():
                event = orjson.loads(line)
                if event.get("kind") in ("tool_start", "tool_end"):
                    events.append(event)
    # The callback's timestamp, not file append order, is the availability clock.
    events.sort(key=lambda event: (float(event["ts_ms"]), event["sequence"]))
    started = {}
    history = {}
    completed = []
    for event in events:
        attrs = event.get("attributes") or {}
        call_id = str(attrs.get("tool_call_id") or "")
        if not call_id or attrs.get("tool_name") != "execute":
            continue
        ts = float(event["ts_ms"])
        if event["kind"] == "tool_start":
            signature = str(attrs.get("input_sha256") or "")
            invocation = str(event.get("invocation_id") or "")
            key = (invocation if history_scope == "invocation" else "*", signature)
            previous = history.get(key) if signature and invocation else None
            started[call_id] = (ts, attrs, previous)
        elif call_id in started:
            start_ts, start_attrs, previous = started.pop(call_id)
            duration = ts - start_ts
            if duration < 0:
                raise ValueError(f"negative tool duration in {path}")
            origin = start_attrs.get("is_child")
            if origin is None and allow_legacy_origin:
                origin = str(event.get("invocation_id") or "").startswith(
                    "deepagents-invocation:"
                )
            row = {
                "project": path.parent.name.split("__", 1)[0],
                "workflow": str(event["workflow_id"]),
                "invocation": str(event.get("invocation_id") or ""),
                "class": str(start_attrs.get("observed_command_class") or "unknown"),
                "input_sha256": str(start_attrs.get("input_sha256") or ""),
                "duration_ms": duration,
                "terminal_ts_ms": ts,
                "is_child": origin,
                "status": str(attrs.get("status") or "unknown"),
                "previous": previous,
                "start_ts_ms": start_ts,
            }
            completed.append(row)
            signature = str(start_attrs.get("input_sha256") or "")
            if signature and row["invocation"]:
                history[(
                    row["invocation"] if history_scope == "invocation" else "*",
                    signature,
                )] = (
                    duration, ts, row["status"]
                )
    return completed


def replay(
    workflows: Path, *, minimum_class_samples: int = 8,
    history_scope: str = "invocation",
) -> dict:
    if minimum_class_samples < 1:
        raise ValueError("minimum_class_samples must be positive")
    rows = []
    for path in sorted(workflows.glob("*/runtime_events.deepagents.jsonl")):
        rows.extend(_read_workflow(path, history_scope=history_scope))
    projects = sorted({row["project"] for row in rows})
    if len(projects) < 2:
        raise ValueError("project-held-out analysis requires at least two projects")
    results = defaultdict(list)
    counts = defaultdict(int)
    for project in projects:
        train = [r for r in rows if r["project"] != project]
        eval_rows = [r for r in rows if r["project"] == project]
        by_class = defaultdict(list)
        for row in train:
            by_class[row["class"]].append(row["duration_ms"])
        global_duration = median(row["duration_ms"] for row in train)
        priors = {
            name: median(values)
            for name, values in by_class.items() if len(values) >= minimum_class_samples
        }
        for row in eval_rows:
            counts["all_calls"] += 1
            previous = row["previous"]
            baseline = priors.get(row["class"], global_duration)
            hybrid = (
                previous[0] if previous is not None and previous[2] == "success"
                else baseline
            )
            aggregate = ["all_calls"]
            if row["is_child"] is True:
                aggregate.append("all_child_calls")
            if row["duration_ms"] >= 2_000:
                aggregate.append("all_long_calls")
                if row["is_child"] is True:
                    aggregate.append("all_long_child_calls")
                    if previous is None or previous[2] != "success":
                        counts["long_child_without_successful_prior"] += 1
            for dimension in aggregate:
                results[dimension + ":class"].append({
                    **row, "error_ms": abs(row["duration_ms"] - baseline)
                })
                results[dimension + ":hybrid"].append({
                    **row, "error_ms": abs(row["duration_ms"] - hybrid)
                })
            if previous is None:
                counts["no_completed_same_input"] += 1
                continue
            prev_duration, prev_end, prev_status = previous
            if prev_end >= row["start_ts_ms"]:
                raise ValueError("future tool result leaked into current prediction")
            counts["has_completed_same_input"] += 1
            status_matches = row["status"] == prev_status
            if not status_matches:
                counts["outcome_changed"] += 1
            age = row["start_ts_ms"] - prev_end
            dimensions = ["all_repeats"]
            dimensions.append(
                "child" if row["is_child"] is True
                else "root" if row["is_child"] is False
                else "origin_unknown"
            )
            if row["duration_ms"] >= 2_000:
                dimensions.append("actual_at_least_2s")
                if row["is_child"] is True:
                    dimensions.append("child_actual_at_least_2s")
            if prev_duration >= 2_000:
                dimensions.append("predicted_at_least_2s")
                if row["is_child"] is True:
                    dimensions.append("child_predicted_at_least_2s")
                if row["duration_ms"] < 2_000:
                    counts["long_prediction_false_positive"] += 1
                    if row["is_child"] is True:
                        counts["child_long_prediction_false_positive"] += 1
            if status_matches:
                dimensions.append("same_outcome")
            else:
                dimensions.append("changed_outcome")
            dimensions.append(
                "prior_success" if prev_status == "success" else "prior_non_success"
            )
            if age > 60_000:
                dimensions.append("older_than_1min")
            else:
                dimensions.append("within_1min")
            if row["status"] not in ("success", "ok", "completed"):
                dimensions.append("non_success")
            for dimension in dimensions:
                results[dimension + ":previous"].append({
                    **row, "error_ms": abs(row["duration_ms"] - prev_duration)
                })
                results[dimension + ":class"].append({
                    **row, "error_ms": abs(row["duration_ms"] - baseline)
                })
            # Premature actions at prior duration P50 are unsafe without a
            # calibrated residual interval and useful lead-time evidence.
            if prev_duration < row["duration_ms"] - 500:
                counts["previous_more_than_500ms_early"] += 1
            if prev_duration > row["duration_ms"] + 500:
                counts["previous_more_than_500ms_late"] += 1
            if prev_duration >= 2_000 and row["is_child"] is True:
                for lead_budget in (500, 750, 1000, 1500):
                    # Negative latest-starts trigger at TOOL_START, not before it.
                    trigger_ts = max(0.0, prev_duration - lead_budget)
                    lead = row["duration_ms"] - trigger_ts
                    prefix = f"child_forecast_budget_{lead_budget}ms"
                    if lead < 0:
                        counts[f"{prefix}_after_return"] += 1
                    elif lead >= 500:
                        counts[f"{prefix}_at_least_500ms_lead"] += 1
                    else:
                        counts[f"{prefix}_under_500ms_lead"] += 1
                    if lead > 2000:
                        counts[f"{prefix}_more_than_2s_early"] += 1
    return {
        "status": "offline_causal_replay_not_deployable",
        "history_scope": history_scope,
        "project_count": len(projects),
        "projects": projects,
        "counts": dict(sorted(counts.items())),
        "metrics": {name: _summarize(value) for name, value in sorted(results.items())},
    }


def transfer_replay(
    train_workflows: Path, evaluation_workflows: Path,
    *, allow_legacy_evaluation_origin: bool = False,
    history_scope: str = "invocation",
) -> dict:
    train = [
        row for path in sorted(train_workflows.glob("*/runtime_events.deepagents.jsonl"))
        for row in _read_workflow(path, history_scope=history_scope)
        if row["previous"] is not None and row["previous"][2] == "success"
    ]
    evaluation = [
        row for path in sorted(evaluation_workflows.glob("*/runtime_events.deepagents.jsonl"))
        for row in _read_workflow(
            path, allow_legacy_origin=allow_legacy_evaluation_origin,
            history_scope=history_scope,
        )
        if row["previous"] is not None and row["previous"][2] == "success"
    ]
    if not train or not evaluation:
        raise ValueError("both groups require prior-success repeat samples")
    if {row["project"] for row in train} & {row["project"] for row in evaluation}:
        raise ValueError("train/evaluation project overlap")
    train_errors = [
        abs(row["duration_ms"] - row["previous"][0]) for row in train
    ]
    margin = _quantile(train_errors, .9)
    rows = []
    for row in evaluation:
        predicted = row["previous"][0]
        rows.append({
            **row, "error_ms": abs(row["duration_ms"] - predicted)
        })
    per_workflow = defaultdict(list)
    for row in rows:
        per_workflow[row["workflow"]].append(row["error_ms"])
    return {
        "status": "cross_run_project_disjoint_diagnostic_not_formal_test",
        "history_scope": history_scope,
        "legacy_evaluation_origin_inferred": allow_legacy_evaluation_origin,
        "train_repeat_count": len(train),
        "evaluation_repeat_count": len(rows),
        "train_p90_absolute_residual_ms": margin,
        "evaluation": _summarize(rows),
        "evaluation_within_train_p90_margin": (
            sum(row["error_ms"] <= margin for row in rows) / len(rows)
        ),
        "workflow_all_repeats_within_margin": (
            sum(max(errors) <= margin for errors in per_workflow.values())
            / len(per_workflow)
        ),
        "evaluation_actual_at_least_2s": _summarize([
            row for row in rows if row["duration_ms"] >= 2_000
        ]),
        "evaluation_child": _summarize([
            row for row in rows if row["is_child"] is True
        ]),
        "evaluation_child_at_least_2s": _summarize([
            row for row in rows if row["is_child"] is True
            and row["duration_ms"] >= 2_000
        ]),
        "predicted_at_least_2s_count": sum(
            row["previous"][0] >= 2_000 for row in rows
        ),
        "predicted_long_false_positive_count": sum(
            row["previous"][0] >= 2_000 and row["duration_ms"] < 2_000
            for row in rows
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--evaluation-workflows", type=Path)
    parser.add_argument("--allow-legacy-evaluation-origin", action="store_true")
    parser.add_argument(
        "--history-scope", choices=("invocation", "workflow"),
        default="invocation",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.workflows, history_scope=args.history_scope)
    if args.evaluation_workflows is not None:
        result["project_isolated_transfer"] = transfer_replay(
            args.workflows, args.evaluation_workflows,
            allow_legacy_evaluation_origin=args.allow_legacy_evaluation_origin,
            history_scope=args.history_scope,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"counts": result["counts"], "cross_run": result.get(
        "project_isolated_transfer"
    ), "all_repeats": {
        name: result["metrics"].get("all_repeats:" + name)
        for name in ("previous", "class")
    }}, indent=2))


if __name__ == "__main__":
    main()
