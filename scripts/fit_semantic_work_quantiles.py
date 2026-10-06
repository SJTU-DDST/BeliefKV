#!/usr/bin/env python3
"""Fit log-work quantiles on a frozen phase representation, CPU only."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.predictor.child_semantic_work import (
    FrozenTextEncoder, SemanticHead, encoder_snapshot, workflow_weights,
)
from beliefkv.predictor.conditional_work import NeuralConditionalWork, structural_work_features
from scripts.compare_semantic_rolling_work import metrics
from scripts.fit_child_completion_windows import load_samples
from scripts.train_child_semantic_work import cached_embeddings, split_roles


def per_workflow_median(values, tasks) -> float:
    return float(np.median([
        np.median(values[np.asarray(tasks) == task]) for task in sorted(set(tasks))
    ]))


def interval_margin(predicted, actual, tasks, coverage) -> tuple[float, dict]:
    scores = np.maximum.reduce((
        np.zeros(len(actual)), predicted[:, 0] - actual,
        np.where(actual > 0, actual - predicted[:, 2], 0.),
    ))
    grouped = sorted(float(max(scores[np.asarray(tasks) == task])) for task in set(tasks))
    rank = math.ceil((len(grouped) + 1) * coverage)
    if not 0 < rank <= len(grouped):
        raise ValueError("insufficient independent workflows for finite work bounds")
    return grouped[rank - 1], {"workflow_count": len(grouped), "rank": rank}


def comparison_rows(samples, predicted):
    return [{
        "request_id": row["observation"].request_id,
        "snapshot_ts_ms": row["observation"].ts_ms,
        "actual_remaining_tokens": row["remaining_tokens"],
        "candidate": float(value),
    } for row, value in zip(samples, predicted)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--phase-artifact", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=160)
    parser.add_argument("--work-features", choices=("structural_v1", "body_progress_v2"), default="structural_v1")
    parser.add_argument("--late-weight", type=float, default=1.)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    if args.epochs <= 0:
        parser.error("epochs must be positive")
    if not math.isfinite(args.late_weight) or args.late_weight < 1.:
        parser.error("late weight must be finite and at least one")
    torch.set_num_threads(2)
    args.cache.mkdir(parents=True, exist_ok=True)
    plan = json.loads(args.plan.read_text())
    artifact = json.loads(args.phase_artifact.read_text())
    phase = SemanticHead.load(args.phase_artifact)
    sha = artifact["metadata"]["adapted_encoder"]["weights_sha256"]
    if sha != plan["encoder"]["revision"]:
        raise ValueError("work fit requires the original frozen encoder")
    samples, exclusions = [], {}
    for run in plan["training_runs"] + plan["calibration_evaluation_runs"]:
        print(f"Collecting {run}", flush=True)
        rows, exclusions[run] = load_samples(ROOT / run, args.cache, plan["snapshot_policy"])
        # This head is conditional on a natural final report; phase stays frozen.
        samples.extend(row for row in rows if row["remaining_tokens"] is not None)
    roles = split_roles(samples, plan)
    observations = [row["observation"] for row in samples]
    encoder = FrozenTextEncoder(encoder_snapshot(artifact, args.phase_artifact), max_tokens=256)
    embeddings = cached_embeddings(samples, encoder, sha, args.cache)
    raw = np.column_stack((
        phase.design(observations, embeddings),
        structural_work_features(observations, version=args.work_features),
    ))
    train, selector, interval, held = (
        roles[name] for name in ("training", "selector", "interval", "evaluation")
    )
    center, scale = raw[train].mean(axis=0), np.maximum(raw[train].std(axis=0), .1)
    x = torch.tensor((raw - center) / scale, dtype=torch.float32)
    actual = np.log1p([row["remaining_tokens"] for row in samples])
    y = torch.tensor(actual, dtype=torch.float32)
    tasks = [row["task"] for row in samples]
    training_tasks = np.asarray([tasks[i] for i in train])
    weights = np.asarray([
        args.late_weight if samples[i]["remaining_tokens"] <= 64 else 1.
        for i in train
    ])
    for task in set(training_tasks):
        chosen = training_tasks == task
        weights[chosen] /= weights[chosen].sum()
    weight = torch.tensor(weights / weights.sum(), dtype=torch.float32)
    selector_weights = workflow_weights([tasks[i] for i in selector])
    levels = torch.tensor((.1, .5, .9), dtype=torch.float32)
    candidates = []
    for decay in (.01, .1):
        torch.manual_seed(21)
        model = nn.Sequential(
            nn.Linear(x.shape[1], 64), nn.GELU(),
            nn.Linear(64, 32), nn.GELU(), nn.Linear(32, 3),
        )
        optimizer = torch.optim.AdamW(model.parameters(), lr=.006, weight_decay=decay)
        print(f"Fitting log-work quantiles, weight_decay={decay}", flush=True)
        for _ in range(args.epochs):
            prediction = torch.sort(model(x[train]), dim=1).values
            residual = y[train, None] - prediction
            loss = (torch.maximum(
                levels * residual, (levels - 1.) * residual,
            ).mean(dim=1) * weight).sum()
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.)
            optimizer.step()
        with torch.inference_mode():
            predicted = torch.sort(model(x), dim=1).values.numpy()
        bias = per_workflow_median(
            actual[selector] - predicted[selector, 1], [tasks[i] for i in selector],
        )
        biased = predicted + bias
        residual = actual[selector, None] - biased[selector]
        score = float(selector_weights @ np.maximum(
            np.asarray((.1, .5, .9)) * residual,
            (np.asarray((.1, .5, .9)) - 1.) * residual,
        ).mean(axis=1))
        candidates.append((score, model, biased, {
            "weight_decay": decay, "log1p_bias": bias,
            "selector_workflow_weighted_log_pinball": score,
        }))
    _, model, biased, selected = min(candidates, key=lambda value: value[0])
    margin, calibration = interval_margin(
        biased[interval], actual[interval], [tasks[i] for i in interval],
        plan["nominal_workflow_interval_coverage"],
    )
    state = model.state_dict()
    work = {
        "kind": "neural_conditional_work", "schema_version": 2,
        "target": "remaining", "output_space": "ordered_log1p_quantiles",
        "feature_version": args.work_features,
        "encoder_weights_sha256": sha,
        "center": center.tolist(), "scale": scale.tolist(),
        "layers": [{
            "weight": state[f"{i}.weight"].tolist(), "bias": state[f"{i}.bias"].tolist(),
        } for i in (0, 2, 4)],
        "log1p_bias": selected["log1p_bias"], "interval_margin_log1p": margin,
        "scope": "Intrinsic remaining work only; no physical action authorization.",
    }
    runtime = NeuralConditionalWork(work)
    bounds = runtime.arrays(observations, embeddings, phase)
    expected = np.expm1(np.clip(
        biased + np.asarray((-margin, 0., margin)), 0., 16.,
    ))
    export_error = float(np.max(np.abs(bounds - expected)))
    if not np.allclose(bounds, expected, atol=.05, rtol=1e-4):
        raise ValueError(f"exported inference differs from fitted head: {export_error}")
    baseline_path = args.phase_artifact.parent / artifact["conditional_work_head"]["path"]
    baseline = SemanticHead.load(baseline_path)
    _, old = baseline.arrays([observations[i] for i in held], embeddings[held])
    rows = comparison_rows([samples[i] for i in held], bounds[held, 1])
    for row, before in zip(rows, old):
        row["baseline"] = float(before[1])
    args.output.mkdir(parents=True)
    work_path = args.output / "work_quantiles.json"
    work_path.write_text(json.dumps(work, indent=2, allow_nan=False) + "\n")
    composite = copy.deepcopy(artifact)
    composite["metadata"]["adapted_encoder"]["snapshot"] = str(
        encoder_snapshot(artifact, args.phase_artifact),
    )
    composite["conditional_work_head"] = {
        "path": work_path.name, "sha256": hashlib.sha256(work_path.read_bytes()).hexdigest(),
    }
    (args.output / "semantic_event_calibrated.json").write_text(
        json.dumps(composite, indent=2, allow_nan=False) + "\n",
    )
    cal = {
        **calibration, "nominal_workflow_coverage": plan["nominal_workflow_interval_coverage"],
        "added_log1p_margin": margin,
        "assumption": "Repeated snapshots clustered by workflow; project exchangeability unverified.",
    }
    report = {
        "scope": "CPU development conditional-work refit, not sealed validation or H2D benefit",
        "plan": plan, "phase_and_encoder_frozen": True,
        "work_features": args.work_features,
        "training_late_weight": args.late_weight,
        "weighted_output_semantics": (
            "Late labels are training-loss weights only, never online features. "
            "Reweighted nominal quantile regression is not the unweighted RETURN "
            "distribution; report separately calibrated bounds and point errors."
        ),
        "phase_artifact_sha256": hashlib.sha256(args.phase_artifact.read_bytes()).hexdigest(),
        "encoder_weights_sha256": sha, "selected": selected,
        "selection_basis": "Astropy selector only; no Sphinx labels or action benefit",
        "candidates": [candidate[3] for candidate in candidates],
        "work_bounds": cal, "export_max_absolute_token_difference": export_error,
        "role_snapshot_counts": {role: len(indices) for role, indices in roles.items()},
        "role_workflow_counts": {
            role: len({tasks[i] for i in indices}) for role, indices in roles.items()
        },
        "excluded_intervened_tasks_by_run": exclusions,
        "evaluation_last_snapshot": {name: metrics(rows, name) for name in ("baseline", "candidate")},
        "evaluation_near_end": {
            str(limit): {name: metrics([
                row for row in rows if row["actual_remaining_tokens"] <= limit
            ], name) for name in ("baseline", "candidate")} for limit in (32, 64, 128)
        },
        "calibration": copy.deepcopy(json.loads(
            (args.phase_artifact.parent / "report.json").read_text(),
        )["calibration"]),
    }
    report["calibration"]["semantic_event"]["work_bounds"] = cal
    (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({key: report[key] for key in (
        "selected", "work_bounds", "evaluation_last_snapshot", "evaluation_near_end",
    )}, indent=2))


if __name__ == "__main__":
    main()
