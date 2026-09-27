#!/usr/bin/env python3
"""Causal, read-only project-history calibration of a frozen tool-window head."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.predictor.tool_window_shadow import FrozenToolWindowShadow
from scripts.audit_repeated_tool_timing import _quantile
from scripts.evaluate_cold_tool_project_loo import require_complete_batch
from scripts.evaluate_cold_tool_structure_holdout import cold_calls
from scripts.evaluate_cold_tool_structure_holdout import _paired_long_gain
from scripts.evaluate_tool_window_shape_gates import (
    DEFAULT_THRESHOLD, _features, training_oof,
)
from scripts.pilot_tool_return_window_100ms import (
    LANDMARK_MS, TARGET_MS, first_inputs,
)


POLICIES = ((3, 1.), (4, 1.), (5, 1.), (4, .8), (5, .8))
HISTORY_WORKFLOWS = 8


def _first_returns(rows: list[dict]) -> list[dict]:
    seen = set()
    first = []
    for row in sorted(rows, key=lambda item: (
        item["start_ts_ms"], item["workflow"], item["tool_call_id"],
    )):
        key = (
            row["workflow"], row["invocation"],
            row["input_sha256"] or row["tool_call_id"],
        )
        if key not in seen:
            first.append(row)
            seen.add(key)
    return first


def replay(
    candidates: list[dict], history: list[dict], *,
    min_history: int, min_fraction: float,
) -> list[dict]:
    if (
        min_history <= 0 or min_history > HISTORY_WORKFLOWS
        or not 0 < min_fraction <= 1
    ):
        raise ValueError("invalid causal history policy")
    # An older workflow's tool must already have returned at the instant
    # the current candidate reaches its 100 ms survival landmark.
    completed = sorted(history, key=lambda row: row["terminal_ts_ms"])
    ordered = sorted(candidates, key=lambda row: (
        row["start_ts_ms"] + LANDMARK_MS,
        row["workflow"], row["tool_call_id"],
    ))
    available: dict[tuple[str, str], list[dict]] = defaultdict(list)
    index = 0
    out = []
    for candidate in ordered:
        now = candidate["start_ts_ms"] + LANDMARK_MS
        while (
            index < len(completed)
            and completed[index]["terminal_ts_ms"] < now
        ):
            row = completed[index]
            available[row["project"], row["shape"]].append(row)
            index += 1
        previous = []
        workflows = set()
        for row in reversed(available[
            candidate["project"], candidate["shape"]
        ]):
            workflow = row["workflow"]
            if workflow == candidate["workflow"] or workflow in workflows:
                continue
            previous.append(row)
            workflows.add(workflow)
            if len(previous) == HISTORY_WORKFLOWS:
                break
        positives = sum(
            row["duration_ms"] >= TARGET_MS for row in previous
        )
        fraction = positives / len(previous) if previous else 0.
        override = (
            len(previous) >= min_history and fraction >= min_fraction
        )
        out.append({
            **candidate,
            "causal_history_workflows": len(previous),
            "causal_history_positive_fraction": fraction,
            "history_override": bool(override),
            "baseline_selected": (
                candidate["score"] >= DEFAULT_THRESHOLD
            ),
        })
    return out


def summarize(rows: list[dict]) -> dict:
    selected = [
        row for row in rows if row["baseline_selected"]
    ]
    added = [
        row for row in rows if row["history_override"]
        and not row["baseline_selected"]
    ]
    def stats(group: list[dict]) -> dict:
        true = sum(row["duration_ms"] >= TARGET_MS for row in group)
        report = {
            "selected": len(group),
            "true_windows": true,
            "false_windows": len(group) - true,
            "precision": true / len(group) if group else None,
            "workflows": len({row["workflow"] for row in group}),
            "returned_success": sum(
                row.get("status") == "success" for row in group
            ),
            "returned_error": sum(
                row.get("status") == "error" for row in group
            ),
        }
        if group and all(
            "eta_total_ms" in row and "eta_global_ms" in row
            for row in group
        ):
            absolute = [
                abs(row["duration_ms"] - row["eta_total_ms"])
                for row in group
            ]
            global_absolute = [
                abs(row["duration_ms"] - row["eta_global_ms"])
                for row in group
            ]
            report["eta_p50_absolute_error_ms"] = _quantile(absolute, .5)
            report["eta_p90_absolute_error_ms"] = _quantile(absolute, .9)
            report["global_eta_p50_absolute_error_ms"] = _quantile(
                global_absolute, .5,
            )
        return report
    by_workflow = defaultdict(lambda: [0, 0])
    for row in added:
        by_workflow[row["workflow"]][
            0 if row["duration_ms"] >= TARGET_MS else 1
        ] += 1
    workflows = sorted({row["workflow"] for row in rows})
    net = np.asarray([
        by_workflow[name][0] - by_workflow[name][1]
        for name in workflows
    ])
    if len(net):
        draws = np.random.default_rng(0).choice(
            net, size=(4000, len(net)), replace=True,
        ).mean(axis=1)
        gain = {
            "workflow_mean_added_true_minus_false": float(np.mean(net)),
            "workflow_bootstrap_95pct_ci": [
                float(x) for x in np.percentile(draws, (2.5, 97.5))
            ],
        }
    else:
        gain = None
    return {
        "survived_100ms_first_inputs": len(rows),
        "actual_windows": sum(
            row["duration_ms"] >= TARGET_MS for row in rows
        ),
        "baseline": stats(selected),
        "additional_from_history": stats(added),
        "combined": stats(selected + added),
        "additional_net_window_gain": gain,
        "added_by_project": {
            project: stats([
                row for row in added if row["project"] == project
            ])
            for project in sorted({row["project"] for row in rows})
        },
        "added_by_shape": {
            shape: stats([
                row for row in added if row["shape"] == shape
            ])
            for shape in sorted({row["shape"] for row in added})
        },
        "added_true_window_eta_gain_vs_global": (
            _paired_long_gain(
                [row for row in added if row["duration_ms"] >= TARGET_MS],
                np.asarray([
                    row["eta_global_ms"] for row in added
                    if row["duration_ms"] >= TARGET_MS
                ]),
                np.asarray([
                    row["eta_total_ms"] for row in added
                    if row["duration_ms"] >= TARGET_MS
                ]),
            )
            if added and all(
                "eta_total_ms" in row and "eta_global_ms" in row
                for row in added
            ) else None
        ),
    }


def choose_policy(scored: list[dict], history: list[dict]) -> tuple[
    tuple[int, float] | None, dict,
]:
    reports = {}
    qualified = []
    for policy in POLICIES:
        result = summarize(replay(
            scored, history, min_history=policy[0],
            min_fraction=policy[1],
        ))
        added = result["additional_from_history"]
        supported = [
            item for item in result["added_by_project"].values()
            if item["selected"] >= 5
        ]
        safe = (
            added["selected"] >= 20
            and added["workflows"] >= 10
            and len(supported) >= 3
            and added["precision"] is not None
            and added["precision"] >= .8
            and all(
                item["precision"] is not None
                and item["precision"] >= .7
                for item in supported
            )
        )
        label = f"{policy[0]}:{policy[1]:.1f}"
        reports[label] = {"qualified": safe, **result}
        if safe:
            qualified.append((
                added["true_windows"], added["precision"],
                policy,
            ))
    return max(qualified)[2] if qualified else None, reports


def evaluate(
    train_workflows: Path, heldout_workflows: Path, artifact: Path,
) -> dict:
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
        raise ValueError("frozen training and heldout projects must be disjoint")
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
    train_history = _first_returns(train)
    heldout_history = _first_returns(heldout)
    policy, reports = choose_policy(
        training_oof(first_inputs(train)), train_history,
    )
    heldout_rows = []
    for row in first_inputs(heldout):
        forecast = head.estimate(_features(row))
        heldout_rows.append({
            **row,
            "score": forecast.probability,
            "eta_total_ms": forecast.total_eta_ms,
            "eta_global_ms": forecast.global_eta_ms,
        })
    return {
        "status": "read_only_causal_tool_history_development_not_action_eligible",
        "train_projects": sorted(train_projects),
        "heldout_projects": sorted(heldout_projects),
        "train_frozen_workflows": len(train_ids),
        "heldout_frozen_workflows": len(heldout_ids),
        "train_runner_errors": train_errors,
        "heldout_runner_errors": heldout_errors,
        "train_censor": train_censor,
        "heldout_censor": heldout_censor,
        "frozen_model_sha256": head.artifact_sha256,
        "selected_training_policy": policy,
        "training_policy_evidence": reports,
        "heldout": summarize(
            replay(
                heldout_rows, heldout_history,
                min_history=policy[0], min_fraction=policy[1],
            ) if policy else [
                {
                    **row,
                    "baseline_selected": row["score"] >= DEFAULT_THRESHOLD,
                    "history_override": False,
                }
                for row in heldout_rows
            ]
        ),
        "scope": (
            "Training projects alone select the policy using OOF model scores. "
            "The heldout project updates its own read-only history only after "
            "a different workflow's tool_end precedes the 100 ms landmark; "
            "at most one completed result per workflow and command shape is "
            "eligible. If no policy qualifies, the heldout comparison uses "
            "the frozen baseline, without a history override. Survival, "
            "completed-return and first-input filters are retrospective. "
            "No live timer, JOIN, KV action, or physical H2D is established."
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
        args.train_workflows, args.heldout_workflows, args.artifact,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
