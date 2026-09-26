#!/usr/bin/env python3
"""Project-disjoint, first-trigger audit of live child report heading phases."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import median

try:
    from scripts.evaluate_child_intent_stream_milestones import collect
    from scripts.evaluate_child_return_intent_timing import _metrics
except ModuleNotFoundError:
    from evaluate_child_intent_stream_milestones import collect
    from evaluate_child_return_intent_timing import _metrics

PHASES = frozenset({"summary", "conclusion", "validation", "next_steps"})


def load(workflows: Path) -> tuple[list[dict], dict]:
    stages, counts = collect(workflows, 1024)
    events_by_task = {}
    for path in workflows.glob("*/runtime_events.deepagents.jsonl"):
        events_by_task[path.parent.name] = [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        ]
    for row in stages:
        events = events_by_task[row["task_id"]]
        candidates = []
        for event in events:
            attrs = event.get("attributes") or {}
            if (
                event.get("kind") != "structured_action"
                or not attrs.get("beliefkv_child_report_phase_shadow")
                or attrs.get("phase_kind") not in PHASES
                or attrs.get("request_id") != row["request_id"]
                or event.get("invocation_id") != row["invocation_id"]
                or (
                    event.get("context_id"), event.get("context_epoch")
                ) != (row["context_id"], row["context_epoch"])
                or float(event["ts_ms"]) < row["signal_ts_ms"]
                or int(attrs.get("stream_content_chars") or 0) < 1024
            ):
                continue
            candidates.append(event)
        if candidates:
            first = min(candidates, key=lambda event: float(event["ts_ms"]))
            row["phase_kind"] = first["attributes"]["phase_kind"]
            row["phase_ts_ms"] = float(first["ts_ms"])
            row["phase_chars"] = first["attributes"]["stream_content_chars"]
            row["phase_lead_ms"] = (
                row["return_ts_ms"] - row["phase_ts_ms"]
                if row["label"] == "true" and row["return_ts_ms"] is not None
                else None
            )
        else:
            row["phase_kind"] = None
    return stages, counts


def _task_median(rows: list[dict], key: str) -> float:
    per_task = defaultdict(list)
    for row in rows:
        per_task[row["task_id"]].append(row[key])
    return median(median(values) for values in per_task.values())


def _score(train: list[dict], test: list[dict]) -> dict:
    train_true = [row for row in train if row["label"] == "true"]
    if not train_true:
        raise ValueError("training projects have no natural 1024-char returns")
    stage_prior = _task_median(train_true, "lead_ms")
    train_phase = [
        row for row in train_true
        if row["phase_kind"] is not None and row["phase_lead_ms"] >= 0
    ]
    priors = {}
    for phase in sorted(PHASES):
        support = [row for row in train_phase if row["phase_kind"] == phase]
        if support:
            priors[phase] = _task_median(support, "phase_lead_ms")
    candidates = [row for row in test if row["phase_kind"] is not None]
    positives = [
        row for row in candidates
        if row["label"] == "true" and row["phase_lead_ms"] >= 0
    ]
    join = [row for row in positives if row["join_last"]]

    def timing(rows: list[dict]) -> dict:
        actual = [row["phase_lead_ms"] for row in rows]
        return {
            "phase_train_prior": _metrics(
                actual,
                [priors.get(row["phase_kind"], stage_prior) for row in rows],
            ),
            "fixed_1024_train_prior_at_phase": _metrics(
                actual,
                [
                    max(0., stage_prior - (
                        row["phase_ts_ms"] - row["signal_ts_ms"]
                    ))
                    for row in rows
                ],
            ),
            "immediate_return_at_phase": _metrics(actual, [0.] * len(rows)),
        }

    return {
        "eligible_1024_stage": len(test),
        "natural_1024_stage": sum(row["label"] == "true" for row in test),
        "candidate_first_trigger": len(candidates),
        "candidate_labels": dict(Counter(row["label"] for row in candidates)),
        "candidate_phases": dict(Counter(row["phase_kind"] for row in candidates)),
        "natural_candidate": len(positives),
        "natural_without_candidate": sum(
            row["label"] == "true" and row["phase_kind"] is None
            for row in test
        ),
        "candidate_with_at_least_500ms_lead": sum(
            row["phase_lead_ms"] >= 500 for row in positives
        ),
        "join_last_natural_candidate": len(join),
        "train_phase_support": dict(Counter(
            row["phase_kind"] for row in train_phase
        )),
        "train_phase_prior_ms": priors,
        "return_timing_on_same_first_triggers": timing(positives),
        "join_last_timing_on_same_first_triggers": timing(join),
    }


def audit_workflows(workflows: Path) -> dict:
    rows, counts = load(workflows)
    candidates = [row for row in rows if row["phase_kind"] is not None]
    return {
        "diagnostic_only": True,
        "collector": counts,
        "stage_count": len(rows),
        "stage_labels": dict(Counter(row["label"] for row in rows)),
        "natural_join_last": sum(
            row["label"] == "true" and row["join_last"] for row in rows
        ),
        "first_trigger_count": len(candidates),
        "first_trigger_labels": dict(Counter(
            row["label"] for row in candidates
        )),
        "first_trigger_phases": dict(Counter(
            row["phase_kind"] for row in candidates
        )),
        "natural_first_trigger_with_500ms_lead": sum(
            row["label"] == "true" and row["phase_lead_ms"] >= 500
            for row in candidates
        ),
        "limitation": (
            "This counts only notice-bound identity-checked 1024-character "
            "stages; it is a training-side coverage audit, not an independent "
            "accuracy result or an online action qualification."
        ),
    }


def evaluate(train_roots: list[Path], heldout_root: Path) -> dict:
    train, train_info, seen_tasks, train_projects = [], {}, set(), set()
    for root in train_roots:
        rows, counts = load(root)
        tasks = {row["task_id"] for row in rows}
        if seen_tasks & tasks:
            raise ValueError("training workflows overlap")
        seen_tasks |= tasks
        train_projects |= {row["project"] for row in rows}
        train.extend(rows)
        train_info[str(root)] = counts
    heldout, heldout_info = load(heldout_root)
    projects = {row["project"] for row in heldout}
    if (
        not train_projects or not projects or train_projects & projects
        or seen_tasks & {row["task_id"] for row in heldout}
    ):
        raise ValueError("training and evaluation projects/tasks must be disjoint")
    return {
        "diagnostic_only": True,
        "train_projects": sorted(train_projects),
        "heldout_projects": sorted(projects),
        "train_collectors": train_info,
        "heldout_collector": heldout_info,
        "evaluation": _score(train, heldout),
        "by_project": {
            project: _score(
                train, [row for row in heldout if row["project"] == project]
            ) for project in sorted(projects)
        },
        "limitation": (
            "Only the first live delivered heading after an identity-checked "
            "1024-character stage is evaluated. That stage requires a preceding "
            "child completion notice. Neither this conditional score nor an "
            "oracle-known natural RETURN subset proves action eligibility. "
            "No text, future report length, or evaluation labels are used to "
            "fit the training-project phase priors."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-workflows", type=Path)
    parser.add_argument("--train-workflows", type=Path, action="append")
    parser.add_argument("--heldout-workflows", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.audit_workflows is not None:
        if args.train_workflows or args.heldout_workflows:
            parser.error("--audit-workflows cannot be combined with evaluation")
        result = audit_workflows(args.audit_workflows)
    else:
        if not args.train_workflows or args.heldout_workflows is None:
            parser.error("evaluation requires training and held-out workflows")
        result = evaluate(args.train_workflows, args.heldout_workflows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
