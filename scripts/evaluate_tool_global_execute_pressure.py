#!/usr/bin/env python3
"""Nested project holdout of causal global execute pressure at TOOL_START."""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys

import orjson

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.predictor.tool_window_shadow import FrozenToolWindowShadow
from scripts.evaluate_cold_tool_project_loo import require_complete_batch
from scripts.evaluate_cold_tool_structure_holdout import cold_calls
from scripts.evaluate_tool_window_shape_gates import (
    DEFAULT_THRESHOLD, _features, training_oof,
)
from scripts.pilot_cold_child_tool_long import _fit_shape_head, _shape_scores
from scripts.pilot_tool_return_window_100ms import TARGET_MS, first_inputs


ACTIVE_THRESHOLDS = (6, 8, 9, 12)
INPUT_LIMITS = (40, 48, 64, 96)
POLICIES = tuple(
    (active, length) for active in ACTIVE_THRESHOLDS for length in INPUT_LIMITS
)


def _boundaries(workflows: Path) -> tuple[list[float], list[float]]:
    starts, ends = [], []
    for path in sorted(workflows.glob("*/runtime_events.deepagents.jsonl")):
        calls = {}
        with path.open("rb") as stream:
            for line in stream:
                if not line.strip():
                    continue
                event = orjson.loads(line)
                attrs = event.get("attributes") or {}
                call_id = attrs.get("tool_call_id")
                if attrs.get("tool_name") != "execute" or not call_id:
                    continue
                ts = float(event["ts_ms"])
                if not math.isfinite(ts):
                    raise ValueError("non-finite execute event timestamp")
                if event["kind"] == "tool_start":
                    if call_id in calls:
                        raise ValueError(f"duplicate execute start: {call_id}")
                    calls[call_id] = ts
                    starts.append(ts)
                elif event["kind"] == "tool_end":
                    if call_id not in calls or ts < calls.pop(call_id):
                        raise ValueError(f"orphan or negative execute end: {call_id}")
                    ends.append(ts)
    return sorted(starts), sorted(ends)


def attach_pressure(
    rows: list[dict], starts: list[float], ends: list[float],
) -> list[dict]:
    if starts != sorted(starts) or ends != sorted(ends):
        raise ValueError("execute boundaries must be ordered")
    result = []
    for row in rows:
        when = float(row["start_ts_ms"])
        # Exclude every start and end at the exact same timestamp; neither
        # event is strictly observable before this tool's own TOOL_START.
        active = bisect_left(starts, when) - bisect_left(ends, when)
        if active < 0:
            raise ValueError("execute boundaries yield negative occupancy")
        result.append({**row, "other_execute_inflight": active})
    return result


def load(workflows: Path) -> tuple[list[dict], dict]:
    ids, errors = require_complete_batch(workflows)
    starts, ends = _boundaries(workflows)
    rows, censor = cold_calls(
        workflows, include_returned_failures=True,
    )
    return attach_pressure(first_inputs(rows), starts, ends), {
        "frozen_workflows": len(ids),
        "runner_errors": errors,
        "frozen_projects": sorted({
            item.split("__", 1)[0] for item in ids
        }),
        "first_surviving_inputs": len(first_inputs(rows)),
        "unmatched_execute_starts": len(starts) - len(ends),
        "censor": censor,
    }


def _candidate(row: dict, policy: tuple[int, int]) -> bool:
    active, length = policy
    return (
        row["shape"] == "other"
        and row["input_chars"] <= length
        and row["other_execute_inflight"] >= active
        and row["score"] < DEFAULT_THRESHOLD
    )


def _quality(rows: list[dict]) -> dict:
    positive = sum(row["duration_ms"] >= TARGET_MS for row in rows)
    by_project = defaultdict(list)
    for row in rows:
        by_project[row["project"]].append(row)
    return {
        "added": len(rows),
        "true_windows": positive,
        "false_windows": len(rows) - positive,
        "precision": positive / len(rows) if rows else None,
        "workflows": len({row["workflow"] for row in rows}),
        "by_project": {
            name: {
                "added": len(items),
                "true_windows": sum(
                    item["duration_ms"] >= TARGET_MS for item in items
                ),
                "workflows": len({
                    item["workflow"] for item in items
                }),
            }
            for name, items in sorted(by_project.items())
        },
    }


def choose(scored: list[dict]) -> tuple[tuple[int, int] | None, dict]:
    reports = {}
    qualified = []
    for policy in POLICIES:
        added = [row for row in scored if _candidate(row, policy)]
        quality = _quality(added)
        supported = [
            row for row in quality["by_project"].values() if row["added"] >= 2
        ]
        valid = (
            quality["added"] >= 5
            and quality["workflows"] >= 5
            and len(quality["by_project"]) >= 3
            and quality["precision"] is not None
            and quality["precision"] >= .8
            and all(
                row["true_windows"] / row["added"] >= .75
                for row in supported
            )
        )
        reports[f"{policy[0]}:{policy[1]}"] = {
            **quality, "qualified": valid,
        }
        if valid:
            qualified.append((
                quality["true_windows"], quality["precision"],
                -quality["false_windows"], policy,
            ))
    return max(qualified)[3] if qualified else None, reports


def evaluate(train_workflows: Path, heldout_workflows: Path | None,
             artifact: Path | None) -> dict:
    train, train_source = load(train_workflows)
    projects = sorted({row["project"] for row in train})
    if len(projects) < 4:
        raise ValueError("nested tool gate requires four training projects")
    folds, added = {}, []
    for project in projects:
        inner = [row for row in train if row["project"] != project]
        outer = [row for row in train if row["project"] == project]
        policy, _ = choose(training_oof(inner))
        model = _fit_shape_head(
            inner, include_live_peers=True,
            include_duration_priors=True, target_ms=TARGET_MS,
        )
        scores = _shape_scores(
            model, outer, include_live_peers=True,
            include_duration_priors=True,
        )
        scored = [
            {**row, "score": float(score)}
            for row, score in zip(outer, scores, strict=True)
        ]
        gain = (
            [row for row in scored if _candidate(row, policy)]
            if policy else []
        )
        folds[project] = {
            "train_only_policy": policy,
            "heldout_survived_first_inputs": len(scored),
            "added": _quality(gain),
        }
        added.extend(gain)
    policy, train_evidence = choose(training_oof(train))
    report = {
        "status": "train_only_nested_project_tool_pressure_not_action_eligible",
        "train_projects": projects,
        "train_source": train_source,
        "nested_folds": folds,
        "nested_added": _quality(added),
        "train_only_selected_policy": policy,
        "train_only_policy_evidence": train_evidence,
        "scope": (
            "At the 100ms survival landmark, global execute occupancy is "
            "reconstructed exclusively from preceding TOOL_START/TOOL_END events. "
            "Only the first distinct child input is eligible. Each outer project "
            "is omitted both when selecting the pressure gate with inner OOF "
            "scores and when fitting its baseline classifier. No held-out "
            "project labels may select the policy. Window precision is not "
            "return-time ETA accuracy or predictive H2D eligibility."
        ),
    }
    if heldout_workflows is not None:
        if artifact is None:
            raise ValueError("heldout scoring requires frozen training artifact")
        heldout, heldout_source = load(heldout_workflows)
        if set(train_source["frozen_projects"]) & set(
            heldout_source["frozen_projects"]
        ):
            raise ValueError("heldout workload overlaps training projects")
        head = FrozenToolWindowShadow(artifact)
        if head.training_projects != set(train_source["frozen_projects"]):
            raise ValueError("frozen tool model does not match training projects")
        frozen = json.loads(artifact.read_text(encoding="utf-8"))
        manifest_hash = hashlib.sha256(
            (train_workflows.parent / "manifest.json").read_bytes()
        ).hexdigest()
        if frozen.get("training_manifest_sha256") != manifest_hash:
            raise ValueError("frozen tool model training manifest mismatch")
        scored = [
            {**row, "score": head.estimate(_features(row)).probability}
            for row in heldout
        ]
        picked = (
            [row for row in scored if _candidate(row, policy)]
            if policy else []
        )
        report.update({
            "status": "project_disjoint_development_not_action_eligible",
            "heldout_source": heldout_source,
            "heldout_added": _quality(picked),
        })
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", required=True, type=Path)
    parser.add_argument("--heldout-workflows", type=Path)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--output", required=True, type=Path)
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
