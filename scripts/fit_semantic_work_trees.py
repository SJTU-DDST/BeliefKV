#!/usr/bin/env python3
"""Nonlinear conditional work fit on frozen semantic features, CPU only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import lightgbm as lgb
import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beliefkv.predictor.child_semantic_work import FrozenTextEncoder, SemanticHead, workflow_weights
from scripts.train_child_semantic_work import cached_samples, cached_embeddings, split_roles
from scripts.fit_semantic_work_neural import structural_features, per_request_rows
from scripts.compare_semantic_rolling_work import metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--phase-artifact", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exclude-workflows-from", type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(2)
    plan = json.loads(args.plan.read_text())
    raw = json.loads(args.phase_artifact.read_text())
    phase = SemanticHead.load(args.phase_artifact)
    baseline = SemanticHead.load(args.phase_artifact.parent / raw["conditional_work_head"]["path"])
    samples = []
    for run in plan["training_runs"] + plan["calibration_evaluation_runs"]:
        rows, _ = cached_samples(ROOT / run, args.cache, snapshot_policy=plan["snapshot_policy"])
        samples.extend(r for r in rows if r["remaining_tokens"] is not None)
    excluded = set(json.loads(args.exclude_workflows_from.read_text())["excluded_intervened_tasks"])
    samples = [r for r in samples if r["task"] not in excluded]
    roles = split_roles(samples, plan)
    observations = [r["observation"] for r in samples]
    encoder = FrozenTextEncoder(raw["metadata"]["adapted_encoder"]["snapshot"])
    embeddings = cached_embeddings(
        samples, encoder, raw["metadata"]["adapted_encoder"]["weights_sha256"], args.cache,
    )
    x = np.column_stack((phase.design(observations, embeddings), structural_features(observations)))
    train, selector, held = (roles[k] for k in ("training", "selector", "evaluation"))
    work = np.asarray([r["remaining_tokens"] for r in samples])
    progress = np.asarray([r.observed_output_tokens for r in observations])
    weights = workflow_weights([samples[i]["task"] for i in train]) * len(train)
    candidates = []
    for target in ("remaining", "total"):
        for leaves in (7, 15, 31):
            labels = work if target == "remaining" else work + progress
            model = lgb.train(
                {"objective": "regression_l1", "num_leaves": leaves,
                 "min_data_in_leaf": 30, "lambda_l2": 10.,
                 "num_threads": 2, "verbosity": -1, "seed": 21,
                 "deterministic": True, "force_col_wise": True},
                lgb.Dataset(x[train], label=np.log1p(labels[train]), weight=weights),
                num_boost_round=300,
            )
            predicted = np.expm1(np.clip(model.predict(x), 0, 16))
            if target == "total":
                predicted -= progress
            bias = float(np.median(work[selector] - predicted[selector]))
            predicted = np.maximum(0., predicted + bias)
            score = metrics(
                per_request_rows([samples[i] for i in selector], predicted[selector]), "candidate",
            )
            candidates.append((score["median_absolute_error_tokens"], model, predicted, {
                "target": target, "leaves": leaves, "bias": bias, "selector": score,
            }))
    _, model, predicted, selected = min(candidates, key=lambda r: r[0])
    _, old = baseline.arrays([observations[i] for i in held], embeddings[held])
    rows = per_request_rows([samples[i] for i in held], predicted[held])
    for row, value in zip(rows, old):
        row["baseline"] = float(value[1])
    result = {
        "scope": "CPU-only development comparison, not deployed or sealed validation",
        "phase_and_encoder_frozen": True,
        "excluded_intervened_tasks": sorted(excluded),
        "selection": "Astropy selector only",
        "selected": selected,
        "held_last_snapshot": {name: metrics(rows, name) for name in ("baseline", "candidate")},
        "held_near_end": {
            str(limit): {name: metrics([
                r for r in rows if r["actual_remaining_tokens"] <= limit
            ], name) for name in ("baseline", "candidate")}
            for limit in (32, 64, 128)
        },
        "candidates": [r[3] for r in candidates],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    model.save_model(str(args.output / "work_candidate.txt"))
    (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("selected", "held_last_snapshot", "held_near_end")}, indent=2))


if __name__ == "__main__":
    main()
