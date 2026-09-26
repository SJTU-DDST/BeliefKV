#!/usr/bin/env python3
"""Train-project-only nonlinear screen for an observable child RETURN window.

This read-only diagnostic never grants eligibility for physical migration.
The hidden-state shadow NPZ is written at completion, so this tests what a
future live delivery hook might expose, not the latency of such a hook.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import lightgbm as lgb
import numpy as np

if __package__:
    from scripts.pilot_child_return_window import (
        STAGE_TOKENS, THRESHOLDS, TARGET_END_MS, TARGET_START_MS,
        causal_observations, report, select_threshold,
    )
    from scripts.pilot_child_terminal_threshold import fit_head
    from scripts.pilot_real_child_hidden_eta import (
        at_stage, content_cues, first_features, load_batch_records,
    )
else:
    from pilot_child_return_window import (
        STAGE_TOKENS, THRESHOLDS, TARGET_END_MS, TARGET_START_MS,
        causal_observations, report, select_threshold,
    )
    from pilot_child_terminal_threshold import fit_head
    from pilot_real_child_hidden_eta import (
        at_stage, content_cues, first_features, load_batch_records,
    )


def observable_features(record: dict, cues: dict, *, hidden: bool) -> list[tuple]:
    content_ts = cues.get(record["rid"], {}).get("content")
    if content_ts is None:
        return []
    observations = causal_observations(record, cues)
    examples = []
    previous = None
    for index, (available_ts, sample) in enumerate(observations):
        if index >= 8 and index % 4:
            continue
        ordinal, elapsed, lead, vector = sample
        original_ts = record["first_arrival_ms"] + elapsed
        interval_ms = (
            available_ts - previous[0] if previous is not None else 0.
        )
        token_delta = (
            ordinal - previous[1] if previous is not None else 0
        )
        columns = [
            math.log1p(ordinal * 32),
            math.log1p(elapsed),
            math.log1p(max(0., available_ts - content_ts)),
            math.log1p(max(0., available_ts - original_ts)),
            math.log1p(max(0., interval_ms)),
            math.log1p(max(0., token_delta)),
            math.log1p(ordinal * 32000 / max(elapsed, 1.)),
        ]
        if hidden:
            columns.extend(vector / math.sqrt(len(vector)))
        examples.append((available_ts, np.asarray(columns, dtype=np.float32), lead))
        previous = (available_ts, ordinal)
    return examples


def fit_window(records: list[dict], cues: dict, *, hidden: bool):
    examples = []
    for record in records:
        eligible = observable_features(record, cues, hidden=hidden)
        if not eligible:
            continue
        positives = sum(
            record["terminal"] and lead is not None
            and TARGET_START_MS <= lead <= TARGET_END_MS
            for _, _, lead in eligible
        )
        negatives = len(eligible) - positives
        for _, columns, lead in eligible:
            label = float(
                record["terminal"] and lead is not None
                and TARGET_START_MS <= lead <= TARGET_END_MS
            )
            # Keep an entire long episode from dominating the fit.
            within_episode = positives if label else negatives
            examples.append((columns, label, 1. / max(within_episode, 1)))
    positive = sum(label for _, label, _ in examples)
    negative = len(examples) - positive
    if positive < 10 or negative < 10:
        raise ValueError("insufficient observable positive/negative window labels")
    weights = np.asarray([item[2] for item in examples], dtype=np.float32)
    labels = np.asarray([item[1] for item in examples], dtype=np.float32)
    for target in (0., 1.):
        mask = labels == target
        weights[mask] *= .5 / weights[mask].sum() * len(examples)
    model = lgb.train(
        {
            "objective": "binary", "learning_rate": .04, "num_leaves": 4,
            "min_data_in_leaf": 12, "lambda_l2": 12, "max_bin": 63,
            "seed": 29, "num_threads": 4, "verbosity": -1,
        },
        lgb.Dataset(
            np.stack([item[0] for item in examples]),
            label=labels, weight=weights,
        ),
        num_boost_round=64,
    )
    return model


def scored_sequences(records: list[dict], terminal_head, model, cues: dict,
                     *, hidden: bool) -> list[dict]:
    if not records:
        return []
    mean, scale, weights, intercept = terminal_head
    stage_scores = np.clip(
        (first_features(records, True) - mean) / scale @ weights + intercept,
        0, 1,
    )
    sequences = []
    for record, stage_score in zip(records, stage_scores):
        if stage_score < .5:
            continue
        examples = observable_features(record, cues, hidden=hidden)
        if not examples:
            continue
        scores = model.predict(
            np.stack([columns for _, columns, _ in examples]),
            num_threads=4,
        )
        sequences.append({
            "project": record["project"],
            "rid": record["rid"],
            "terminal": record["terminal"],
            "return_ms": record["return_ms"],
            "join_last": record.get("join_last", False),
            "observations": [
                (available_ts, float(value))
                for (available_ts, _, _), value in zip(examples, scores)
            ],
        })
    return sequences


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-traces", type=Path, required=True)
    parser.add_argument("--train-workflows", type=Path, required=True)
    parser.add_argument("--heldout-traces", type=Path, required=True)
    parser.add_argument("--heldout-workflows", type=Path, required=True)
    parser.add_argument("--stage-tokens", type=int, default=STAGE_TOKENS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.stage_tokens < 32 or args.stage_tokens % 32:
        parser.error("stage-tokens must be a positive multiple of 32")
    train, train_counts = load_batch_records(
        [args.train_workflows], args.train_traces,
    )
    heldout, heldout_counts = load_batch_records(
        [args.heldout_workflows], args.heldout_traces,
    )
    projects = {row["project"] for row in train}
    heldout_projects = {row["project"] for row in heldout}
    if len(projects) < 3 or projects & heldout_projects:
        parser.error("need three training projects and a disjoint heldout project")
    stage = at_stage(train, args.stage_tokens)
    cues = content_cues(args.train_workflows)
    result = {
        "diagnostic_only": True,
        "stage_tokens": args.stage_tokens,
        "train": train_counts,
        "heldout": heldout_counts,
        "train_projects": sorted(projects),
        "heldout_projects": sorted(heldout_projects),
        "heldout_at_frozen_threshold": {},
        "families": {},
    }
    for hidden in (False, True):
        name = "progress" if not hidden else "progress_hidden"
        folds = []
        for project in sorted(projects):
            fit = [row for row in stage if row["project"] != project]
            validation = [row for row in stage if row["project"] == project]
            folds.extend(scored_sequences(
                validation, fit_head(fit), fit_window(fit, cues, hidden=hidden),
                cues, hidden=hidden,
            ))
        choice = select_threshold(folds)
        result["families"][name] = {
            "threshold_chosen_on_train_project_cv": choice,
            "train_project_cv": {
                str(repeats): {
                    str(threshold): report(folds, threshold, repeats)
                    for threshold in THRESHOLDS
                } for repeats in (1, 2, 3)
            },
        }
        if choice is not None:
            target = scored_sequences(
                at_stage(heldout, args.stage_tokens),
                fit_head(stage), fit_window(stage, cues, hidden=hidden),
                content_cues(args.heldout_workflows), hidden=hidden,
            )
            result["heldout_at_frozen_threshold"][name] = report(target, *choice)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "stage_tokens": args.stage_tokens,
        "families": {
            name: payload["threshold_chosen_on_train_project_cv"]
            for name, payload in result["families"].items()
        },
        "heldout_at_frozen_threshold": result["heldout_at_frozen_threshold"],
    }, indent=2))


if __name__ == "__main__":
    main()
