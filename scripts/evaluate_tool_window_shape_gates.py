#!/usr/bin/env python3
"""Train-project-only shape gates for a frozen 100 ms tool-window head."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.predictor.tool_window_shadow import FrozenToolWindowShadow
from scripts.evaluate_cold_tool_project_loo import require_complete_batch
from scripts.evaluate_cold_tool_structure_holdout import cold_calls
from scripts.pilot_cold_child_tool_long import _fit_shape_head, _shape_scores
from scripts.pilot_tool_return_window_100ms import TARGET_MS, first_inputs


THRESHOLDS = (.5, .6, .7, .8, .9)
DEFAULT_THRESHOLD = .8


def _features(row: dict) -> dict:
    return {
        "observed_command_shape": row["shape"],
        "input_chars": row["input_chars"],
        "project_class_completed_support": row.get(
            "project_class_completed_support"
        ),
        "project_class_inflight_other_workflow_2s_peers": row.get(
            "other_workflow_2s_peers"
        ),
        "project_class_duration_median_ms": row.get(
            "project_class_duration_median_ms"
        ),
        "project_input_neighbor_duration_ms": row.get(
            "project_input_neighbor_duration_ms"
        ),
        "project_input_neighbor_support": row.get(
            "project_input_neighbor_support"
        ),
    }


def training_oof(rows: list[dict]) -> list[dict]:
    projects = sorted({row["project"] for row in rows})
    if len(projects) < 3:
        raise ValueError("need at least three training projects")
    scored = []
    for project in projects:
        train = [row for row in rows if row["project"] != project]
        heldout = [row for row in rows if row["project"] == project]
        model = _fit_shape_head(
            train, include_live_peers=True,
            include_duration_priors=True, target_ms=TARGET_MS,
        )
        values = _shape_scores(
            model, heldout, include_live_peers=True,
            include_duration_priors=True,
        )
        scored.extend(
            {**row, "score": float(value)}
            for row, value in zip(heldout, values)
        )
    return scored


def choose_gates(
    scored: list[dict], *,
    min_selected: int = 30,
    min_projects: int = 3,
) -> tuple[dict[str, float], dict]:
    """Use OOF labels only, requiring support and precision in each project."""

    if not scored or min_selected <= 0 or min_projects <= 0:
        raise ValueError("invalid training evidence")
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in scored:
        groups[row["shape"]].append(row)
    gates = {}
    evidence = {}
    for shape, group in sorted(groups.items()):
        candidates = {}
        for threshold in THRESHOLDS:
            selected = [
                row for row in group if row["score"] >= threshold
            ]
            by_project = defaultdict(list)
            for row in selected:
                by_project[row["project"]].append(row)
            supported = {
                project: values for project, values in by_project.items()
                if len(values) >= 5
            }
            good = sum(
                row["duration_ms"] >= TARGET_MS for row in selected
            )
            precision = good / len(selected) if selected else None
            eligible = (
                len(selected) >= min_selected
                and len({row["workflow"] for row in selected}) >= 10
                and len(supported) >= min_projects
                and precision is not None and precision >= .8
                and all(
                    sum(row["duration_ms"] >= TARGET_MS for row in values)
                    / len(values) >= .75
                    for values in supported.values()
                )
            )
            candidates[str(threshold)] = {
                "selected": len(selected),
                "true_windows": good,
                "precision": precision,
                "supported_projects": sorted(supported),
                "qualified": eligible,
            }
        # Only lower-than-default gates can increase coverage; never replace
        # the default with a weaker or more restrictive unsupported gate.
        qualified = [
            threshold for threshold in THRESHOLDS
            if threshold < DEFAULT_THRESHOLD
            and candidates[str(threshold)]["qualified"]
        ]
        if qualified:
            gates[shape] = min(qualified)
        evidence[shape] = candidates
    return gates, evidence


def quality(rows: list[dict], gates: dict[str, float]) -> dict:
    positive = sum(row["duration_ms"] >= TARGET_MS for row in rows)
    def group_stats(selected: list[dict], positives: int) -> dict:
        good = sum(row["duration_ms"] >= TARGET_MS for row in selected)
        return {
            "selected": len(selected),
            "true_windows": good,
            "false_windows": len(selected) - good,
            "precision": good / len(selected) if selected else None,
            "recall": good / positives if positives else None,
            "workflows": len({row["workflow"] for row in selected}),
        }
    baseline = [row for row in rows if row["score"] >= DEFAULT_THRESHOLD]
    adapted = [
        row for row in rows
        if row["score"] >= gates.get(row["shape"], DEFAULT_THRESHOLD)
    ]
    by_project = {}
    for project in sorted({row["project"] for row in rows}):
        project_rows = [row for row in rows if row["project"] == project]
        project_positive = sum(
            row["duration_ms"] >= TARGET_MS for row in project_rows
        )
        by_project[project] = {
            "survived_100ms_first_inputs": len(project_rows),
            "true_windows": project_positive,
            "baseline": group_stats(
                [row for row in baseline if row["project"] == project],
                project_positive,
            ),
            "shape_gate": group_stats(
                [row for row in adapted if row["project"] == project],
                project_positive,
            ),
        }
    return {
        "survived_100ms_first_inputs": len(rows),
        "true_windows": positive,
        "baseline": group_stats(baseline, positive),
        "shape_gate": group_stats(adapted, positive),
        "by_project": by_project,
    }


def evaluate(train_workflows: Path, heldout_workflows: Path,
             artifact: Path) -> dict:
    train_ids, train_errors = require_complete_batch(train_workflows)
    heldout_ids, heldout_errors = require_complete_batch(heldout_workflows)
    head = FrozenToolWindowShadow(artifact)
    train_projects = {item.split("__", 1)[0] for item in train_ids}
    heldout_projects = {item.split("__", 1)[0] for item in heldout_ids}
    if (
        train_projects != head.training_projects
        or not train_projects.isdisjoint(heldout_projects)
        or set(train_ids) & set(heldout_ids)
    ):
        raise ValueError("frozen model must match project-disjoint training batch")
    if hashlib.sha256(
        (train_workflows.parent / "manifest.json").read_bytes()
    ).hexdigest() != json.loads(artifact.read_text(encoding="utf-8"))[
        "training_manifest_sha256"
    ]:
        raise ValueError("frozen training manifest changed")
    train, train_censor = cold_calls(
        train_workflows, include_returned_failures=True
    )
    heldout, heldout_censor = cold_calls(
        heldout_workflows, include_returned_failures=True
    )
    first_train = first_inputs(train)
    first_heldout = first_inputs(heldout)
    gates, evidence = choose_gates(training_oof(first_train))
    scored_heldout = [
        {**row, "score": head.estimate(_features(row)).probability}
        for row in first_heldout
    ]
    return {
        "status": "development_project_disjoint_shape_gate_not_action_eligible",
        "train_projects": sorted(train_projects),
        "heldout_projects": sorted(heldout_projects),
        "train_frozen_workflows": len(train_ids),
        "heldout_frozen_workflows": len(heldout_ids),
        "train_runner_errors": train_errors,
        "heldout_runner_errors": heldout_errors,
        "train_censor": train_censor,
        "heldout_censor": heldout_censor,
        "frozen_model_sha256": head.artifact_sha256,
        "training_shape_gates": gates,
        "training_oof_evidence": evidence,
        "heldout": quality(scored_heldout, gates),
        "scope": (
            "Gates are selected from train-project out-of-fold tool labels only. "
            "Held-out returns are scored by the already frozen classifier. "
            "The 100 ms survival and complete-return filters are retrospective; "
            "this is not a live-timer, JOIN, or physical transfer result. "
            "Astropy/Sphinx have been used for development and are not sealed tests."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, required=True)
    parser.add_argument("--heldout-workflows", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = evaluate(
        args.train_workflows, args.heldout_workflows, args.artifact
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
