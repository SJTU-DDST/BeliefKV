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
    from scripts.evaluate_cold_tool_project_loo import require_complete_batch
    from scripts.evaluate_child_return_intent_timing import _metrics, load_episodes
except ModuleNotFoundError:
    from audit_child_hidden_trace import blocked_child_invocations
    from evaluate_cold_tool_project_loo import require_complete_batch
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
                event for event in events
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
            started = max(
                float(join["ts_ms"]),
                min(float(event["ts_ms"]) for event in waiting),
            )
            finished = min(completed) if completed else None
            parents = {
                event["invocation_id"] for event in waiting
                if event.get("invocation_id")
            }
            parent_reentry = None
            if finished is not None and len(parents) == 1:
                parent = next(iter(parents))
                waiter = min(
                    (event for event in waiting if event["invocation_id"] == parent),
                    key=lambda event: float(event["ts_ms"]),
                )
                invalidation = min(
                    (
                        float(event["ts_ms"]) for event in events
                        if event.get("invocation_id") == parent
                        and float(event["ts_ms"]) > started
                        and (
                            event["kind"] in {"return", "invocation_cancel"}
                            or (
                                event["kind"] == "join_wait"
                                and event.get("join_id") != join_id
                            )
                        )
                    ),
                    default=float("inf"),
                )
                submits = [
                    float(event["ts_ms"]) for event in events
                    if event["kind"] == "llm_submit"
                    and event.get("invocation_id") == parent
                    and not (event.get("attributes") or {}).get("runtime_internal")
                    and finished <= float(event["ts_ms"]) < invalidation
                    and (
                        waiter.get("context_id") is None
                        or event.get("context_id") is None
                        or waiter["context_id"] == event["context_id"]
                    )
                    and (
                        waiter.get("context_epoch") is None
                        or event.get("context_epoch") is None
                        or waiter["context_epoch"] == event["context_epoch"]
                    )
                ]
                parent_reentry = min(submits) if submits else None
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
            if parent_reentry is not None:
                counts["candidate_parent_reentry_observed"] += 1
            groups.append({
                "project": path.parent.name.split("__", 1)[0],
                "task_id": path.parent.name,
                "join_id": join_id,
                "members": len(members),
                "trigger_ts_ms": trigger,
                "pending_notices_ms": pending,
                "label": label,
                "lead_ms": finished - trigger if label == "natural" else None,
                "parent_reentry_lead_ms": (
                    parent_reentry - trigger if parent_reentry is not None
                    else None
                ),
                "join_to_parent_submit_ms": (
                    parent_reentry - finished
                    if parent_reentry is not None and finished is not None
                    else None
                ),
            })
    return groups, dict(counts)


def evaluate(train_workflows: Path, heldout_workflows: Path) -> dict:
    train, train_counts = load_episodes(train_workflows.parent)
    train_groups, train_group_counts = collect(train_workflows)
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
    parent_by_task = defaultdict(list)
    for row in train_groups:
        if row["parent_reentry_lead_ms"] is not None and row["label"] != "revoked":
            parent_by_task[row["task_id"]].append(
                row["parent_reentry_lead_ms"]
            )
    parent_prior_ms = (
        median(median(values) for values in parent_by_task.values())
        if parent_by_task else None
    )
    submit_by_task = defaultdict(list)
    for row in train_groups:
        if row["join_to_parent_submit_ms"] is not None and row["label"] != "revoked":
            submit_by_task[row["task_id"]].append(row["join_to_parent_submit_ms"])
    submit_prior_ms = (
        median(median(values) for values in submit_by_task.values())
        if submit_by_task else None
    )
    true = [row for row in heldout if row["label"] == "natural"]
    prediction = [
        max(0., *(prior_ms + notice - row["trigger_ts_ms"]
                  for notice in row["pending_notices_ms"].values()))
        for row in true
    ]
    actual = [row["lead_ms"] for row in true]
    parent = [
        row for row in heldout
        if row["parent_reentry_lead_ms"] is not None and row["label"] != "revoked"
    ]
    parent_actual = [row["parent_reentry_lead_ms"] for row in parent]
    parent_predicted = (
        [parent_prior_ms] * len(parent_actual)
        if parent_prior_ms is not None else []
    )
    composed_prediction = (
        [
            max(0., *(prior_ms + notice - row["trigger_ts_ms"]
                      for notice in row["pending_notices_ms"].values()))
            + submit_prior_ms
            for row in parent
        ]
        if submit_prior_ms is not None else []
    )
    return {
        "status": "read_only_first_whole_join_candidate_not_action_eligible",
        "train_projects": sorted(train_projects),
        "heldout_projects": sorted(heldout_projects),
        "train_counts": train_counts,
        "train_group_counts": train_group_counts,
        "heldout_counts": counts,
        "train_task_balanced_child_notice_prior_ms": prior_ms,
        "train_task_balanced_parent_reentry_prior_ms": parent_prior_ms,
        "train_task_balanced_join_to_parent_submit_prior_ms": submit_prior_ms,
        "heldout_natural_group_lead_ms": _metrics(
            actual, [0.] * len(actual),
        ),
        "heldout_natural_group_point_error_ms": _metrics(actual, prediction),
        "heldout_observed_parent_reentry_lead_ms": _metrics(
            parent_actual, [0.] * len(parent_actual),
        ),
        "heldout_observed_parent_reentry_point_error_ms": (
            _metrics(parent_actual, parent_predicted)
            if parent_prior_ms is not None else None
        ),
        "heldout_observed_parent_reentry_composed_point_error_ms": (
            _metrics(parent_actual, composed_prediction)
            if submit_prior_ms is not None else None
        ),
        "scope": (
            "First causal all-member coverage after parent JOIN_WAIT: every "
            "unfinished child has a non-revoked notice. Completed children "
            "need no ETA. Project-disjoint child notice prior is frozen before "
            "held-out groups. Parent reentry is the first matched non-internal "
            "LLM_SUBMIT after JOIN satisfaction, before parent invalidation; "
            "it is evaluated separately even if child completion is blocked. "
            "The composed ETA uses the latest pending-child notice extrapolated "
            "with a train-only child prior, plus a train-only JOIN-to-parent "
            "submit lag. Future events only label revocation, censoring and reentry. "
            "No conditional-on-last-child oracle, "
            "physical H2D, online delivery or task-performance claim."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, required=True)
    parser.add_argument("--heldout-workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    train_ids, train_errors = require_complete_batch(args.train_workflows)
    heldout_ids, heldout_errors = require_complete_batch(args.heldout_workflows)
    trace_ids = {
        path.parent.name for path in args.heldout_workflows.glob(
            "*/runtime_events.deepagents.jsonl"
        )
    }
    if trace_ids - set(heldout_ids):
        raise ValueError("held-out trace is not in the frozen manifest")
    result = evaluate(args.train_workflows, args.heldout_workflows)
    result["train_frozen_workflows"] = len(train_ids)
    result["heldout_frozen_workflows"] = len(heldout_ids)
    result["train_runner_errors"] = train_errors
    result["heldout_runner_errors"] = heldout_errors
    result["heldout_frozen_by_project"] = dict(sorted(Counter(
        task.split("__", 1)[0] for task in heldout_ids
    ).items()))
    result["heldout_missing_trace_workflows"] = sorted(
        set(heldout_ids) - trace_ids
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
