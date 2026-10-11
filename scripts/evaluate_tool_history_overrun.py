#!/usr/bin/env python3
"""Compare tool-history tail handling on identical recorded accepted forecasts."""

from __future__ import annotations

import argparse
import ast
from bisect import bisect_right
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from statistics import median
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import beliefkv.runtime.sglang_v0520_prediction as prediction
from scripts.audit_prefetch_lifecycle import distribution
from scripts.audit_wait_offload import (
    decision_summary, hint_from_forecast, read_tools, sealed_records, tool_decision,
)

SOURCE = "beliefkv/runtime/sglang_v0520_prediction.py"


def baseline_type(revision: str):
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{SOURCE}"], cwd=ROOT, text=True,
    )
    cls = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "NativeToolWaitHint"
    )
    namespace = dict(vars(prediction))
    module = ast.Module(body=[
        ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0,
        ), cls,
    ], type_ignores=[])
    exec(
        compile(ast.fix_missing_locations(module), f"{revision}:{SOURCE}", "exec"),
        namespace,
    )
    return namespace["NativeToolWaitHint"], hashlib.sha256(source.encode()).hexdigest()


def timing_summary(groups: dict, variant: str) -> dict:
    matched = list(groups.values())
    triggered = [
        group[variant]["first_500ms_trigger"]
        for group in matched if group[variant]["first_500ms_trigger"] is not None
    ]
    near_errors = [
        median(group[variant]["near_end_absolute_errors"])
        for group in matched if group[variant]["near_end_absolute_errors"]
    ]
    return {
        "independent_tools": len(matched),
        "tools_with_first_p50_le_500ms_snapshot": len(triggered),
        "first_trigger_with_actual_0_to_500ms_lead": sum(
            0 < lead <= 500. for lead in triggered
        ),
        "first_trigger_with_actual_500_to_1000ms_lead": sum(
            500. < lead <= 1000. for lead in triggered
        ),
        "first_trigger_more_than_1000ms_early": sum(lead > 1000. for lead in triggered),
        "first_trigger_after_observed_end": sum(lead <= 0 for lead in triggered),
        "first_trigger_actual_lead_ms": distribution(triggered),
        "tools_with_near_end_snapshots": sum(group["near_end_snapshots"] > 0 for group in matched),
        "tools_with_valid_near_end_p50": len(near_errors),
        "per_tool_near_end_median_absolute_error_ms": distribution(near_errors),
    }


def evaluate(arm: Path, revision: str) -> dict:
    baseline, digest = baseline_type(revision)
    variants = {"baseline": baseline, "optimized": prediction.NativeToolWaitHint}
    seals = []
    windows, _ = read_tools(arm, seals)
    starts = {
        scope: [row["start_monotonic_ms"] for row in group]
        for scope, group in windows.items()
    }
    forecasts, groups = {}, {}
    selected = {variant: defaultdict(list) for variant in variants}
    bursts, offset = set(), None
    skipped = defaultdict(int)
    for row in sealed_records(arm / "opportunities/admission_opportunities.jsonl", seals):
        kind = row["event"]
        if kind == "safe_point_census":
            offset = row["ts_ms"] - row["monotonic_ms"]
            continue
        if kind == "tool_wait_forecast":
            forecasts[row["context_id"], row["context_epoch"]] = row
            when = row["ts_ms"] - offset
            scope = row["context_id"], row["context_epoch"]
            values = windows.get(scope, ())
            index = bisect_right(starts.get(scope, ()), when)
            active = [
                window for window in values[:index]
                if window["end_monotonic_ms"] is None or when < window["end_monotonic_ms"]
            ]
            if len(active) != 1 or active[0]["end_monotonic_ms"] is None:
                skipped["no_single_complete_active_tool"] += 1
                continue
            window = active[0]
            identity = window["workflow_id"], window["invocation_id"], window["tool_run_id"]
            group = groups.setdefault(identity, {
                "near_end_snapshots": 0,
                **{
                    variant: {"first_500ms_trigger": None, "near_end_absolute_errors": []}
                    for variant in variants
                },
            })
            actual = window["end_monotonic_ms"] - when
            if actual <= 1000.:
                group["near_end_snapshots"] += 1
            for variant, hint_type in variants.items():
                hint = hint_from_forecast(row, hint_type)
                if not hint.live(hint.key, now_ms=when):
                    skipped[f"{variant}_expired_hint"] += 1
                    continue
                remaining = hint.remaining_quantile(.5, now_ms=when)
                if remaining is None:
                    continue
                if remaining <= 500. and group[variant]["first_500ms_trigger"] is None:
                    group[variant]["first_500ms_trigger"] = actual
                if actual <= 1000.:
                    group[variant]["near_end_absolute_errors"].append(abs(remaining - actual))
            continue
        category = None
        if kind == "prepare_native_issued" and row["source"] == "tool_wait":
            identity = tuple(row.get("burst_command_ids") or (row["command_id"],))
            if identity not in bursts:
                bursts.add(identity)
                category = "prepare"
        elif kind == "parent_pressure_demoted" and row["source"] == "tool_wait":
            category = "demotion"
        if category is not None:
            forecast = forecasts.get((row["context_id"], row["context_epoch"]))
            for variant, hint_type in variants.items():
                selected[variant][category].append(tool_decision(
                    row, forecast, offset, windows, starts, hint_type=hint_type,
                ))
    return {
        "scope": (
            "Development replay of the same frozen forecasts without fitting. "
            "Later tool ends are labels only. Selection comparisons re-evaluate "
            "recorded old-policy actions; they do not replay allocator decisions, "
            "identify all feasible opportunities or prove GPU throughput gains. "
            "Rolling timing uses accepted snapshots while exactly one completed "
            "tool is active; first triggers are recorded snapshots, not continuous "
            "start times. Near-end errors take each tool's median over snapshots "
            "with actual remaining <=1000ms before aggregating tools. No actual "
            "transmission, Host-only copy, capacity, admission or reuse is implied."
        ),
        "arm": str(arm), "baseline_revision": revision,
        "baseline_source_sha256": digest,
        "optimized_source_sha256": hashlib.sha256((ROOT / SOURCE).read_bytes()).hexdigest(),
        "source_seals": seals, "skipped_snapshots": dict(skipped),
        "summary": {
            variant: {
                "selected_decisions": {
                    category: {
                        "all_recorded_decisions": decision_summary(rows),
                        "reconstructed_long_wait_subset": decision_summary([
                            row for row in rows if row.get("runtime_long_wait_predicate") is True
                        ]),
                    }
                    for category, rows in selected[variant].items()
                },
                "rolling_tool_timing": timing_summary(groups, variant),
            }
            for variant in variants
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--baseline-revision", default="b854a91")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.arm.resolve(), args.baseline_revision)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result["summary"], indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
