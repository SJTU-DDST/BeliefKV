#!/usr/bin/env python3
"""Causal sole-pending JOIN notices and project-held-out return lower bounds."""

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
except ModuleNotFoundError:
    from audit_child_hidden_trace import blocked_child_invocations, index_workflow


WINDOWS_MS = (500, 1_000, 2_000)


def collect(
    workflows: list[Path], *, require_observed_sibling_final: bool = False,
) -> list[dict]:
    records = []
    seen_tasks: set[tuple[str, str]] = set()
    for root in workflows:
        files = sorted(root.glob("*/runtime_events.deepagents.jsonl"))
        if not files:
            raise FileNotFoundError(f"no workflow events under {root}")
        for path in files:
            task = path.parent.name
            project = task.split("__", 1)[0]
            if (project, task) in seen_tasks:
                raise ValueError(f"duplicate workflow task: {task}")
            seen_tasks.add((project, task))
            events = [
                json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            audit = path.parent / "sandbox_audit.jsonl"
            if not audit.is_file():
                raise FileNotFoundError(audit)
            notices = {}
            for line in audit.read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                if item.get("event") == "child_return_intent_shadow":
                    child = item.get("invocation_id")
                    if child and (child not in notices
                                  or item["ts_ms"] < notices[child]["ts_ms"]):
                        notices[child] = item
            groups = {
                row["join_id"]: row for row in events
                if row["kind"] == "join_create" and row.get("join_id")
                and (row.get("attributes") or {}).get("mode") == "all"
            }
            returned = {
                row["invocation_id"]: float(row["ts_ms"])
                for row in events
                if row["kind"] == "return" and row.get("invocation_id")
                and (row.get("attributes") or {}).get("outcome") == "completed"
            }
            returned_status = {
                row["invocation_id"]: (row.get("attributes") or {}).get(
                    "child_report_status"
                )
                for row in events
                if row["kind"] == "return" and row.get("invocation_id")
            }
            blocked = blocked_child_invocations(path.parent) | {
                child for child, status in returned_status.items()
                if status == "blocked"
            }
            terminals, join_last = index_workflow(
                events, blocked_invocations=blocked,
            )
            natural_returns = {
                child: float(ts) for child, ts in terminals.values()
                if child in returned
            }
            observed_finals = {
                row["invocation_id"]: float(row["ts_ms"])
                for row in events
                if row["kind"] == "llm_result" and row.get("invocation_id")
                and not (row.get("attributes") or {}).get("runtime_internal")
                and (row.get("attributes") or {}).get("finish_reason") == "stop"
                and type((row.get("attributes") or {}).get("output_chars")) is int
                and (row.get("attributes") or {})["output_chars"] > 0
                and not (row.get("attributes") or {}).get("tool_call_count")
            }
            satisfied = {
                row["join_id"]: float(row["ts_ms"]) for row in events
                if row["kind"] == "join_satisfied"
            }
            waiters = defaultdict(list)
            for row in events:
                if row["kind"] == "join_wait" and row.get("join_id") and row.get(
                    "invocation_id"
                ):
                    waiters[row["join_id"]].append(row)
            model_submissions = defaultdict(list)
            for row in events:
                if row["kind"] == "llm_submit" and row.get("invocation_id") and not (
                    row.get("attributes") or {}
                ).get("runtime_internal"):
                    model_submissions[row["invocation_id"]].append(row)
            parent_invalidations = defaultdict(list)
            for row in events:
                parent = row.get("invocation_id")
                if parent and (
                    row["kind"] in {"invocation_cancel", "return"}
                    or row["kind"] == "join_wait"
                ):
                    parent_invalidations[parent].append(row)
            cancels = defaultdict(list)
            tools = defaultdict(list)
            for row in events:
                if row["kind"] == "invocation_cancel" and row.get("invocation_id"):
                    cancels[row["invocation_id"]].append(float(row["ts_ms"]))
                if row["kind"] == "tool_start" and row.get("invocation_id"):
                    if (row.get("attributes") or {}).get("tool_name") != (
                        "announce_completion_intent"
                    ):
                        tools[row["invocation_id"]].append(float(row["ts_ms"]))
            for child, notice in notices.items():
                when = float(notice["ts_ms"])
                candidates = [
                    join for join in groups.values()
                    if child in join["member_invocation_ids"]
                    and float(join["ts_ms"]) < when
                    and satisfied.get(join["join_id"], float("inf")) > when
                    and (not notice.get("join_id")
                         or notice["join_id"] == join["join_id"])
                ]
                sole = [
                    join for join in candidates
                    if returned.get(child, float("inf")) > when
                    and not any(ts <= when for ts in cancels[child])
                    and all(
                        member in returned and returned[member] < when
                        and not any(ts <= when for ts in cancels[member])
                        and (
                            not require_observed_sibling_final
                            or (
                                returned_status.get(member) != "blocked"
                                and (
                                    returned_status.get(member) == "complete"
                                    or observed_finals.get(
                                        member, float("inf")
                                    ) <= returned[member]
                                )
                            )
                        )
                        for member in join["member_invocation_ids"]
                        if member != child
                    )
                ]
                if len(sole) != 1 or len(candidates) != 1:
                    continue
                join = sole[0]
                join_id = join["join_id"]
                earlier_waiters = [
                    row for row in waiters[join_id]
                    if float(row["ts_ms"]) <= when
                ]
                parent_ids = {row["invocation_id"] for row in earlier_waiters}
                waiter = (
                    max(earlier_waiters, key=lambda row: float(row["ts_ms"]))
                    if len(parent_ids) == 1 else None
                )
                parent = waiter["invocation_id"] if waiter else None
                satisfied_at = satisfied.get(join_id)
                next_invalid_at = min(
                    (
                        float(row["ts_ms"])
                        for row in parent_invalidations[parent]
                        if float(row["ts_ms"]) > when
                        and (
                            row["kind"] != "join_wait"
                            or row.get("join_id") != join_id
                        )
                    ),
                    default=float("inf"),
                ) if parent is not None else float("inf")
                parent_reentry_at = (
                    min(
                        (
                            float(row["ts_ms"])
                            for row in model_submissions[parent]
                            if satisfied_at <= float(row["ts_ms"]) < next_invalid_at
                            and (
                                not waiter.get("context_id")
                                or not row.get("context_id")
                                or waiter["context_id"] == row["context_id"]
                            )
                        ),
                        default=None,
                    )
                    if parent is not None and satisfied_at is not None
                    and satisfied_at > when else None
                )
                end = natural_returns.get(child)
                later_tool = any(
                    when < ts < (end if end is not None else float("inf"))
                    for ts in tools[child]
                )
                valid = (
                    end is not None and end > when and not later_tool
                    and child in join_last
                    and join["join_id"] in satisfied
                    and satisfied[join["join_id"]] >= end
                    and satisfied[join["join_id"]] - end < 1
                )
                failure = None
                if later_tool:
                    failure = "revoked_by_later_tool"
                elif end is None:
                    failure = "no_natural_child_return"
                elif child not in join_last or join["join_id"] not in satisfied:
                    failure = "no_complete_join"
                elif not valid:
                    failure = "join_return_mismatch"
                records.append({
                    "project": project,
                    "task": task,
                    "sole_pending_at_notice": True,
                    "join_identity_explicit": bool(notice.get("join_id")),
                    "natural_join": valid,
                    "lead_ms": end - when if valid else None,
                    "revoked_by_tool": later_tool,
                    "censored_or_nonterminal": not valid and not later_tool,
                    "failure": failure,
                    "join_satisfied_after_notice": (
                        satisfied_at is not None and satisfied_at > when
                    ),
                    "child_report_status": returned_status.get(child),
                    "parent_reentry_observed": parent_reentry_at is not None,
                    "parent_reentry_lead_ms": (
                        parent_reentry_at - when
                        if parent_reentry_at is not None else None
                    ),
                    "parent_submit_after_join_ms": (
                        parent_reentry_at - satisfied_at
                        if parent_reentry_at is not None else None
                    ),
                })
    return records


def evaluate(records: list[dict]) -> dict:
    projects = sorted({record["project"] for record in records})
    if len(projects) < 3:
        raise ValueError("need at least three projects with sole-pending notices")
    folds = {}
    for heldout in projects:
        training = [
            item for item in records
            if item["project"] != heldout and item["natural_join"]
        ]
        test = [item for item in records if item["project"] == heldout]
        durations = [item["lead_ms"] for item in training]
        # A conservative historical floor is descriptive, not a finite-sample
        # guarantee for a new project's future child.
        floor = max(0.0, min(durations) - 200.0) if durations else None
        eligible = [item["lead_ms"] for item in test if item["natural_join"]]
        training_reentries = [
            item["parent_reentry_lead_ms"] for item in records
            if item["project"] != heldout and item["parent_reentry_observed"]
            and not item["revoked_by_tool"]
        ]
        reentries = [
            item["parent_reentry_lead_ms"] for item in test
            if item["parent_reentry_observed"] and not item["revoked_by_tool"]
        ]
        reentry_prior = median(training_reentries) if training_reentries else None
        reentry_errors = [
            abs(lead - reentry_prior) for lead in reentries
        ] if reentry_prior is not None else []
        folds[heldout] = {
            "training_projects": sorted({item["project"] for item in training}),
            "training_natural_join_count": len(training),
            "candidate_count": len(test),
            "explicit_join_identity_count": sum(
                item["join_identity_explicit"] for item in test
            ),
            "natural_join_count": len(eligible),
            "nonterminal_or_censored_count": sum(
                item["censored_or_nonterminal"] for item in test
            ),
            "revoked_by_tool_count": sum(item["revoked_by_tool"] for item in test),
            "failure_reasons": dict(Counter(
                item["failure"] for item in test if item["failure"]
            )),
            "lead_p50_ms": median(eligible) if eligible else None,
            "join_satisfied_after_notice_count": sum(
                item["join_satisfied_after_notice"] for item in test
            ),
            "parent_reentry_observed_count": sum(
                item["parent_reentry_observed"] for item in test
            ),
            "parent_reentry_count": len(reentries),
            "parent_reentry_without_natural_label_count": sum(
                item["parent_reentry_observed"] and not item["natural_join"]
                for item in test
            ),
            "parent_reentry_after_notice_revocation_count": sum(
                item["parent_reentry_observed"] and item["revoked_by_tool"]
                for item in test
            ),
            "parent_reentry_train_count": len(training_reentries),
            "parent_reentry_train_median_ms": reentry_prior,
            "parent_reentry_abs_error_p50_ms": (
                median(reentry_errors) if reentry_errors else None
            ),
            "parent_reentry_within_500ms_count": sum(
                error <= 500 for error in reentry_errors
            ),
            "parent_reentry_lead_p50_ms": (
                median(reentries) if reentries else None
            ),
            "parent_submit_after_join_p50_ms": (
                median(
                    item["parent_submit_after_join_ms"] for item in test
                    if item["parent_reentry_observed"]
                ) if reentries else None
            ),
            "window_success_counts": {
                str(window): sum(lead >= window for lead in eligible)
                for window in WINDOWS_MS
            },
            "train_min_minus_200ms": floor,
            "natural_join_before_floor_count": (
                sum(lead < floor for lead in eligible) if floor is not None else None
            ),
        }
    return {
        "diagnostic_only": True,
        "first_notice_only": True,
        "selection": (
            "ALL JOIN exists before first notice; child alone remains without "
            "any observed sibling cancellation; no future RETURN used to select."
        ),
        "folds": folds,
        "scope": (
            "Development projects already used for other analyses. Natural "
            "JOIN labels exclude blocked returns, while parent reentry counts "
            "include the first matching submit after satisfied JOIN and before "
            "parent cancellation, return, or another JOIN. Revoked notices are "
            "counted separately and do not provide actionable lead. The parent "
            "point-error prior uses only other projects' observed nonrevoked "
            "reentries and is scored only on observed future reentries; missing "
            "or censored reentries are reported separately. Historical "
            "minimum has no distribution-free coverage guarantee. No residency, "
            "physical transfer, validated child RETURN point-ETA, or throughput "
            "evidence."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps({
        "sole_pending": evaluate(collect(args.workflows)),
        "sole_pending_observed_sibling_final": evaluate(collect(
            args.workflows, require_observed_sibling_final=True,
        )),
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
