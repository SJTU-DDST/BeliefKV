#!/usr/bin/env python3
"""Project-isolated, causal screen for a short child RETURN transfer window.

This is an offline development probe. A positive label means a natural RETURN
is 500-3000 ms away at the snapshot; it does not authorize physical prefetch.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from statistics import median

import numpy as np

if __package__:
    from scripts.pilot_child_terminal_threshold import fit_head
    from scripts.pilot_hidden_eta_project_split import features, fit_ridge
    from scripts.pilot_real_child_hidden_eta import (
        at_stage, content_cues, first_features, load_batch_records,
    )
else:
    from pilot_child_terminal_threshold import fit_head
    from pilot_hidden_eta_project_split import features, fit_ridge
    from pilot_real_child_hidden_eta import (
        at_stage, content_cues, first_features, load_batch_records,
    )


STAGE_TOKENS = 512
TARGET_START_MS = 500
TARGET_END_MS = 3000
FAR_NEGATIVE_MS = 5000
THRESHOLDS = tuple(round(i / 100, 2) for i in range(50, 96, 5))


def score(model: tuple, samples: list[tuple]) -> np.ndarray:
    mean, scale, weights, intercept = model
    return np.clip(
        (features(samples, True) - mean) / scale @ weights + intercept,
        0, 1,
    )


def fit_window(records: list[dict]) -> tuple:
    positive = []
    negative_nonterminal = []
    negative_timing = []
    for record in records:
        samples = record["samples"]
        # Sampling depends only on the current ordinal, not the future length.
        for index, sample in enumerate(samples):
            if index >= 8 and index % 4:
                continue
            lead = sample[2]
            if record["terminal"] and TARGET_START_MS <= lead <= TARGET_END_MS:
                positive.append(sample)
            elif not record["terminal"]:
                negative_nonterminal.append(sample)
            elif lead < TARGET_START_MS or lead > FAR_NEGATIVE_MS:
                negative_timing.append(sample)
    if len(positive) < 10 or not negative_nonterminal or not negative_timing:
        raise ValueError("too few causal window labels across terminal/nonterminal rounds")
    random.Random(61).shuffle(negative_nonterminal)
    random.Random(67).shuffle(negative_timing)
    # Keep both wrong-round and wrong-time negatives in every fit.
    limit = 3 * len(positive)
    negatives = (
        negative_nonterminal[:limit] + negative_timing[:limit]
    )
    examples = positive + negatives
    random.Random(71).shuffle(examples)
    labels = np.asarray(
        [float(TARGET_START_MS <= sample[2] <= TARGET_END_MS)
         if sample[2] is not None else 0. for sample in examples],
    )
    return fit_ridge(features(examples, True), labels)


def scored_sequences(
    records: list[dict], terminal_head: tuple, window_head: tuple,
    cues: dict,
) -> list[dict]:
    if not records:
        return []
    mean, scale, weights, intercept = terminal_head
    stage_scores = np.clip(
        (first_features(records, True) - mean) / scale @ weights + intercept,
        0, 1,
    )
    sequences = []
    for record, stage_score in zip(records, stage_scores):
        if stage_score < 0.5:
            continue
        cue = cues.get(record["rid"], {})
        content = cue.get("content")
        if content is None:
            continue
        tool = cue.get("tool", float("inf"))
        observations = []
        scores = score(window_head, record["samples"])
        for sample, value in zip(record["samples"], scores):
            sample_ts = record["first_arrival_ms"] + sample[1]
            if sample_ts < content:
                continue
            if tool <= sample_ts:
                break
            observations.append((sample_ts, float(value)))
        if observations:
            sequences.append({
                "project": record["project"],
                "rid": record["rid"],
                "terminal": record["terminal"],
                "return_ms": record["return_ms"],
                "join_last": record.get("join_last", False),
                "observations": observations,
            })
    return sequences


def report(
    sequences: list[dict], threshold: float, confirmation_samples: int = 1,
) -> dict:
    if confirmation_samples < 1:
        raise ValueError("confirmation_samples must be positive")
    leads = []
    join_leads = []
    false_rounds = 0
    projects = set()
    for sequence in sequences:
        trigger = next(
            (
                at for index, (at, value) in enumerate(sequence["observations"])
                if index + 1 >= confirmation_samples
                and value >= threshold
                and all(
                    previous >= threshold for _, previous in
                    sequence["observations"][
                        index + 1 - confirmation_samples:index
                    ]
                )
            ),
            None,
        )
        if trigger is None:
            continue
        projects.add(sequence["project"])
        if sequence["terminal"]:
            lead = sequence["return_ms"] - trigger
            leads.append(lead)
            if sequence["join_last"]:
                join_leads.append(lead)
        else:
            false_rounds += 1
    total = len(leads) + false_rounds
    in_window = sum(TARGET_START_MS <= x <= TARGET_END_MS for x in leads)
    return {
        "threshold": threshold,
        "confirmation_samples": confirmation_samples,
        "first_trigger_count": total,
        "projects_with_triggers": len(projects),
        "nonterminal_false_triggers": false_rounds,
        "natural_return_triggers": len(leads),
        "window_true_triggers": in_window,
        "over_3000ms": sum(x > TARGET_END_MS for x in leads),
        "under_500ms": sum(x < TARGET_START_MS for x in leads),
        "join_last_window_triggers": sum(
            TARGET_START_MS <= x <= TARGET_END_MS for x in join_leads
        ),
        "window_precision": round(in_window / total, 4) if total else None,
        "lead_p50_ms": round(median(leads), 2) if leads else None,
    }


def select_threshold(sequences: list[dict]) -> tuple[float, int] | None:
    eligible = []
    for samples in (1, 2, 3):
        for threshold in THRESHOLDS:
            result = report(sequences, threshold, samples)
            if (
                result["window_true_triggers"] >= 8
                and result["projects_with_triggers"] >= 2
                and result["window_precision"] is not None
                and result["window_precision"] >= 0.90
            ):
                eligible.append((result["window_true_triggers"], threshold, samples))
    if not eligible:
        return None
    _, threshold, samples = max(eligible, key=lambda row: (row[0], row[1], -row[2]))
    return threshold, samples


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-traces", type=Path, action="append", required=True)
    parser.add_argument("--heldout-traces", type=Path, required=True)
    parser.add_argument("--train-workflows", type=Path, action="append", required=True)
    parser.add_argument("--heldout-workflows", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len(args.train_traces) != len(args.train_workflows):
        parser.error("provide one --train-traces per --train-workflows, in the same order")
    train, train_counts = load_batch_records(
        args.train_workflows, args.train_traces,
    )
    heldout, heldout_counts = load_batch_records(
        args.heldout_workflows, args.heldout_traces,
    )
    train_projects = {row["project"] for row in train}
    heldout_projects = {row["project"] for row in heldout}
    if train_projects & heldout_projects or len(train_projects) < 3:
        parser.error("need at least three training projects and a disjoint heldout project")
    stage_train = at_stage(train, STAGE_TOKENS)
    train_cues = {}
    for root in args.train_workflows:
        train_cues.update(content_cues(root))
    folds = []
    for project in sorted(train_projects):
        fit = [row for row in stage_train if row["project"] != project]
        validation = [row for row in stage_train if row["project"] == project]
        folds.extend(scored_sequences(
            validation, fit_head(fit), fit_window(fit), train_cues,
        ))
    selected = select_threshold(folds)
    result = {
        "diagnostic_only": True,
        "train_projects": sorted(train_projects),
        "heldout_projects": sorted(heldout_projects),
        "train": train_counts,
        "heldout": heldout_counts,
        "stage_tokens": STAGE_TOKENS,
        "window_ms": [TARGET_START_MS, TARGET_END_MS],
        "threshold_chosen_on_train_project_cv": selected,
        "project_cv": {
            str(samples): {
                str(value): report(folds, value, samples) for value in THRESHOLDS
            } for samples in (1, 2, 3)
        },
        "heldout_at_frozen_threshold": None,
    }
    if selected is not None:
        heldout_cues = {}
        for root in args.heldout_workflows:
            heldout_cues.update(content_cues(root))
        sequences = scored_sequences(
            at_stage(heldout, STAGE_TOKENS),
            fit_head(stage_train), fit_window(stage_train),
            heldout_cues,
        )
        result["heldout_at_frozen_threshold"] = report(sequences, *selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
