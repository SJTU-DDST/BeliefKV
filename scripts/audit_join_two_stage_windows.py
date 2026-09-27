#!/usr/bin/env python3
"""Compare causal early and terminal JOIN notices against parent reentry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median

try:
    from scripts.evaluate_cold_tool_project_loo import require_complete_batch
    from scripts.evaluate_join_group_notice import collect
except ModuleNotFoundError:
    from evaluate_cold_tool_project_loo import require_complete_batch
    from evaluate_join_group_notice import collect


def summarize(rows: list[dict], counts: dict) -> dict:
    natural = [row for row in rows if row["label"] == "natural"]
    parent = [
        row["parent_reentry_lead_ms"] for row in natural
        if row["parent_reentry_lead_ms"] is not None
    ]
    leads = [row["lead_ms"] for row in natural]
    return {
        "all_mode_groups": counts["all_mode_groups"],
        "candidate_natural": len(natural),
        "candidate_revoked": counts.get("candidate_revoked", 0),
        "candidate_censored": counts.get("candidate_censored", 0),
        "no_candidate": counts.get("no_whole_group_candidate", 0),
        "join_lead_p50_ms": median(leads) if leads else None,
        "join_lead_ge_500ms": sum(value >= 500 for value in leads),
        "parent_first_submit_observed": len(parent),
        "parent_first_submit_lead_p50_ms": median(parent) if parent else None,
        "parent_first_submit_lead_ge_500ms": sum(
            value >= 500 for value in parent
        ),
        "parent_first_submit_lead_ge_1000ms": sum(
            value >= 1000 for value in parent
        ),
    }


def paired(early: list[dict], final: list[dict]) -> dict:
    def natural_by_group(rows: list[dict]) -> dict[tuple[str, str], dict]:
        indexed = {}
        for row in rows:
            if row["label"] != "natural":
                continue
            key = row["task_id"], row["join_id"]
            if key in indexed:
                raise ValueError(f"duplicate natural JOIN group: {key}")
            indexed[key] = row
        return indexed

    early_groups = natural_by_group(early)
    final_groups = natural_by_group(final)
    common = sorted(early_groups.keys() & final_groups.keys())
    if any(
        early_groups[key]["trigger_ts_ms"] > final_groups[key]["trigger_ts_ms"]
        for key in common
    ):
        raise ValueError("terminal notice preceded early notice")
    return {
        "both_natural_groups": len(common),
        "early_only_natural_groups": len(early_groups) - len(common),
        "final_only_natural_groups": len(final_groups) - len(common),
        "both_with_parent_first_submit": sum(
            early_groups[key]["parent_reentry_lead_ms"] is not None
            and final_groups[key]["parent_reentry_lead_ms"] is not None
            for key in common
        ),
        "early_join_lead_ge_500ms": sum(
            early_groups[key]["lead_ms"] >= 500 for key in common
        ),
        "final_join_lead_ge_500ms": sum(
            final_groups[key]["lead_ms"] >= 500 for key in common
        ),
        "early_parent_first_submit_lead_ge_500ms": sum(
            early_groups[key]["parent_reentry_lead_ms"] is not None
            and early_groups[key]["parent_reentry_lead_ms"] >= 500
            for key in common
        ),
        "final_parent_first_submit_lead_ge_500ms": sum(
            final_groups[key]["parent_reentry_lead_ms"] is not None
            and final_groups[key]["parent_reentry_lead_ms"] >= 500
            for key in common
        ),
    }


def audit(train_workflows: Path, heldout_workflows: Path) -> dict:
    train_ids, train_errors = require_complete_batch(train_workflows)
    heldout_ids, heldout_errors = require_complete_batch(heldout_workflows)
    train_projects = {task.split("__", 1)[0] for task in train_ids}
    heldout_projects = {task.split("__", 1)[0] for task in heldout_ids}
    if (train_projects & heldout_projects) or (set(train_ids) & set(heldout_ids)):
        raise ValueError("training and heldout must be project-disjoint")
    batches = {}
    for label, workflows, ids, errors in (
        ("train", train_workflows, train_ids, train_errors),
        ("heldout", heldout_workflows, heldout_ids, heldout_errors),
    ):
        early, early_counts = collect(workflows, notice_source="shadow")
        final, final_counts = collect(workflows, notice_source="llm_result")
        batches[label] = {
            "frozen_workflows": len(ids),
            "runner_errors": errors,
            "early": summarize(early, early_counts),
            "terminal": summarize(final, final_counts),
            "paired_natural": paired(early, final),
        }
    return {
        "status": "read_only_two_stage_join_windows_not_action_eligible",
        "train_projects": sorted(train_projects),
        "heldout_projects": sorted(heldout_projects),
        "batches": batches,
        "scope": (
            "Early completion intent and terminal LLM_RESULT are measured on "
            "the same batch, paired only for natural complete JOIN groups. "
            "Parent first LLM_SUBMIT is not first GPU service; a 500ms window "
            "does not prove that H2D can finish. Revoked, censored and missing "
            "candidates never count as useful physical transfers. This "
            "project-disjoint batch has previously been used for development, "
            "so it is not a fresh sealed evaluation."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, required=True)
    parser.add_argument("--heldout-workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.train_workflows, args.heldout_workflows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )


if __name__ == "__main__":
    main()
