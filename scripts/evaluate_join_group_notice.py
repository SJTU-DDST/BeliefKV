#!/usr/bin/env python3
"""Read-only project-held-out timing of the first actionable whole-JOIN notice."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import median

try:
    from scripts.audit_child_hidden_trace import blocked_child_invocations
    from scripts.evaluate_child_return_intent_timing import _metrics, load_episodes
except ModuleNotFoundError:
    from audit_child_hidden_trace import blocked_child_invocations
    from evaluate_child_return_intent_timing import _metrics, load_episodes


def collect(workflows: Path) -> tuple[list[dict], dict]:
    files = sorted(workflows.glob("*/runtime_events.deepagents.jsonl"))
    if not files:
        raise FileNotFoundError(f"missing workflow events in {workflows}")
    groups = []
    counts = Counter(workflows=len(files))
    for path in files:
        events = [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        audit_path = path.parent / "sandbox_audit.jsonl"
        if not audit_path.is_file():
            raise FileNotFoundError(f"missing sandbox audit in {path.parent}")
        notices = [
            row for line in audit_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
            if (row := json.loads(line)).get("event") == "child_return_intent_shadow"
        ]
        context_events = defaultdict(list)
        for event in events:
            if event.get("invocation_id") and event.get("context_id") is not None and (
                event.get("context_epoch") is not None
            ):
                context_events[event["invocation_id"]].append(event)
        blocked = blocked_child_invocations(path.parent)
        joins = {
            event["join_id"]: event
            for event in events
            if event["kind"] == "join_create" and event.get("join_id")
            and (event.get("attributes") or {}).get("mode") == "all"
        }
        for join_id, join in joins.items():
            members = set(join.get("member_invocation_ids") or ())
            if not members:
                continue
            counts["all_mode_groups"] += 1
            waiting = [
                float(event["ts_ms"]) for event in events
                if event["kind"] == "join_wait" and event.get("join_id") == join_id
            ]
            completed = [
                float(event["ts_ms"]) for event in events
                if event["kind"] == "join_satisfied"
                and event.get("join_id") == join_id
            ]
            if not waiting:
                counts["no_parent_wait"] += 1
                continue
            started = max(float(join["ts_ms"]), min(waiting))
            finished = min(completed) if completed else None
            returns = {
                event["invocation_id"]: event
                for event in events
                if event["kind"] == "return"
                and event.get("invocation_id") in members
            }
            cancelled_children = {
                event["invocation_id"]
                for event in events
                if event["kind"] == "invocation_cancel"
                and event.get("invocation_id") in members
                and (finished is None or float(event["ts_ms"]) <= finished)
            }
            natural = (
                finished is not None
                and all(
                    child in returns
                    and child not in blocked | cancelled_children
                    and (returns[child].get("attributes") or {}).get("outcome")
                    == "completed"
                    and (returns[child].get("attributes") or {}).get(
                        "child_report_status"
                    ) != "blocked"
                    and float(returns[child]["ts_ms"]) <= finished
                    for child in members
                )
            )
            if natural:
                counts["natural_groups"] += 1
            changes = []
            for event in events:
                child = event.get("invocation_id")
                if child not in members:
                    continue
                when = float(event["ts_ms"])
                if when < float(join["ts_ms"]) or (
                    finished is not None and when >= finished
                ):
                    continue
                kind = event["kind"]
                if kind == "return":
                    changes.append((when, 0, "return", child, event))
                elif kind == "invocation_cancel":
                    changes.append((when, 0, "cancel", child, event))
                elif kind == "tool_start" and (
                    (event.get("attributes") or {}).get("tool_name")
                    != "announce_completion_intent"
                ):
                    changes.append((when, 0, "revoke", child, event))
            for notice in notices:
                child = notice.get("invocation_id")
                when = float(notice["ts_ms"])
                if (
                    child in members and when >= float(join["ts_ms"])
                    and (finished is None or when < finished)
                    and notice.get("join_id") in (None, join_id)
                ):
                    if notice.get("context_id") is not None or (
                        notice.get("context_epoch") is not None
                    ):
                        prior = [
                            event for event in context_events[child]
                            if float(event["ts_ms"]) <= when
                        ]
                        source = max(
                            prior, key=lambda event: float(event["ts_ms"]),
                            default=None,
                        )
                        if source is None or (
                            source["context_id"], source["context_epoch"]
                        ) != (
                            notice.get("context_id"), notice.get("context_epoch")
                        ):
                            counts["invalid_notice_identity"] += 1
                            continue
                    changes.append((when, 1, "notice", child, notice))
            changes.append((started, 2, "wait", "", {}))
            latest: dict[str, float] = {}
            returned: set[str] = set()
            cancelled: set[str] = set()
            candidate = None
            for when, _, kind, child, _ in sorted(
                changes, key=lambda change: (change[0], change[1])
            ):
                if kind == "return":
                    returned.add(child)
                    latest.pop(child, None)
                elif kind == "cancel":
                    cancelled.add(child)
                    latest.pop(child, None)
                elif kind == "revoke":
                    latest.pop(child, None)
                elif kind == "notice" and child not in returned | cancelled:
                    latest[child] = when
                pending = members - returned
                if when >= started and pending and pending <= latest.keys():
                    candidate = {
                        "trigger_ts_ms": when,
                        "pending_notices_ms": {
                            child: latest[child] for child in pending
                        },
                    }
                    break
            if candidate is None:
                counts["no_whole_group_candidate"] += 1
                continue
            trigger = candidate["trigger_ts_ms"]
            pending = candidate["pending_notices_ms"]
            later_tool = any(
                event["kind"] == "tool_start"
                and event.get("invocation_id") in pending
                and (event.get("attributes") or {}).get("tool_name")
                != "announce_completion_intent"
                and trigger < float(event["ts_ms"]) < (
                    float(returns[event["invocation_id"]]["ts_ms"])
                    if event["invocation_id"] in returns else float("inf")
                )
                for event in events
            )
            label = (
                "revoked" if later_tool else
                "natural" if natural and finished is not None and trigger < finished
                else "censored"
            )
            counts[f"candidate_{label}"] += 1
            groups.append({
                "project": path.parent.name.split("__", 1)[0],
                "task_id": path.parent.name,
                "join_id": join_id,
                "members": len(members),
                "trigger_ts_ms": trigger,
                "pending_notices_ms": pending,
                "label": label,
                "lead_ms": finished - trigger if label == "natural" else None,
            })
    return groups, dict(counts)


def evaluate(train_workflows: Path, heldout_workflows: Path) -> dict:
    train, train_counts = load_episodes(train_workflows.parent)
    heldout, counts = collect(heldout_workflows)
    train_projects = {row["project"] for row in train}
    heldout_projects = {row["project"] for row in heldout}
    if not train_projects or not heldout_projects or (
        train_projects & heldout_projects
    ):
        raise ValueError("training notices and held-out JOIN groups need disjoint projects")
    if {row["task_id"] for row in train} & {row["task_id"] for row in heldout}:
        raise ValueError("training and held-out task IDs overlap")
    by_task = defaultdict(list)
    for row in train:
        by_task[row["task_id"]].append(row["lead_ms"])
    prior_ms = median(median(values) for values in by_task.values())
    true = [row for row in heldout if row["label"] == "natural"]
    prediction = [
        max(0., *(prior_ms + notice - row["trigger_ts_ms"]
                  for notice in row["pending_notices_ms"].values()))
        for row in true
    ]
    actual = [row["lead_ms"] for row in true]
    return {
        "status": "read_only_first_whole_join_candidate_not_action_eligible",
        "train_projects": sorted(train_projects),
        "heldout_projects": sorted(heldout_projects),
        "train_counts": train_counts,
        "heldout_counts": counts,
        "train_task_balanced_child_notice_prior_ms": prior_ms,
        "heldout_natural_group_lead_ms": _metrics(
            actual, [0.] * len(actual),
        ),
        "heldout_natural_group_point_error_ms": _metrics(actual, prediction),
        "scope": (
            "First causal all-member coverage after parent JOIN_WAIT: every "
            "unfinished child has a non-revoked notice. Completed children "
            "need no ETA. Project-disjoint child notice prior is frozen before "
            "held-out groups; future events only label revocation, censoring "
            "and JOIN satisfaction. No conditional-on-last-child oracle, "
            "physical H2D, online delivery or task-performance claim."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, required=True)
    parser.add_argument("--heldout-workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.train_workflows, args.heldout_workflows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
