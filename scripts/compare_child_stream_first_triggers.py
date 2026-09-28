#!/usr/bin/env python3
"""Paired, workflow-clustered first-trigger gains from a frozen score report."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pilot_child_stream_content import collect


def compare(
    report: dict,
    workflows: list[Path],
    baseline: str,
    candidate: str,
    *,
    service_live: bool = False,
    draws: int = 10000,
) -> dict:
    if draws < 1:
        raise ValueError("draws must be positive")
    if baseline == candidate:
        raise ValueError("baseline and candidate must be distinct")
    rows, _ = collect(
        workflows, min_snapshot_chars=report["min_snapshot_chars"],
        exclude_boundary_snapshots=report["exclude_boundary_snapshots"],
    )
    held = [row for row in rows if row["project"] == report["heldout_project"]]
    returns = Counter(row["task"] for row in held if row["label"] == "return")
    tools = Counter(row["task"] for row in held if row["label"] == "tool")
    scores = report["results"]
    if baseline not in scores or candidate not in scores:
        raise ValueError("both frozen model keys must be in the score report")
    if not returns:
        raise ValueError("held-out project has no natural child returns")
    for key in (baseline, candidate):
        result = scores[key]
        if (
            result["heldout_return_rounds"] != sum(returns.values())
            or result["heldout_tool_rounds"] != sum(tools.values())
        ):
            raise ValueError(f"held-out denominators do not match: {key}")

    hit_field = (
        "live_window_hits_by_workflow"
        if service_live else "heldout_window_hits_by_workflow"
    )
    count_field = (
        "window_hits_with_recent_decode"
        if service_live else "lead_between_500_and_2000ms"
    )

    def hits(key: str) -> Counter:
        result = scores[key]
        source = (
            result["offline_service_audit"] if service_live else result
        )
        values = Counter(source[hit_field])
        if (
            sum(values.values()) != source[count_field]
            or set(values) - set(returns)
            or any(values[task] > returns[task] for task in values)
        ):
            raise ValueError(f"first-trigger workflow counts do not match: {key}")
        return values

    baseline_hits = hits(baseline)
    candidate_hits = hits(candidate)
    groups = sorted(returns)
    denominators = np.asarray([returns[task] for task in groups])
    differences = np.asarray([
        candidate_hits[task] - baseline_hits[task] for task in groups
    ])
    observed = float(differences.sum() / denominators.sum())
    if len(groups) >= 5:
        rng = np.random.default_rng(42)
        selected = rng.integers(0, len(groups), size=(draws, len(groups)))
        gains = differences[selected].sum(axis=1) / denominators[selected].sum(axis=1)
        ci95 = [float(np.quantile(gains, p)) for p in (.025, .975)]
    else:
        ci95 = None
    return {
        "scope": (
            "Paired first-trigger recall by held-out workflow. No threshold "
            "or representation is chosen here; repeated snapshots are not "
            "independent observations. An offline service-live score uses "
            "server completion only to audit the already-chosen trigger. "
            "This is not an online control or an H2D benefit estimate."
        ),
        "heldout_project": report["heldout_project"],
        "baseline": baseline,
        "candidate": candidate,
        "service_live": service_live,
        "workflow_count": len(groups),
        "natural_returns": int(denominators.sum()),
        "tool_rounds_with_content": sum(tools.values()),
        "baseline_window_hits": sum(baseline_hits.values()),
        "candidate_window_hits": sum(candidate_hits.values()),
        "absolute_recall_gain": observed,
        "workflow_bootstrap_95pct_ci": ci95,
        "workflow_bootstrap_draws": draws if ci95 is not None else 0,
        "paired_positive_95pct": ci95 is not None and ci95[0] > 0,
        "candidate_false_tool_first_triggers": (
            scores[candidate]["heldout_tool_false_first_triggers"]
        ),
        "baseline_false_tool_first_triggers": (
            scores[baseline]["heldout_tool_false_first_triggers"]
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--workflows", type=Path, action="append", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--service-live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    result = compare(
        report, args.workflows, args.baseline, args.candidate,
        service_live=args.service_live,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
