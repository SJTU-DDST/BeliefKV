#!/usr/bin/env python3
"""Probe a work-window score on frozen semantic inputs, without deploying it."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.predictor.child_semantic_work import SemanticReportPredictor
from beliefkv.predictor.conditional_work import structural_work_features
from scripts.compare_semantic_rolling_work import work_arrays
from scripts.fit_semantic_work_quantiles import cached_work_samples
from scripts.train_child_semantic_work import cached_embeddings, split_roles


def request_weights(samples):
    counts = Counter(row["observation"].request_id for row in samples)
    requests = defaultdict(set)
    for row in samples:
        requests[row["task"]].add(row["observation"].request_id)
    weights = np.asarray([
        1. / (counts[row["observation"].request_id] * len(requests[row["task"]]))
        for row in samples
    ])
    return weights * len(weights) / weights.sum()


def first_crossings(samples, eligible, values, threshold, horizon):
    if not len(samples) == len(eligible) == len(values):
        raise ValueError("sample, eligibility and score populations differ")
    grouped = defaultdict(list)
    for index, row in enumerate(samples):
        grouped[row["observation"].request_id].append(index)
    selected = []
    possible = 0
    for rid, indices in grouped.items():
        possible += any(
            eligible[i] and samples[i]["remaining_tokens"] <= horizon for i in indices
        )
        for i in sorted(indices, key=lambda i: samples[i]["observation"].ts_ms):
            if eligible[i] and values[i] >= threshold:
                row, obs = samples[i], samples[i]["observation"]
                selected.append({
                    "request_id": rid, "snapshot_ts_ms": obs.ts_ms,
                    "actual_remaining_tokens": row["remaining_tokens"],
                    "lead_to_return_ms": row["remaining_client_wall_ms"],
                    "score": float(values[i]),
                })
                break
    leads = [row["lead_to_return_ms"] for row in selected]
    return {
        "requests": len(grouped), "eligible_near_window_requests": int(possible),
        "first_trigger_count": len(selected),
        "within_token_window_count": sum(
            row["actual_remaining_tokens"] <= horizon for row in selected
        ),
        "premature_token_window_count": sum(
            row["actual_remaining_tokens"] > horizon for row in selected
        ),
        "lead_0_to_1000ms_count": sum(0 < value <= 1000 for value in leads),
        "lead_over_2000ms_count": sum(value > 2000 for value in leads),
        "median_lead_to_return_ms": float(np.median(leads)) if leads else None,
        "rows": selected,
    }


def selection_rank(report, coverage_floor):
    count = report["first_trigger_count"]
    return (
        count < coverage_floor,
        -count if count < coverage_floor else report["premature_token_window_count"] / max(1, count),
        -report["within_token_window_count"],
        -count,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--phase-artifact", type=Path, required=True)
    parser.add_argument("--sample-cache-manifest", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--horizon-tokens", type=int, default=32)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.horizon_tokens <= 0:
        parser.error("a new output directory and positive token horizon are required")
    torch.set_num_threads(1)
    plan = json.loads(args.plan.read_text())
    baseline = SemanticReportPredictor.load(args.phase_artifact)
    phase_report = json.loads((args.phase_artifact.parent / "report.json").read_text())
    threshold = phase_report["calibration"]["semantic_event"]["request_operating_point"]["threshold"]
    samples, exclusions, manifest = cached_work_samples(args.sample_cache_manifest, plan)
    roles = split_roles(samples, plan)
    obs = [row["observation"] for row in samples]
    artifact = json.loads(args.phase_artifact.read_text())
    revision = artifact["metadata"]["adapted_encoder"]["weights_sha256"]
    if revision != plan["encoder"]["revision"] or baseline.encoder is None:
        raise ValueError("work-window fit requires the frozen encoder")
    args.cache.mkdir(parents=True, exist_ok=True)
    embeddings = cached_embeddings(samples, baseline.encoder, revision, args.cache)
    phase, work = work_arrays(baseline, obs, embeddings)
    old_scores = np.asarray([row["online_forecast"]["score"] for row in samples])
    old_work = np.asarray([row["online_forecast"]["remaining_tokens"] for row in samples])
    if (
        not np.allclose(phase[:, 2], old_scores, atol=1e-5, rtol=1e-4)
        or not np.allclose(work[:, 1], old_work, atol=.1, rtol=1e-3)
    ):
        raise ValueError("reconstructed frozen predictions differ from delivered inputs")
    eligible = np.asarray([
        item.notice_active and item.observed_output_tokens >= 16
        and score >= threshold for item, score in zip(obs, phase[:, 2])
    ])
    raw = np.column_stack((
        baseline.head.design(obs, embeddings),
        structural_work_features(obs, version="body_progress_v2"),
    ))
    train, selector, held = (roles[name] for name in ("training", "selector", "evaluation"))
    train = train[eligible[train]]
    if not len(train):
        raise ValueError("no phase-eligible training inputs")
    center, scale = raw[train].mean(axis=0), np.maximum(raw[train].std(axis=0), .1)
    features = (raw - center) / scale
    labels = np.asarray([row["remaining_tokens"] <= args.horizon_tokens for row in samples])
    if len(set(labels[train])) != 2:
        raise ValueError("both work-window classes are required")
    baseline_reports = {}
    for role in ("training", "selector", "interval", "evaluation"):
        indices = roles[role]
        baseline_reports[role] = first_crossings(
            [samples[i] for i in indices], eligible[indices],
            -work[indices, 1], -args.horizon_tokens, args.horizon_tokens,
        )
    floor = baseline_reports["selector"]["first_trigger_count"]
    candidates = []
    train_x = torch.tensor(features[train], dtype=torch.float32)
    train_y = torch.tensor(labels[train], dtype=torch.float32)
    train_weights = request_weights([samples[i] for i in train])
    train_weights = torch.tensor(train_weights / train_weights.sum(), dtype=torch.float32)
    criterion = nn.BCEWithLogitsLoss(reduction="none")
    for regularization in (.0001, .001, .01):
        model = nn.Linear(features.shape[1], 1)
        nn.init.zeros_(model.weight)
        nn.init.zeros_(model.bias)
        optimizer = torch.optim.LBFGS(
            model.parameters(), max_iter=200, history_size=20,
            line_search_fn="strong_wolfe",
        )

        def closure():
            optimizer.zero_grad()
            logits = model(train_x).squeeze(1)
            loss = (
                (criterion(logits, train_y) * train_weights).sum()
                + regularization * model.weight.square().mean()
            )
            loss.backward()
            return loss

        optimizer.step(closure)
        weights = model.weight.detach().numpy()[0].astype(float)
        intercept = float(model.bias.detach()[0])
        scores = 1. / (1. + np.exp(-np.clip(features @ weights + intercept, -700, 700)))
        for cutoff in (0., .05, .1, .2, .3, .4, .5, .6, .7, .8, .9, .95, .99):
            report = first_crossings(
                [samples[i] for i in selector], eligible[selector], scores[selector],
                cutoff, args.horizon_tokens,
            )
            candidates.append((selection_rank(report, floor), model, scores, {
                "l2_penalty": regularization, "cutoff": cutoff,
                "selector": report,
            }))
        print(f"Fitted L2={regularization}", flush=True)
    _, model, scores, selected = min(candidates, key=lambda row: row[0])
    candidate_reports = {}
    for role in ("training", "selector", "interval", "evaluation"):
        indices = roles[role]
        candidate_reports[role] = first_crossings(
            [samples[i] for i in indices], eligible[indices], scores[indices],
            selected["cutoff"], args.horizon_tokens,
        )
    weights = model.weight.detach().numpy()[0].astype(float)
    intercept = float(model.bias.detach()[0])
    reproduced = 1. / (1. + np.exp(-np.clip(features @ weights + intercept, -700, 700)))
    if not np.allclose(reproduced, scores, atol=1e-10, rtol=1e-10):
        raise ValueError("exported score differs from fitted classifier")
    report = {
        "scope": (
            "Conditional natural-final-request CPU development probe, not deployed, "
            "an unseen-project test, nonfinal/tool-round false-alarm validation, or "
            "H2D/throughput benefit. Original phase and encoder remain frozen. "
            "Snapshot scores are not calibrated probabilities or exact runtime "
            "delivery-time triggers. Wall-clock leads are evaluation labels only."
        ),
        "plan": plan, "horizon_tokens": args.horizon_tokens,
        "phase_and_encoder_frozen": True,
        "phase_artifact_sha256": hashlib.sha256(args.phase_artifact.read_bytes()).hexdigest(),
        "sample_manifest": manifest, "excluded_intervened_tasks_by_run": exclusions,
        "selection_basis": (
            "Selector workflows only; preserve at least baseline selector first-trigger "
            "coverage, then minimize premature first-trigger fraction within the token "
            "window and maximize timely token-window triggers. Evaluation labels are "
            "not used for model/cutoff selection."
        ),
        "selector_coverage_floor": floor, "selected": selected,
        "role_request_counts": {
            role: len({samples[i]["observation"].request_id for i in indices})
            for role, indices in roles.items()
        },
        "baseline": baseline_reports, "candidate": candidate_reports,
        "candidates": [row[3] for row in candidates],
        "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    args.output.mkdir(parents=True)
    (args.output / "window_score.json").write_text(json.dumps({
        "kind": "experimental_conditional_work_window_score",
        "horizon_tokens": args.horizon_tokens, "phase_threshold": threshold,
        "center": center.tolist(), "scale": scale.tolist(),
        "weight": weights.tolist(), "intercept": intercept,
        "cutoff": selected["cutoff"], "feature_version": "body_progress_v2",
        "scope": "Offline probe only; no runtime loader or action authorization.",
    }, indent=2, allow_nan=False) + "\n")
    (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "output": str(args.output), "selected": selected,
        "evaluation": {name: report[name]["evaluation"] for name in ("baseline", "candidate")},
    }, indent=2))


if __name__ == "__main__":
    main()
