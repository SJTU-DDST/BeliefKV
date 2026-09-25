#!/usr/bin/env python3
"""Evaluate causal cross-workflow exact-input tool history against v9."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
import heapq
import json
import math
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.predictor.structured_frontier import (
    FrontierBeliefModel, WaitBeliefKind, _local_features_from_row,
)
from scripts.compare_qwen35_tool_timing_models import (
    _metrics, _rows, _start_trigger_quality,
)


def _cross_workflow_priors(waits: list[dict], minimum_support: int) -> dict:
    if minimum_support < 1:
        raise ValueError("minimum_support must be positive")
    history = defaultdict(lambda: deque(maxlen=64))
    pending = []
    priors = {}
    for index, wait in sorted(
        enumerate(waits), key=lambda pair: (pair[1]["start_ts_ms"], pair[0])
    ):
        start = float(wait["start_ts_ms"])
        while pending and pending[0][0] < start:
            _, _, completed = heapq.heappop(pending)
            if completed.get("status") == "success":
                key = _signature(completed)
                if key is not None:
                    history[key].append((
                        completed["workflow_id"],
                        float(completed["terminal_ts_ms"])
                        - float(completed["start_ts_ms"]),
                    ))
        key = _signature(wait)
        if key is not None:
            values = [
                duration for workflow, duration in history[key]
                if workflow != wait["workflow_id"]
            ]
            if len(values) >= minimum_support:
                priors[wait["workflow_id"], wait["tool_call_id"]] = (
                    median(values), len(values)
                )
        heapq.heappush(pending, (
            float(wait["terminal_ts_ms"]), index, wait,
        ))
    return priors


def _signature(wait: dict):
    digest = wait.get("parameter_signature")
    if not isinstance(digest, str) or len(digest) != 64:
        return None
    return wait.get("project"), wait.get("tool_name"), digest


def _group(samples: list[dict]) -> dict:
    return {
        "count": len(samples),
        "workflows": len({row["workflow"] for row in samples}),
        "reference_v9": _metrics(samples, "reference"),
        "cross_workflow_exact": _metrics(samples, "candidate"),
        "reference_trigger_quality": _start_trigger_quality(samples, "reference"),
        "cross_workflow_trigger_quality": _start_trigger_quality(
            samples, "candidate",
        ),
    }


def compare(dataset: Path, model: FrontierBeliefModel,
            *, minimum_support: int = 2) -> dict:
    if model.tool_feature_contract != "observed_command_child_project_v3":
        raise ValueError("reference must use the Qwen3.5 v9 tool contract")
    waits = [
        row for row in _rows(dataset / "external_waits.jsonl")
        if row.get("is_child") is True
        and type(row.get("start_ts_ms")) in (float, int)
        and type(row.get("terminal_ts_ms")) in (float, int)
        and math.isfinite(row["start_ts_ms"])
        and math.isfinite(row["terminal_ts_ms"])
        and row["terminal_ts_ms"] >= row["start_ts_ms"]
    ]
    indexed = {(row["workflow_id"], row["tool_call_id"]): row for row in waits}
    priors = _cross_workflow_priors(waits, minimum_support)
    seen = set()
    groups = defaultdict(list)
    for row in _rows(dataset / "frontier_decision_points.jsonl"):
        if row.get("trigger_kind") != "tool_start":
            continue
        attrs = row.get("trigger_attributes") or {}
        identity = row.get("workflow_id"), attrs.get("tool_call_id")
        wait = indexed.get(identity)
        if (
            identity in seen or identity not in priors or wait is None
            or wait.get("training_eligible_survival") is not True
            or wait.get("censored") is True
            or row.get("trigger_invocation_id") != wait.get("invocation_id")
            or (
                attrs.get("tool_name") == "execute"
                and attrs.get("previous_same_input_status") == "success"
            )
        ):
            continue
        invocation = next((
            item for item in row.get("invocations") or ()
            if item.get("invocation_id") == wait["invocation_id"]
            and item.get("state") == "wait_tool"
            and item.get("is_child") is True
        ), None)
        if invocation is None:
            continue
        seen.add(identity)
        belief = model.predict(_local_features_from_row(
            row, invocation, tool_feature_contract=model.tool_feature_contract,
        )).wait_belief
        if belief is None or belief.kind is not WaitBeliefKind.TOOL:
            continue
        reference = belief.residual_duration.quantile(.5)
        if not math.isfinite(reference):
            continue
        actual = float(wait["terminal_ts_ms"]) - float(row["timestamp_ms"])
        elapsed = float(row["timestamp_ms"]) - float(wait["start_ts_ms"])
        if actual < 0 or elapsed < 0:
            continue
        candidate = max(0, priors[identity][0] - elapsed)
        sample = {
            "workflow": identity[0], "actual_ms": actual,
            "reference": abs(reference - actual),
            "candidate": abs(candidate - actual),
            "reference_forecast_ms": reference,
            "candidate_forecast_ms": candidate,
        }
        groups["all"].append(sample)
        groups[f"project:{wait['project']}"].append(sample)
        if actual >= 2_000:
            groups["long"].append(sample)
            groups[f"long_project:{wait['project']}"].append(sample)
        if (
            attrs.get("tool_name") == "execute"
            and type(attrs.get("project_class_duration_median_ms")) in (int, float)
            and int(attrs.get("project_class_completed_support") or 0) >= 16
        ):
            groups["with_project_prior"].append(sample)
        else:
            groups["without_project_prior"].append(sample)
    return {
        "status": "matched_causal_calibration_diagnostic_not_action_eligible",
        "minimum_other_workflow_completed_support": minimum_support,
        "dataset": str(dataset.resolve()),
        "prior_available": len(priors),
        "groups": {key: _group(items) for key, items in sorted(groups.items())},
        "limitations": (
            "Same project and same hashed input, completed success from OTHER "
            "workflow strictly before target start; latest 64 completions, "
            "censored targets excluded; no cross-project transferability claim"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--v9-model", type=Path, required=True)
    parser.add_argument("--minimum-support", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(
        args.dataset_dir, FrontierBeliefModel.load(args.v9_model),
        minimum_support=args.minimum_support,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
