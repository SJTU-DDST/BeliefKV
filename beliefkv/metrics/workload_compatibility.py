"""Necessary realized-work checks for descriptive workflow JCT comparisons."""

from __future__ import annotations

import math


WORK_METRICS = ("output_tokens", "llm_requests", "input_tokens")
DEFAULT_WORK_TOLERANCE = .05


def compare_workload(left: dict, right: dict, *, tolerance: float = DEFAULT_WORK_TOLERANCE) -> dict:
    if type(tolerance) not in (int, float) or not math.isfinite(tolerance) or not 0 < tolerance < 1:
        raise ValueError("invalid realized-work tolerance")
    metrics = {}
    for field in (*WORK_METRICS, "tool_calls"):
        before, after = left.get(field), right.get(field)
        valid = all(
            type(value) in (int, float) and math.isfinite(value) and value >= 0
            for value in (before, after)
        )
        if not valid:
            metrics[field] = {"available": False}
            continue
        gap = abs(before - after) / max(before, after) if max(before, after) else 0.
        metrics[field] = {
            "available": True, "left": before, "right": after,
            "left_vs_right_change_percent": (
                100. * (before / after - 1.) if after else 0. if before == 0 else None
            ),
            "symmetric_relative_gap": gap, "within_tolerance": gap <= tolerance + 1e-12,
        }
    available = all(metrics[field]["available"] for field in WORK_METRICS)
    positive = available and all(
        metrics[field]["left"] > 0 and metrics[field]["right"] > 0 for field in WORK_METRICS
    )
    close = positive and all(metrics[field]["within_tolerance"] for field in WORK_METRICS)
    return {
        "necessary_workload_condition_passed": close,
        "status": "workload_close_other_checks_required" if close else
        "workload_mismatch" if positive else "workload_evidence_missing",
        "tolerance": tolerance, "required_metrics": list(WORK_METRICS), "metrics": metrics,
        "log_ratio_distance": (
            math.sqrt(sum(math.log(metrics[field]["left"] / metrics[field]["right"]) ** 2
                          for field in WORK_METRICS) / len(WORK_METRICS))
            if positive else None
        ),
        "direct_jct_speedup_accepted": False,
        "scope": (
            "Realized work is a necessary condition, not causal or task-quality proof. "
            "The 5% default is a declared engineering screening threshold, not a statistical "
            "guarantee. Check task populations, paired work distributions, context, capacity, "
            "seed and completion quality separately; do not replace JCT with JCT/token."
        ),
    }


def classify_workload(run: dict, references: dict[str, dict]) -> dict:
    comparisons = {name: compare_workload(run, row) for name, row in references.items()}
    distances = {
        name: row["log_ratio_distance"] for name, row in comparisons.items()
        if row["log_ratio_distance"] is not None
    }
    nearest = None
    if len(distances) == len(references):
        minimum = min(distances.values())
        winners = [name for name, value in distances.items()
                   if math.isclose(value, minimum, rel_tol=1e-9, abs_tol=1e-12)]
        nearest = winners[0] if len(winners) == 1 else "tie"
    return {
        "closest_reference": nearest,
        "close_references": [
            name for name, row in comparisons.items() if row["necessary_workload_condition_passed"]
        ],
        "comparisons": comparisons,
        "closer_does_not_imply_close": True,
    }
