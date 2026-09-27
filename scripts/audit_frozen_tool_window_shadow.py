#!/usr/bin/env python3
"""Audit frozen-model 100 ms tool observations against a completed workflow batch."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from statistics import median
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.predictor.tool_window_shadow import FrozenToolWindowShadow
from scripts.audit_repeated_tool_timing import _quantile
from scripts.evaluate_cold_tool_project_loo import require_complete_batch
from scripts.evaluate_cold_tool_structure_holdout import _paired_long_gain


def audit(run_dir: Path, *, artifact: Path) -> dict:
    workloads = run_dir / "intent_workloads"
    workflows = workloads / "workflows"
    expected, errors = require_complete_batch(workflows)
    if errors:
        raise ValueError(f"heldout runner errors: {errors}")
    traced = {
        path.parent.name for path in workflows.glob(
            "*/runtime_events.deepagents.jsonl"
        )
    }
    if traced != set(expected):
        raise ValueError("trace identities differ from the frozen workload batch")
    run_manifest = json.loads(
        (workloads / "manifest.json").read_text(encoding="utf-8")
    )
    config = run_manifest["config"]
    if (
        not config.get("tool_window_shadow_artifact")
        or Path(config["tool_window_shadow_artifact"]).resolve()
        != artifact.resolve()
        or config.get("early_tool_wait_shadow")
    ):
        raise ValueError("run does not use the requested isolated shadow artifact")
    head = FrozenToolWindowShadow(artifact)
    if head.training_projects & {
        name.split("__", 1)[0] for name in expected
    }:
        raise ValueError("training and heldout projects overlap")
    first_inputs = set()
    actual_windows = set()
    censored = 0
    observed = []
    reasons: Counter[str] = Counter()
    other_published = 0
    for instance in expected:
        path = workflows / instance / "runtime_events.deepagents.jsonl"
        events = [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not events or events[-1]["kind"] != "workflow_end":
            raise ValueError(f"incomplete workflow trace: {instance}")
        starts, ends, signals = {}, {}, {}
        for event in events:
            attrs = event.get("attributes") or {}
            if event["kind"] == "tool_wait_observation":
                other_published += 1
            if attrs.get("beliefkv_tool_wait_early_shadow") is True:
                raise ValueError("legacy early shadow mixed with frozen model")
            call_id = attrs.get("tool_call_id")
            invocation_id = event.get("invocation_id")
            if not call_id or not invocation_id:
                continue
            key = str(invocation_id), str(call_id)
            kind = event["kind"]
            if kind == "tool_start":
                if key in starts:
                    raise ValueError(f"duplicate tool start: {instance}: {key}")
                starts[key] = event
            elif kind == "tool_end":
                if key in ends:
                    raise ValueError(f"duplicate tool end: {instance}: {key}")
                ends[key] = event
            elif kind == "structured_action" and attrs.get(
                "beliefkv_tool_window_100ms_shadow"
            ) is True:
                if key in signals:
                    raise ValueError(f"duplicate model observation: {instance}: {key}")
                signals[key] = event
        if set(signals) - set(starts) or set(ends) - set(starts):
            raise ValueError(f"orphan tool signal or end: {instance}")
        for key, start in starts.items():
            attrs = start.get("attributes") or {}
            if not (
                attrs.get("is_child") is True
                and attrs.get("tool_name") == "execute"
                and "previous_same_input_status" not in attrs
            ):
                if key in signals:
                    raise ValueError(f"repeated or non-child input observed: {key}")
                continue
            sha = attrs.get("input_sha256")
            if not isinstance(sha, str) or len(sha) != 64:
                raise ValueError(f"missing first-input identity: {key}")
            distinct = (instance, key[0], sha)
            if distinct in first_inputs:
                if key in signals:
                    raise ValueError(f"repeat input was observed: {key}")
                continue
            first_inputs.add(distinct)
            forecast = head.estimate(attrs)
            if (
                attrs.get("tool_window_shadow_artifact_sha256")
                != head.artifact_sha256
                or type(attrs.get("tool_window_shadow_probability"))
                not in (int, float)
                or not math.isclose(
                    attrs["tool_window_shadow_probability"],
                    forecast.probability, abs_tol=1e-5,
                )
                or not math.isclose(
                    float(attrs.get("tool_window_shadow_total_eta_ms", -1)),
                    forecast.total_eta_ms, abs_tol=1e-4,
                )
            ):
                raise ValueError(f"tool-start score differs from frozen model: {key}")
            end = ends.get(key)
            if end is None:
                censored += 1
            elif end["ts_ms"] - start["ts_ms"] >= 600:
                actual_windows.add(distinct)
            selected = forecast.probability >= head.threshold
            if not selected and key in signals:
                raise ValueError(f"unselected tool produced observation: {key}")
            if selected:
                reasons["selected_starts"] += 1
            if key not in signals:
                if selected:
                    reasons["selected_without_observation"] += 1
                continue
            signal = signals[key]
            properties = signal.get("attributes") or {}
            elapsed = float(signal["ts_ms"] - start["ts_ms"])
            if (
                end is None or end["ts_ms"] < signal["ts_ms"]
                or elapsed < 100
                or properties.get("source") != "deepagents_tool_window_shadow"
                or properties.get("diagnostic_only") is not True
                or properties.get("tool_window_artifact_sha256")
                != head.artifact_sha256
                or not math.isclose(
                    float(properties.get("tool_elapsed_ms", -1)),
                    elapsed, abs_tol=1,
                )
                or not math.isclose(
                    float(properties.get("tool_window_probability", -1)),
                    forecast.probability, abs_tol=1e-5,
                )
                or not math.isclose(
                    float(properties.get("tool_window_remaining_eta_ms", -1)),
                    forecast.total_eta_ms - elapsed, abs_tol=1,
                )
            ):
                raise ValueError(f"model observation violates live identity: {key}")
            status = (end.get("attributes") or {}).get("status")
            if status not in ("success", "error"):
                raise ValueError(f"unexpected tool return status: {key}")
            reasons["observed"] += 1
            reasons[f"observed_{status}"] += 1
            observed.append({
                "workflow": instance,
                "status": status,
                "duration_ms": float(end["ts_ms"] - start["ts_ms"]),
                "lead_ms": float(end["ts_ms"] - signal["ts_ms"]),
                "eta_ms": forecast.total_eta_ms,
                "global_eta_ms": forecast.global_eta_ms,
            })
    summary = json.loads((workloads / "summary.json").read_text(encoding="utf-8"))
    timer = summary["tool_wait_shadow"]
    if (
        timer["errors"] != 0 or timer["dropped"] != 0 or timer["pending"] != 0
        or timer["published"] != len(observed) + other_published
    ):
        raise ValueError("timer accounting disagrees with frozen observations")
    by_status = {}
    for status in ("success", "error"):
        group = [row for row in observed if row["status"] == status]
        errors = [abs(row["duration_ms"] - row["eta_ms"]) for row in group]
        global_errors = [
            abs(row["duration_ms"] - row["global_eta_ms"]) for row in group
        ]
        by_status[status] = {
            "observed": len(group),
            "true_remaining_500ms": sum(
                row["lead_ms"] >= 500 for row in group
            ),
            "remaining_lead_p50_ms": (
                median(row["lead_ms"] for row in group) if group else None
            ),
            "eta_p50_absolute_error_ms": _quantile(errors, .5),
            "eta_p90_absolute_error_ms": _quantile(errors, .9),
            "global_eta_p50_absolute_error_ms": _quantile(global_errors, .5),
            "paired_eta_gain_vs_global": _paired_long_gain(
                group, np.asarray([row["global_eta_ms"] for row in group]),
                np.asarray([row["eta_ms"] for row in group]),
            ),
        }
    return {
        "status": "project_disjoint_live_100ms_shadow_not_action_eligible",
        "artifact_sha256": head.artifact_sha256,
        "training_projects": sorted(head.training_projects),
        "heldout_projects": sorted({
            name.split("__", 1)[0] for name in expected
        }),
        "frozen_workflows": len(expected),
        "first_cold_inputs": len(first_inputs),
        "actual_start_to_return_600ms_windows": len(actual_windows),
        "right_censored_first_inputs": censored,
        "counts": dict(sorted(reasons.items())),
        "by_status": by_status,
        "timer": timer,
        "limitation": (
            "Only authenticated tool observations, not predicted KV actions. "
            "Return-time ETA is conditional on the selected tool still running "
            "at 100 ms; outcome status and actual lead are known only afterwards. "
            "Start-to-return 600 ms count is an ideal timer upper bound, not "
            "a claim of on-time H2D or JOIN benefit."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = audit(args.run_dir, artifact=args.artifact)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
