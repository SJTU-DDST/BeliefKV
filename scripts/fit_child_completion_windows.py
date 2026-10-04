#!/usr/bin/env python3
"""Work-only short-token CDF experiment on causal pre-EOS snapshots, CPU only."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

import lightgbm as lgb
import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import logsumexp, softmax
import torch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.predictor.child_semantic_work import FrozenTextEncoder, SemanticHead, workflow_weights
from beliefkv.predictor.conditional_work import structural_work_features
from scripts.summarize_semantic_h2d_ab import records
from scripts.train_child_semantic_work import cached_embeddings, cached_samples, split_roles

HORIZONS = (8, 16, 32, 64)
INTERVENTIONS = frozenset({
    "agent_graph_budget_finalization", "agent_guard_finalization_attempt",
    "agent_protocol_repair_attempt",
})


def load_samples(run: Path, cache: Path, policy: str) -> tuple[list[dict], list[str]]:
    excluded = {
        path.parent.name for path in (
            *run.glob("client_*/workflows/*/sandbox_audit.jsonl"),
            *run.glob("workloads/workflows/*/sandbox_audit.jsonl"),
        )
        if any(row.get("event") in INTERVENTIONS for row in records(path))
    }
    samples, _ = cached_samples(run, cache, snapshot_policy=policy)
    # cached_samples already requires unfinished generation and causally prior decode.
    return [
        row for row in samples if row["task"] not in excluded
        and (row["remaining_tokens"] is None or row["remaining_tokens"] > 0)
    ], sorted(excluded)


def window_metrics(
    samples: list[dict], scores: np.ndarray, horizon: int, threshold: float | None,
) -> dict:
    grouped = defaultdict(list)
    for row, score in zip(samples, scores):
        grouped[row["observation"].request_id].append((row, float(score)))
    natural = [
        group for group in grouped.values() if group[0][0]["remaining_tokens"] is not None
    ]
    reachable = sum(
        any(0 < row["remaining_tokens"] <= horizon for row, _ in group)
        for group in natural
    )
    selected = []
    if threshold is not None:
        for group in grouped.values():
            match = next((
                row for row, score in sorted(group, key=lambda pair: pair[0]["observation"].ts_ms)
                if score >= threshold
            ), None)
            if match is not None:
                selected.append(match)
    correct = [
        row for row in selected if row["remaining_tokens"] is not None
        and 0 < row["remaining_tokens"] <= horizon
    ]
    leads = [row["remaining_client_wall_ms"] for row in correct]
    return {
        "threshold": threshold, "request_count": len(grouped),
        "natural_request_count": len(natural),
        "reachable_natural_request_count": reachable,
        "first_trigger_count": len(selected), "within_token_horizon_count": len(correct),
        "too_early_final_trigger_count": sum(
            row["remaining_tokens"] is not None and row["remaining_tokens"] > horizon
            for row in selected
        ),
        "tool_round_false_trigger_count": sum(row["remaining_tokens"] is None for row in selected),
        "first_trigger_precision": len(correct) / len(selected) if selected else None,
        "natural_request_recall": len(correct) / len(natural) if natural else None,
        "reachable_request_recall": len(correct) / reachable if reachable else None,
        "median_snapshot_lead_to_return_ms": float(np.median(leads)) if leads else None,
        "snapshot_lead_100_to_500ms_count": sum(100 <= lead <= 500 for lead in leads),
        "snapshot_lead_500_to_2000ms_count": sum(500 < lead <= 2000 for lead in leads),
        "snapshot_lead_over_2000ms_count": sum(lead > 2000 for lead in leads),
    }


def select_threshold(samples, scores, horizon, *, precision=.8, minimum=5):
    best = None
    for threshold in sorted(set(scores[np.isfinite(scores)]), reverse=True):
        result = window_metrics(samples, scores, horizon, float(threshold))
        if (
            result["first_trigger_count"] >= minimum
            and result["first_trigger_precision"] >= precision
            and (best is None or result["within_token_horizon_count"] > best["within_token_horizon_count"])
        ):
            best = result
    return best


def grouped_nll(samples, labels, logits) -> float:
    weights = workflow_weights([row["task"] for row in samples])
    return float(weights @ (logsumexp(logits, axis=1) - logits[np.arange(len(labels)), labels]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--phase-artifact", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay-arm", action="append", type=Path, default=[])
    parser.add_argument("--snapshot-policy", choices=("rolling_250ms", "rolling_100ms"))
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    torch.set_num_threads(2)
    args.cache.mkdir(parents=True, exist_ok=True)
    plan = json.loads(args.plan.read_text())
    if args.snapshot_policy:
        plan["snapshot_policy"] = args.snapshot_policy
    artifact = json.loads(args.phase_artifact.read_text())
    phase = SemanticHead.load(args.phase_artifact)
    work = SemanticHead.load(
        args.phase_artifact.parent / artifact["conditional_work_head"]["path"],
    )
    sha = artifact["metadata"]["adapted_encoder"]["weights_sha256"]
    encoder = FrozenTextEncoder(
        artifact["metadata"]["adapted_encoder"]["snapshot"],
        max_tokens=plan["encoder"]["max_tokens"],
    )
    phase_threshold = json.loads(
        (args.phase_artifact.parent / "report.json").read_text()
    )["calibration"]["semantic_event"]["request_operating_point"]["threshold"]
    samples, excluded = [], {}
    for run_name in plan["training_runs"] + plan["calibration_evaluation_runs"]:
        run = ROOT / run_name
        print(f"Collecting {run_name}", flush=True)
        values, excluded[run_name] = load_samples(run, args.cache, plan["snapshot_policy"])
        samples.extend(values)
    roles = split_roles(samples, plan)
    identities = {
        role: {samples[i]["task"] for i in roles[role]}
        for role in ("training", "selector", "interval", "evaluation")
    }
    if any(identities[a] & identities[b] for a in identities for b in identities if a != b):
        raise ValueError("workflow roles overlap")
    observations = [row["observation"] for row in samples]
    print(f"Encoding {len(samples)} snapshots with {plan['snapshot_policy']}", flush=True)
    embeddings = cached_embeddings(samples, encoder, sha, args.cache)
    x = np.column_stack((
        phase.design(observations, embeddings), structural_work_features(observations),
    ))
    final = np.asarray([row["remaining_tokens"] is not None for row in samples])
    labels = np.asarray([
        np.searchsorted(HORIZONS, row["remaining_tokens"], side="left")
        if row["remaining_tokens"] is not None else len(HORIZONS) for row in samples
    ])
    train = roles["training"][final[roles["training"]]]
    interval = roles["interval"][final[roles["interval"]]]
    selector = roles["selector"][final[roles["selector"]]]
    if not len(interval) or not len(selector) or len(np.unique(labels[train])) < 2:
        raise ValueError("insufficient disjoint conditional completion labels")
    candidates = []
    for leaves in (7, 15):
        print(f"Fitting completion CDF with {leaves} leaves", flush=True)
        model = lgb.train(
            {"objective": "multiclass", "num_class": len(HORIZONS) + 1,
             "num_leaves": leaves, "min_data_in_leaf": 30, "lambda_l2": 10.,
             "learning_rate": .05, "num_threads": 2, "verbosity": -1, "seed": 21,
             "deterministic": True, "force_col_wise": True},
            lgb.Dataset(x[train], label=labels[train], weight=(
                workflow_weights([samples[i]["task"] for i in train]) * len(train)
            )),
            num_boost_round=200,
        )
        logits = model.predict(x, raw_score=True, num_threads=2)
        temperature = float(minimize_scalar(
            lambda value: grouped_nll(
                [samples[i] for i in interval], labels[interval], logits[interval] / value,
            ), bounds=(.25, 8.), method="bounded",
        ).x)
        score = grouped_nll(
            [samples[i] for i in selector], labels[selector], logits[selector] / temperature,
        )
        candidates.append((score, model, temperature, leaves))
    _, model, temperature, leaves = min(candidates, key=lambda candidate: candidate[0])
    probabilities = np.cumsum(
        softmax(model.predict(x, raw_score=True, num_threads=2) / temperature, axis=1),
        axis=1,
    )[:, :-1]
    phase_scores, _ = phase.arrays(observations, embeddings)
    probabilities = np.where(phase_scores[:, 2, None] >= phase_threshold, probabilities, -1.)
    selection = {}
    for j, horizon in enumerate(HORIZONS):
        indices = roles["selector"]
        selection[str(horizon)] = select_threshold(
            [samples[i] for i in indices], probabilities[indices, j], horizon,
        )

    def evaluate(values, features, encoded):
        obs = [row["observation"] for row in values]
        scores, _ = phase.arrays(obs, encoded)
        cdf = np.cumsum(softmax(
            model.predict(features, raw_score=True, num_threads=2) / temperature, axis=1,
        ), axis=1)[:, :-1]
        cdf = np.where(scores[:, 2, None] >= phase_threshold, cdf, -1.)
        _, old = work.arrays(obs, encoded)
        return {
            str(horizon): {
                "baseline_point": window_metrics(values, np.where(
                    scores[:, 2] >= phase_threshold, (old[:, 1] <= horizon).astype(float), -1.,
                ), horizon, 1.),
                "candidate": window_metrics(
                    values, cdf[:, j], horizon,
                    selection[str(horizon)]["threshold"] if selection[str(horizon)] else None,
                ),
                "candidate_fixed_p80": window_metrics(values, cdf[:, j], horizon, .8),
            } for j, horizon in enumerate(HORIZONS)
        }

    held = roles["evaluation"]
    report = {
        "scope": "CPU development work-only CDF; not deployed, sealed validation, or H2D benefit",
        "snapshot_policy": plan["snapshot_policy"],
        "target": "P(remaining output tokens <= h | natural final report); not RETURN wall time",
        "horizons": HORIZONS, "phase_and_encoder_frozen": True,
        "phase_threshold": phase_threshold,
        "phase_artifact_sha256": hashlib.sha256(args.phase_artifact.read_bytes()).hexdigest(),
        "encoder_weights_sha256": sha,
        "selected": {"leaves": leaves, "temperature": temperature,
                     "selection": "Astropy selector workflow-balanced conditional NLL",
                     "candidate_nll": [{"leaves": item[3], "nll": item[0]} for item in candidates]},
        "workflow_counts": {role: len(tasks) for role, tasks in identities.items()},
        "fit_label_counts": dict(Counter(map(int, labels[train]))),
        "fit_label_request_counts": {
            str(index): len({
                samples[i]["observation"].request_id for i in train if labels[i] == index
            }) for index in range(len(HORIZONS) + 1)
        },
        "excluded_intervened_workflows": excluded,
        "selector_operating_points": selection,
        "held_project": evaluate([samples[i] for i in held], x[held], embeddings[held]),
        "replays": {},
        "lead_interpretation": (
            "Leads are at delivered snapshot time, before inference/control latency. "
            "A short token horizon does not constrain future service gaps or prove "
            "a 100-500 ms native H2D start window."
        ),
    }
    for run in args.replay_arm:
        print(f"Replaying {run}", flush=True)
        values, skipped = load_samples(run, args.cache, plan["snapshot_policy"])
        encoded = cached_embeddings(values, encoder, sha, args.cache)
        features = np.column_stack((
            phase.design([row["observation"] for row in values], encoded),
            structural_work_features([row["observation"] for row in values]),
        ))
        report["replays"][str(run)] = {
            "excluded_intervened_workflows": skipped,
            "snapshot_count": len(values), "metrics": evaluate(values, features, encoded),
            "scope": "post-selection development replay; labels not used to fit or select",
        }
    args.output.mkdir(parents=True)
    model.save_model(str(args.output / "completion_windows.txt"))
    (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "selected": report["selected"], "fit_label_counts": report["fit_label_counts"],
        "selector_operating_points": selection, "report": str(args.output / "report.json"),
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
