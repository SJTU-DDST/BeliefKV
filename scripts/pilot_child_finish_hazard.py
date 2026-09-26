#!/usr/bin/env python3
"""Explore short-horizon RETURN risk after the frozen terminal-intent gate."""

from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path

import numpy as np

if __package__:
    from scripts.pilot_hidden_eta_project_split import features, fit_ridge
    from scripts.pilot_real_child_hidden_eta import (
        at_stage, content_cues, first_features, load_records,
    )
else:
    from pilot_hidden_eta_project_split import features, fit_ridge
    from pilot_real_child_hidden_eta import (
        at_stage, content_cues, first_features, load_records,
    )


def fit_binary(records: list[tuple], hidden: bool) -> tuple:
    return fit_ridge(
        features(records, hidden),
        np.asarray([record[2] for record in records], dtype=np.float64),
    )


def score_binary(model: tuple, records: list[tuple], hidden: bool) -> np.ndarray:
    mean, scale, weights, intercept = model
    return np.clip(
        (features(records, hidden) - mean) / scale @ weights + intercept, 0, 1
    )


def fit_hazard(train: list[dict], hidden: bool) -> tuple[tuple, dict]:
    yes, no = [], []
    for record in train:
        if not record["terminal"]:
            continue
        for sample in record["samples"]:
            ordinal, elapsed, lead, vector = sample
            if 0 < lead <= 1000:
                yes.append((ordinal, elapsed, 1., vector))
            elif lead >= 2000:
                no.append((ordinal, elapsed, 0., vector))
    random.Random(43).shuffle(no)
    if len(yes) < 10:
        raise ValueError("insufficient naturally finished <=1s snapshots")
    examples = yes + no[: 5 * len(yes)]
    return fit_binary(examples, hidden), {
        "positive_near_finish_snapshots": len(yes),
        "negative_far_finish_snapshots": len(no),
        "training_negative_sampled": min(len(no), 5 * len(yes)),
    }


def evaluate(
    train: list[dict], heldout: list[dict], cues: dict, hidden: bool,
) -> dict:
    at512_train = at_stage(train, 512)
    positive = [row for row in at512_train if row["terminal"]]
    negative = [row for row in at512_train if not row["terminal"]]
    random.Random(17).shuffle(negative)
    selected = positive + negative[: 5 * len(positive)]
    random.Random(23).shuffle(selected)
    head = fit_ridge(
        first_features(selected, True),
        np.asarray([row["terminal"] for row in selected], dtype=np.float64),
    )
    mean, scale, weights, intercept = head
    hazard, counts = fit_hazard(train, hidden)
    candidate_count = 0
    triggered = []
    missed = 0
    for record in at_stage(heldout, 512):
        score = np.clip(
            (first_features([record], True) - mean) / scale @ weights
            + intercept, 0, 1,
        )[0]
        if score < 0.5:
            continue
        cue = cues.get(record["rid"], {})
        content = cue.get("content")
        if content is None:
            continue
        stage_abs = record["first_arrival_ms"] + record["samples"][0][1]
        gate_at = max(stage_abs, content)
        if cue.get("tool", float("inf")) <= gate_at:
            continue
        candidate_count += 1
        if not record["terminal"]:
            continue
        eligible = [
            row for row in record["samples"]
            if record["first_arrival_ms"] + row[1] >= gate_at
        ]
        if not eligible:
            missed += 1
            continue
        scores = score_binary(hazard, eligible, hidden)
        matches = np.flatnonzero(scores >= 0.5)
        if not len(matches):
            missed += 1
            continue
        triggered.append(float(eligible[matches[0]][2]))
    return {
        **counts,
        "frozen_gate_candidates": candidate_count,
        "terminal_hazard_triggered": len(triggered),
        "terminal_hazard_missed": missed,
        "trigger_500_to_1500ms": sum(500 <= lead <= 1500 for lead in triggered),
        "trigger_under_500ms": sum(lead < 500 for lead in triggered),
        "trigger_over_1500ms": sum(lead > 1500 for lead in triggered),
        "lead_p50_ms": round(statistics.median(triggered), 2) if triggered else None,
        "abs_eta_error_p50_ms": round(
            statistics.median(abs(lead - 1000) for lead in triggered), 2
        ) if triggered else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces", required=True, type=Path)
    parser.add_argument("--train-workflows", required=True, type=Path)
    parser.add_argument("--heldout-workflows", required=True, type=Path)
    args = parser.parse_args()
    train, train_counts = load_records(args.train_workflows, args.traces)
    heldout, heldout_counts = load_records(args.heldout_workflows, args.traces)
    if {row["project"] for row in train} & {row["project"] for row in heldout}:
        parser.error("projects overlap")
    cues = content_cues(args.heldout_workflows)
    print(json.dumps({
        "diagnostic_only": True,
        "hazard_label": "return_within_1000ms_after_snapshot",
        "train": train_counts,
        "heldout": heldout_counts,
        "progress_only": evaluate(train, heldout, cues, False),
        "hidden_plus_progress": evaluate(train, heldout, cues, True),
    }, indent=2))


if __name__ == "__main__":
    main()
