#!/usr/bin/env python3
"""Compare a live 100 ms tool clock with a frozen, project-disjoint shape prior."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
from statistics import median
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_early_tool_wait_shadow import _eligible, audit
from scripts.audit_repeated_tool_timing import _quantile
from scripts.evaluate_cold_tool_project_loo import require_complete_batch
from scripts.evaluate_cold_tool_structure_holdout import (
    _paired_long_gain, cold_calls,
)


def evaluate(train_rows: list[dict], heldout_workflows: Path) -> dict:
    durations = [
        row["duration_ms"] for row in train_rows
        if row["status"] == "success" and row["duration_ms"] > 100
    ]
    if not durations:
        raise ValueError("training projects have no successful 100 ms survivors")
    global_prior = float(median(durations))
    by_shape: dict[str, list[float]] = defaultdict(list)
    for row in train_rows:
        if row["status"] == "success" and row["duration_ms"] > 100:
            by_shape[row["shape"]].append(row["duration_ms"])
    frozen_shape = {
        shape: float(median(values)) for shape, values in by_shape.items()
        if len(values) >= 4
    }

    paired: list[dict] = []
    all_success_windows: set[tuple[str, str, str]] = set()
    window_reasons: Counter[str] = Counter()
    censored_starts = 0
    for path in sorted(heldout_workflows.glob("*/runtime_events.deepagents.jsonl")):
        starts: dict[tuple[str, str], dict] = {}
        ends: dict[tuple[str, str], dict] = {}
        signals: dict[tuple[str, str], dict] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            attrs = event.get("attributes") or {}
            call_id = attrs.get("tool_call_id")
            invocation = event.get("invocation_id")
            if not call_id or not invocation:
                continue
            key = str(invocation), str(call_id)
            kind = event.get("kind")
            if kind == "tool_start":
                if key in starts:
                    raise ValueError(f"duplicate start: {path}: {key}")
                starts[key] = event
            elif kind == "tool_end":
                if key in ends:
                    raise ValueError(f"duplicate end: {path}: {key}")
                ends[key] = event
            elif kind == "structured_action" and attrs.get(
                "beliefkv_tool_wait_early_shadow"
            ) is True:
                if key in signals:
                    raise ValueError(f"duplicate observation: {path}: {key}")
                signals[key] = event
        first_success: set[tuple[str, str]] = set()
        for key, start in starts.items():
            attrs = start.get("attributes") or {}
            if not (
                attrs.get("is_child") is True
                and attrs.get("tool_name") == "execute"
                and attrs.get("previous_same_input_status") != "success"
            ):
                continue
            ended = ends.get(key)
            if ended is None:
                censored_starts += 1
                continue
            if (ended.get("attributes") or {}).get("status") != "success":
                continue
            input_hash = attrs.get("input_sha256")
            if not isinstance(input_hash, str) or len(input_hash) != 64:
                continue
            identity = key[0], input_hash
            if identity in first_success:
                continue
            first_success.add(identity)
            duration = float(ended["ts_ms"] - start["ts_ms"])
            if duration >= 600:
                all_success_windows.add((path.parent.name, *identity))
            signal = signals.get(key)
            if duration >= 600:
                support = attrs.get("project_shape_survivor_100ms_support")
                total = attrs.get("project_shape_survivor_100ms_total_median_ms")
                spread = attrs.get("project_shape_survivor_100ms_deviation_p90_ms")
                if type(support) is not int or support < 4 or (
                    type(total) not in (int, float) or not math.isfinite(total)
                ):
                    reason = "missing_supported_history"
                elif total <= 1100:
                    reason = "historical_median_at_most_1100ms"
                elif type(spread) not in (int, float) or not math.isfinite(
                    spread
                ) or spread > 1000 or spread < 0:
                    reason = "historical_spread_above_1000ms"
                elif signal is None:
                    reason = "qualified_but_no_live_observation"
                else:
                    reason = "observed"
                window_reasons[reason] += 1
            if signal is None:
                continue
            if not _eligible(attrs):
                raise ValueError(f"observed call lacks frozen qualification: {key}")
            elapsed = float(signal["ts_ms"] - start["ts_ms"])
            if elapsed < 100 or elapsed >= duration:
                raise ValueError(f"observation did not precede return: {key}")
            prior = float(attrs["project_shape_survivor_100ms_total_median_ms"])
            shape = str(attrs.get("observed_command_shape") or "")
            paired.append({
                "workflow": path.parent.name,
                "duration_ms": duration,
                "lead_ms": float(ended["ts_ms"] - signal["ts_ms"]),
                "elapsed_ms": elapsed,
                "online_total_ms": prior,
                "frozen_total_ms": frozen_shape.get(shape, global_prior),
                "frozen_shape_supported": shape in frozen_shape,
            })

    reference = np.asarray([row["frozen_total_ms"] for row in paired])
    online = np.asarray([row["online_total_ms"] for row in paired])
    online_errors = [
        abs(row["duration_ms"] - row["online_total_ms"]) for row in paired
    ]
    reference_errors = [
        abs(row["duration_ms"] - row["frozen_total_ms"]) for row in paired
    ]
    selected = [
        row for row in paired
        if row["online_total_ms"] - row["elapsed_ms"] >= 500
    ]
    return {
        "status": "read_only_project_disjoint_100ms_tool_timing_not_action_eligible",
        "frozen_train_success_survivors": len(durations),
        "frozen_train_global_total_ms": global_prior,
        "frozen_shape_support": {
            shape: len(by_shape[shape]) for shape in sorted(frozen_shape)
        },
        "heldout_success_distinct_observed_inputs": len(paired),
        "heldout_success_window_500ms_start_count": len(all_success_windows),
        "heldout_success_window_500ms_start_reasons": dict(sorted(
            window_reasons.items()
        )),
        "heldout_success_censored_tool_starts": censored_starts,
        "heldout_success_observed_window_500ms": sum(
            row["lead_ms"] >= 500 for row in paired
        ),
        "heldout_frozen_shape_supported": sum(
            row["frozen_shape_supported"] for row in paired
        ),
        "online_error_p50_ms": _quantile(online_errors, .5),
        "online_error_p90_ms": _quantile(online_errors, .9),
        "frozen_error_p50_ms": _quantile(reference_errors, .5),
        "frozen_error_p90_ms": _quantile(reference_errors, .9),
        "paired_gain": _paired_long_gain(paired, reference, online),
        "online_selected_remaining_500ms": {
            "distinct_success_inputs": len(selected),
            "true_remaining_500ms": sum(
                row["lead_ms"] >= 500 for row in selected
            ),
            "returned_before_500ms": sum(
                row["lead_ms"] < 500 for row in selected
            ),
        },
        "scope": (
            "Project-disjoint frozen shape baseline, causally updated same-project "
            "online prior; same successfully returned first input for paired errors. "
            "Observed qualification and success are conditional. Start-count "
            "coverage includes unsupported/early calls; 600 ms is only an "
            "upper bound on true 100 ms + 500 ms window opportunities. "
            "Not a JOIN, physical prefetch or throughput evaluation."
        ),
    }


def compare(train_workflows: Path, heldout_workflows: Path) -> dict:
    train_ids, train_errors = require_complete_batch(train_workflows)
    heldout_ids, heldout_errors = require_complete_batch(heldout_workflows)
    train_projects = {item.split("__", 1)[0] for item in train_ids}
    heldout_projects = {item.split("__", 1)[0] for item in heldout_ids}
    if train_projects & heldout_projects:
        raise ValueError("training and heldout projects must be disjoint")
    traced_ids = {
        path.parent.name for path in heldout_workflows.glob(
            "*/runtime_events.deepagents.jsonl"
        )
    }
    if traced_ids != set(heldout_ids):
        raise ValueError("heldout batch lacks frozen workflow traces or has extras")
    train_rows, censor = cold_calls(train_workflows)
    report = evaluate(train_rows, heldout_workflows)
    # The existing audit verifies that an emitted observation was supported
    # at TOOL_START, unique and occurred before its TOOL_END.
    validated = audit(heldout_workflows)
    report.update({
        "train_projects": sorted(train_projects),
        "heldout_projects": sorted(heldout_projects),
        "train_workflows": len(train_ids),
        "heldout_workflows": len(heldout_ids),
        "train_runner_errors": train_errors,
        "heldout_runner_errors": heldout_errors,
        "train_censor": censor,
        "heldout_early_audit": validated,
    })
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, required=True)
    parser.add_argument("--heldout-workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = compare(args.train_workflows, args.heldout_workflows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
