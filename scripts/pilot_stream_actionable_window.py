#!/usr/bin/env python3
"""Read-only classification of child RETURN with at least 2 seconds of lead."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pilot_stream_final_classifier import (
    MIN_PRECISION, MIN_SELECTED, _fit, _scores, samples,
)


MIN_USEFUL_LEAD_MS = 2000


def _annotated(rows: list[dict]) -> list[dict]:
    return [
        {
            **row,
            "final": row["final"]
            and row["return_lead_ms"] >= MIN_USEFUL_LEAD_MS,
        }
        for row in rows
    ]


def _quality(
    rows: list[dict], scores: np.ndarray, cutoff: float,
    last_children: set[tuple[str, str, str]],
) -> dict:
    chosen = [row for row, score in zip(rows, scores) if score >= cutoff]
    useful = [
        row for row in chosen if row["final"]
        and row["return_lead_ms"] >= MIN_USEFUL_LEAD_MS
    ]
    returns = sum(row["final"] for row in rows)
    potential = sum(
        row["final"] and row["return_lead_ms"] >= MIN_USEFUL_LEAD_MS
        for row in rows
    )
    raw_useful_last = sum(
        row["final"] and row["return_lead_ms"] >= MIN_USEFUL_LEAD_MS
        and (row["trace_path"], row["workflow"], row["child"])
        in last_children
        for row in rows
    )
    useful_last = sum(
        (row["trace_path"], row["workflow"], row["child"]) in last_children
        for row in useful
    )
    return {
        "children": len(rows),
        "returned_children": returns,
        "potential_useful_windows": potential,
        "raw_candidate_useful_precision": (
            potential / len(rows) if rows else None
        ),
        "raw_candidate_last_child_useful_recall": (
            raw_useful_last / len(last_children) if last_children else None
        ),
        "selected": len(chosen),
        "selected_returned": sum(row["final"] for row in chosen),
        "selected_useful": len(useful),
        "selected_late_return": sum(
            row["final"] and row["return_lead_ms"] < MIN_USEFUL_LEAD_MS
            for row in chosen
        ),
        "selected_nonreturn": sum(not row["final"] for row in chosen),
        "useful_precision": len(useful) / len(chosen) if chosen else None,
        "useful_recall": len(useful) / potential if potential else None,
        "eligible_last_children": len(last_children),
        "useful_last_children": useful_last,
        "last_child_useful_recall": (
            useful_last / len(last_children) if last_children else None
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, action="append", required=True)
    parser.add_argument("--evaluate-workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    development = []
    development_last = set()
    for directory in args.train_workflows:
        rows, _, last = samples(directory)
        development.extend(rows)
        development_last.update(last)
    heldout, censored, heldout_last = samples(args.evaluate_workflows)
    model = _fit(_annotated(development), join_aware=True)
    scores = _scores(development, model, join_aware=True)
    ordered = sorted(set(scores), reverse=True)
    acceptable = [
        cutoff for cutoff in ordered
        if (selected := scores >= cutoff).sum() >= MIN_SELECTED
        and np.mean([
            row["final"] and row["return_lead_ms"] >= MIN_USEFUL_LEAD_MS
            for row, flag in zip(development, selected) if flag
        ]) >= MIN_PRECISION
    ]
    if acceptable:
        cutoff = min(acceptable)
        report = {
            "status": "read_only_actionable_window_pilot",
            "cutoff": float(cutoff),
            "development": _quality(
                development, scores, cutoff, development_last
            ),
            "project_holdout": _quality(
                heldout, _scores(heldout, model, join_aware=True),
                cutoff, heldout_last,
            ),
        }
    else:
        report = {"status": "no_precision_qualified_development_cutoff"}
    report["min_useful_lead_ms"] = MIN_USEFUL_LEAD_MS
    report["censored_heldout"] = censored
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
