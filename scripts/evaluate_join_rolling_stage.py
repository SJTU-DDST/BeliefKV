#!/usr/bin/env python3
"""Read-only project-held-out JOIN clock at a causally sole-pending child stage."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_child_intent_project_holdout import _metrics
from scripts.evaluate_child_intent_stream_milestones import collect as collect_stages
from scripts.evaluate_cold_tool_project_loo import require_complete_batch
from scripts.evaluate_join_group_notice import collect as collect_groups


THRESHOLDS = (1024, 1700)
OBSERVED_THRESHOLDS = THRESHOLDS + (2400, 3200, 4200, 5600, 7000)


def _sole_pending(
    events: list[dict], stage: dict, members: set[str], *,
    join_id: str, created: float, waited: float,
    satisfied: float,
) -> bool:
    when = stage["signal_ts_ms"]
    child = stage["invocation_id"]
    if child not in members or not (max(created, waited) <= when < satisfied):
        return False
    returned = {
        event["invocation_id"] for event in events
        if event["kind"] == "return" and event.get("invocation_id") in members
        and float(event["ts_ms"]) < when
    }
    cancelled = any(
        event["kind"] == "invocation_cancel"
        and event.get("invocation_id") in members
        and float(event["ts_ms"]) <= when
        for event in events
    )
    if cancelled or members - returned != {child}:
        return False
    return any(
        event["kind"] == "structured_action"
        and event.get("join_id") == join_id
        and event.get("invocation_id") == child
        and event.get("context_id") == stage["context_id"]
        and event.get("context_epoch") == stage["context_epoch"]
        and float(event["ts_ms"]) == when
        and (event.get("attributes") or {}).get("request_id")
        == stage["request_id"]
        and (event.get("attributes") or {}).get("content_threshold_chars")
        == stage["stage_threshold_chars"]
        and (event.get("attributes") or {}).get(
            "beliefkv_child_substantial_content_shadow"
        )
        for event in events
    )


def collect(workflows: Path, threshold: int) -> tuple[list[dict], dict]:
    if threshold not in OBSERVED_THRESHOLDS:
        raise ValueError("unsupported frozen stage threshold")
    groups, counts = collect_groups(workflows)
    stages, stage_counts = collect_stages(workflows, threshold)
    by_task = defaultdict(list)
    for stage in stages:
        by_task[stage["task_id"]].append(stage)
    selected = []
    for path in sorted(workflows.glob("*/runtime_events.deepagents.jsonl")):
        with path.open(encoding="utf-8") as stream:
            events = [json.loads(line) for line in stream if line.strip()]
        group_by_id = {
            group["join_id"]: group for group in groups
            if group["task_id"] == path.parent.name
        }
        joins = {
            event["join_id"]: event for event in events
            if event["kind"] == "join_create" and event.get("join_id")
            and (event.get("attributes") or {}).get("mode") == "all"
        }
        waits = {
            event["join_id"]: min(
                float(item["ts_ms"]) for item in events
                if item["kind"] == "join_wait"
                and item.get("join_id") == event["join_id"]
            ) for event in events
            if event["kind"] == "join_wait" and event.get("join_id")
        }
        satisfied = {
            event["join_id"]: float(event["ts_ms"]) for event in events
            if event["kind"] == "join_satisfied" and event.get("join_id")
        }
        for stage in by_task[path.parent.name]:
            for join_id, join in joins.items():
                group = group_by_id.get(join_id)
                if group is None or join_id not in waits:
                    continue
                members = set(join.get("member_invocation_ids") or ())
                if stage["signal_ts_ms"] < group["trigger_ts_ms"]:
                    continue
                if not _sole_pending(
                    events, stage, members, join_id=join_id,
                    created=float(join["ts_ms"]), waited=waits[join_id],
                    satisfied=satisfied.get(join_id, float("inf")),
                ):
                    continue
                selected.append({
                    **stage,
                    "join_id": join_id,
                    "group_label": group["label"],
                    "join_lead_ms": (
                        group["lead_ms"] + group["trigger_ts_ms"]
                        - stage["signal_ts_ms"]
                        if group["lead_ms"] is not None else None
                    ),
                    "parent_lead_ms": (
                        group["parent_reentry_lead_ms"]
                        + group["trigger_ts_ms"] - stage["signal_ts_ms"]
                        if group["parent_reentry_lead_ms"] is not None else None
                    ),
                })
    first = {}
    for row in sorted(selected, key=lambda item: item["signal_ts_ms"]):
        first.setdefault((row["task_id"], row["join_id"]), row)
    return list(first.values()), {
        "groups": counts,
        "stages": stage_counts,
        "sole_pending_candidates": len(first),
        "sole_pending_natural": sum(
            row["group_label"] == "natural" and row["label"] == "true"
            for row in first.values()
        ),
        "sole_pending_parent_reentry": sum(
            row["parent_lead_ms"] is not None
            and row["group_label"] != "revoked"
            for row in first.values()
        ),
    }


def evaluate(
    train_workflows: Path, heldout_workflows: Path, *,
    frozen_train_ids: list[str] | None = None,
    frozen_heldout_ids: list[str] | None = None,
) -> dict:
    observed_train_tasks = {
        path.parent.name for path in train_workflows.glob(
            "*/runtime_events.deepagents.jsonl"
        )
    }
    observed_heldout_tasks = {
        path.parent.name for path in heldout_workflows.glob(
            "*/runtime_events.deepagents.jsonl"
        )
    }
    train_tasks = (
        set(frozen_train_ids) if frozen_train_ids is not None
        else observed_train_tasks
    )
    heldout_tasks = (
        set(frozen_heldout_ids) if frozen_heldout_ids is not None
        else observed_heldout_tasks
    )
    if not (
        observed_train_tasks <= train_tasks
        and observed_heldout_tasks <= heldout_tasks
    ):
        raise ValueError("observed JOIN tasks differ from frozen manifest")
    train_projects = {task.split("__", 1)[0] for task in train_tasks}
    heldout_projects = {task.split("__", 1)[0] for task in heldout_tasks}
    if (
        not train_tasks or not heldout_tasks or train_tasks & heldout_tasks
        or train_projects & heldout_projects
    ):
        raise ValueError("JOIN stage train and heldout must be disjoint projects")
    result = {
        "status": "read_only_project_disjoint_rolling_join_stage_not_online",
        "thresholds": list(THRESHOLDS),
        "train_projects": sorted(train_projects),
        "heldout_projects": sorted(heldout_projects),
        "scope": (
            "Only the first authenticated stage after the first full-notice "
            "candidate and while the parent waits and exactly one child is "
            "still pending. Other children must have returned before this "
            "stage; future RETURN/JOIN labels never choose a candidate. "
            "The task-balanced stage prior is trained on disjoint projects. "
            "A stage with less than 500 ms real lead cannot hide a 500 ms "
            "physical transfer even when its point error is small. "
            "This is an offline timing audit, not physical action eligibility."
        ),
    }
    for threshold in THRESHOLDS:
        train, train_counts = collect(train_workflows, threshold)
        heldout, heldout_counts = collect(heldout_workflows, threshold)
        natural_train = [
            row for row in train
            if row["group_label"] == "natural" and row["label"] == "true"
            and row["join_lead_ms"] is not None
        ]
        by_task = defaultdict(list)
        for row in natural_train:
            by_task[row["task_id"]].append(row["join_lead_ms"])
        prior = (
            median(median(values) for values in by_task.values())
            if by_task else None
        )
        parent_by_task = defaultdict(list)
        for row in train:
            if row["group_label"] != "revoked" and row["parent_lead_ms"] is not None:
                parent_by_task[row["task_id"]].append(row["parent_lead_ms"])
        parent_prior = (
            median(median(values) for values in parent_by_task.values())
            if parent_by_task else None
        )
        natural = [
            row for row in heldout
            if row["group_label"] == "natural" and row["label"] == "true"
            and row["join_lead_ms"] is not None
        ]
        parent = [
            row for row in heldout
            if row["parent_lead_ms"] is not None
            and row["group_label"] != "revoked"
        ]
        def summarize_project(project: str) -> dict:
            candidates = [row for row in heldout if row["project"] == project]
            project_natural = [
                row for row in natural if row["project"] == project
            ]
            project_parent = [
                row for row in parent if row["project"] == project
            ]
            join_actual = [row["join_lead_ms"] for row in project_natural]
            parent_actual = [row["parent_lead_ms"] for row in project_parent]
            return {
                "tasks": sum(
                    task.split("__", 1)[0] == project for task in heldout_tasks
                ),
                "first_sole_pending_candidates": len(candidates),
                "natural_join_candidates": len(project_natural),
                "parent_reentry_candidates": len(project_parent),
                "natural_join_lead_ms": _metrics(
                    join_actual, [0.] * len(join_actual),
                ),
                "natural_join_point_error_ms": (
                    _metrics(join_actual, [prior] * len(join_actual))
                    if prior is not None else None
                ),
                "parent_reentry_lead_ms": _metrics(
                    parent_actual, [0.] * len(parent_actual),
                ),
                "parent_reentry_point_error_ms": (
                    _metrics(parent_actual, [parent_prior] * len(parent_actual))
                    if parent_prior is not None else None
                ),
                "natural_join_lead_at_least_500ms": sum(
                    value >= 500 for value in join_actual
                ),
                "parent_reentry_lead_at_least_500ms": sum(
                    value >= 500 for value in parent_actual
                ),
            }
        result[str(threshold)] = {
            "train_counts": train_counts,
            "heldout_counts": heldout_counts,
            "train_projects": sorted(train_projects),
            "heldout_projects": sorted(heldout_projects),
            "train_natural_join_tasks": len(by_task),
            "train_stage_prior_ms": prior,
            "train_parent_reentry_tasks": len(parent_by_task),
            "train_parent_reentry_prior_ms": parent_prior,
            "heldout_by_project": {
                project: summarize_project(project)
                for project in sorted(heldout_projects)
            },
            "heldout_natural_join_workflows": len({
                row["task_id"] for row in natural
            }),
            "heldout_parent_reentry_workflows": len({
                row["task_id"] for row in parent
            }),
            "heldout_join_lead_ms": _metrics(
                [row["join_lead_ms"] for row in natural],
                [0.] * len(natural),
            ),
            "heldout_join_point_error_ms": (
                _metrics(
                    [row["join_lead_ms"] for row in natural],
                    [prior] * len(natural),
                ) if prior is not None else None
            ),
            "heldout_join_real_lead_at_least_500ms": sum(
                row["join_lead_ms"] >= 500 for row in natural
            ),
            "heldout_parent_real_lead_at_least_500ms": sum(
                row["parent_lead_ms"] >= 500 for row in parent
            ),
            "heldout_parent_reentry_point_error_ms": (
                _metrics(
                    [row["parent_lead_ms"] for row in parent],
                    [parent_prior] * len(parent),
                ) if parent_prior is not None else None
            ),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", required=True, type=Path)
    parser.add_argument("--heldout-workflows", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    train_ids, train_errors = require_complete_batch(args.train_workflows)
    heldout_ids, heldout_errors = require_complete_batch(args.heldout_workflows)
    report = evaluate(
        args.train_workflows, args.heldout_workflows,
        frozen_train_ids=train_ids, frozen_heldout_ids=heldout_ids,
    )
    report["train_frozen_workflows"] = len(train_ids)
    report["heldout_frozen_workflows"] = len(heldout_ids)
    report["train_runner_errors"] = train_errors
    report["heldout_runner_errors"] = heldout_errors
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
