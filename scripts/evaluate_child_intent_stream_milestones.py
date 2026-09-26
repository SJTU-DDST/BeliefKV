#!/usr/bin/env python3
"""Project-held-out timing for fixed stream milestones after child intent."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import median

try:
    from scripts.audit_child_hidden_trace import (
        blocked_child_invocations, index_workflow,
    )
    from scripts.evaluate_child_return_intent_timing import _metrics
except ModuleNotFoundError:
    from audit_child_hidden_trace import blocked_child_invocations, index_workflow
    from evaluate_child_return_intent_timing import _metrics


THRESHOLDS = (1024, 1700)


def collect(workflows: Path, threshold: int) -> tuple[list[dict], dict]:
    paths = sorted(workflows.glob("*/runtime_events.deepagents.jsonl"))
    if not paths:
        raise FileNotFoundError(f"missing workflow events in {workflows}")
    counts = Counter(workflows=len(paths))
    rows = []
    for path in paths:
        events = [json.loads(line) for line in path.read_text().splitlines()]
        audit_path = path.parent / "sandbox_audit.jsonl"
        if not audit_path.is_file():
            raise FileNotFoundError(f"missing sandbox audit in {path.parent}")
        notices = defaultdict(list)
        for line in audit_path.read_text().splitlines():
            event = json.loads(line)
            if event.get("event") == "child_return_intent_shadow":
                notices[event["invocation_id"]].append(event)
        blocked = blocked_child_invocations(path.parent)
        terminals, join_last = index_workflow(
            events, blocked_invocations=blocked,
        )
        terminal_by_child = {
            child: (rid, float(ts)) for rid, (child, ts) in terminals.items()
        }
        by_child = defaultdict(list)
        for event in events:
            if event.get("invocation_id") in notices:
                by_child[event["invocation_id"]].append(event)
        for child, entries in notices.items():
            counts["announced_children"] += 1
            counts["repeated_notices"] += len(entries) - 1
            first = min(entries, key=lambda row: float(row["ts_ms"]))
            notice = float(first["ts_ms"])
            observed = by_child[child]
            prior = [
                event for event in observed
                if float(event["ts_ms"]) <= notice
                and event.get("context_id") is not None
                and event.get("context_epoch") is not None
            ]
            if not prior:
                counts["missing_source_epoch"] += 1
                continue
            source = max(prior, key=lambda row: float(row["ts_ms"]))
            if first.get("context_id") is not None and (
                first.get("context_id"), first.get("context_epoch")
            ) != (source["context_id"], source["context_epoch"]):
                counts["notice_identity_mismatch"] += 1
                continue
            stages = sorted(
                (
                    event for event in observed
                    if event["kind"] == "structured_action"
                    and (event.get("attributes") or {}).get(
                        "beliefkv_child_substantial_content_shadow"
                    )
                    and (event.get("attributes") or {}).get(
                        "content_threshold_chars"
                    ) == threshold
                    and float(event["ts_ms"]) > notice
                ),
                key=lambda event: float(event["ts_ms"]),
            )
            if not stages:
                counts["no_stage_after_notice"] += 1
                continue
            stage = stages[0]
            ts = float(stage["ts_ms"])
            if any(
                event["kind"] == "tool_start"
                and (event.get("attributes") or {}).get("tool_name")
                != "announce_completion_intent"
                and notice < float(event["ts_ms"]) <= ts
                for event in observed
            ):
                counts["revoked_before_stage"] += 1
                continue
            submits = [
                event for event in observed
                if event["kind"] == "llm_submit"
                and not (event.get("attributes") or {}).get("runtime_internal")
                and notice < float(event["ts_ms"]) <= ts
            ]
            rid = (stage.get("attributes") or {}).get("request_id")
            if (
                len(submits) != 1 or not rid
                or (submits[0].get("attributes") or {}).get("request_id") != rid
                or (submits[0].get("context_id"), submits[0].get("context_epoch"))
                != (stage.get("context_id"), stage.get("context_epoch"))
                or submits[0].get("context_id") != source["context_id"]
                or submits[0].get("context_epoch") != source["context_epoch"] + 1
            ):
                counts["stage_identity_mismatch"] += 1
                continue
            terminal = terminal_by_child.get(child)
            later_tool = any(
                event["kind"] == "tool_start"
                and (event.get("attributes") or {}).get("tool_name")
                != "announce_completion_intent"
                and ts < float(event["ts_ms"]) < (
                    terminal[1] if terminal else float("inf")
                )
                for event in observed
            )
            if terminal and rid == terminal[0] and ts < terminal[1] and not later_tool:
                label = "true"
                lead_ms = terminal[1] - ts
            elif later_tool or terminal:
                label = "false"
                lead_ms = None
            else:
                label = "censored"
                lead_ms = None
            rows.append({
                "project": path.parent.name.split("__", 1)[0],
                "task_id": path.parent.name,
                "join_last": child in join_last,
                "label": label,
                "lead_ms": lead_ms,
            })
    return rows, dict(counts)


def evaluate(train_workflows: Path, heldout_workflows: Path) -> dict:
    result = {"diagnostic_only": True, "thresholds_chars": list(THRESHOLDS)}
    train_tasks = {
        path.parent.name for path in train_workflows.glob(
            "*/runtime_events.deepagents.jsonl"
        )
    }
    heldout_tasks = {
        path.parent.name for path in heldout_workflows.glob(
            "*/runtime_events.deepagents.jsonl"
        )
    }
    projects = {task.split("__", 1)[0] for task in train_tasks}
    heldout_projects = {task.split("__", 1)[0] for task in heldout_tasks}
    if not projects or not heldout_projects or projects & heldout_projects:
        raise ValueError("both splits need workflows from disjoint projects")
    if train_tasks & heldout_tasks:
        raise ValueError("training and held-out task IDs overlap")
    for threshold in THRESHOLDS:
        train, train_counts = collect(train_workflows, threshold)
        heldout, heldout_counts = collect(heldout_workflows, threshold)
        by_task = defaultdict(list)
        for row in train:
            if row["label"] == "true":
                by_task[row["task_id"]].append(row["lead_ms"])
        if not by_task:
            raise ValueError("training split has no natural stage returns")
        prior = median([median(values) for values in by_task.values()])
        positives = [row for row in heldout if row["label"] == "true"]
        join = [row for row in positives if row["join_last"]]
        result[str(threshold)] = {
            "train_projects": sorted(projects),
            "heldout_projects": sorted(heldout_projects),
            "train_counts": train_counts,
            "heldout_counts": heldout_counts,
            "train_unique_positive_tasks": len(by_task),
            "train_positive_count": sum(map(len, by_task.values())),
            "frozen_task_balanced_prior_ms": prior,
            "heldout_true": len(positives),
            "heldout_false": sum(row["label"] == "false" for row in heldout),
            "heldout_censored": sum(row["label"] == "censored" for row in heldout),
            "heldout_join_last_true": len(join),
            "heldout_lead_ms": _metrics(
                [row["lead_ms"] for row in positives], [0.] * len(positives),
            ),
            "heldout_point_error_ms": _metrics(
                [row["lead_ms"] for row in positives], [prior] * len(positives),
            ),
            "heldout_join_last_lead_ms": _metrics(
                [row["lead_ms"] for row in join], [0.] * len(join),
            ),
            "heldout_join_last_point_error_ms": _metrics(
                [row["lead_ms"] for row in join], [prior] * len(join),
            ),
        }
    result["scope"] = (
        "First stage after first notice; fixed existing 1024/1700 character "
        "milestones. Future natural return used only to label. No online "
        "delivery, physical transfer, or sealed-test claim."
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, required=True)
    parser.add_argument("--heldout-workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(
            evaluate(args.train_workflows, args.heldout_workflows),
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
