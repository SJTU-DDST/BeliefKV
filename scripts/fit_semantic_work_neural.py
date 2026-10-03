#!/usr/bin/env python3
"""CPU-only nonlinear work comparison; frozen phase is never refitted or deployed."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.predictor.child_semantic_work import FrozenTextEncoder, SemanticHead, workflow_weights
from scripts.train_child_semantic_work import cached_samples, cached_embeddings, split_roles
from scripts.compare_semantic_rolling_work import metrics
from scripts.summarize_semantic_h2d_ab import records


def structural_features(observations):
    values = []
    for row in observations:
        text = row.content_tail.rstrip()
        lower = text.lower()
        values.append([
            float(text.endswith((".", "!", "?", "`", ")", "]", "}"))),
            float(text.count("```") % 2),
            float(text.count("(") > text.count(")")),
            float(text.count("[") > text.count("]")),
            float(text.count("{") > text.count("}")),
            float("\n" in text[-80:]),
            float(any(word in lower[-200:] for word in (
                "in summary", "in conclusion", "verified", "passed", "no further",
            ))),
            np.log1p(max(0, row.estimated_report_tokens - row.observed_output_tokens)),
        ])
    return np.asarray(values, dtype=np.float32)


def per_request_rows(samples, predicted):
    return [
        {"request_id": r["observation"].request_id,
         "snapshot_ts_ms": r["observation"].ts_ms,
         "actual_remaining_tokens": r["remaining_tokens"],
         "candidate": float(p)}
        for r, p in zip(samples, predicted)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--phase-artifact", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(2)
    args.cache.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    plan = json.loads(args.plan.read_text())
    artifact = json.loads(args.phase_artifact.read_text())
    phase = SemanticHead.load(args.phase_artifact)
    work_ref = artifact.get("conditional_work_head")
    baseline = SemanticHead.load(args.phase_artifact.parent / work_ref["path"]) if work_ref else phase
    samples, excluded = [], set()
    for run_name in plan["training_runs"] + plan["calibration_evaluation_runs"]:
        run = ROOT / run_name
        for file in run.glob("client_*/workflows/*/sandbox_audit.jsonl"):
            if any(r.get("event") in (
                "agent_graph_budget_finalization", "agent_guard_finalization_attempt",
                "agent_protocol_repair_attempt",
            ) for r in records(file)):
                excluded.add(file.parent.name)
        rows, _ = cached_samples(run, args.cache, snapshot_policy=plan["snapshot_policy"])
        samples.extend(r for r in rows if r["remaining_tokens"] is not None)
    samples = [r for r in samples if r["task"] not in excluded]
    roles = split_roles(samples, plan)
    sha = artifact["metadata"]["adapted_encoder"]["weights_sha256"]
    encoder = FrozenTextEncoder(artifact["metadata"]["adapted_encoder"]["snapshot"])
    embeddings = cached_embeddings(samples, encoder, sha, args.cache)
    observations = [r["observation"] for r in samples]
    x = np.column_stack((phase.design(observations, embeddings), structural_features(observations)))
    train = roles["training"]
    center, scale = x[train].mean(axis=0), np.maximum(x[train].std(axis=0), .1)
    data = torch.tensor((x-center)/scale, dtype=torch.float32)
    y = torch.tensor(np.log1p([r["remaining_tokens"] for r in samples]), dtype=torch.float32)
    weight = torch.tensor(workflow_weights([samples[i]["task"] for i in train]), dtype=torch.float32)
    candidates = []
    for decay in (0.001, 0.01, 0.1):
        for target in ("remaining", "total"):
            torch.manual_seed(21)
            model = nn.Sequential(
                nn.Linear(data.shape[1], 64), nn.GELU(),
                nn.Linear(64, 32), nn.GELU(), nn.Linear(32, 1),
            )
            optimizer = torch.optim.AdamW(model.parameters(), lr=.008, weight_decay=decay)
            labels = y
            if target == "total":
                labels = torch.tensor(np.log1p([
                    r["remaining_tokens"] + r["observation"].observed_output_tokens
                    for r in samples
                ]), dtype=torch.float32)
            for _ in range(400):
                optimizer.zero_grad()
                prediction = model(data[train]).squeeze(-1)
                loss = (nn.functional.smooth_l1_loss(
                    prediction, labels[train], reduction="none",
                ) * weight).sum()
                loss.backward()
                optimizer.step()
            with torch.inference_mode():
                raw = np.expm1(np.clip(model(data).squeeze(-1).numpy(), 0, 16))
            if target == "total":
                raw -= np.asarray([r.observed_output_tokens for r in observations])
            selector = roles["selector"]
            actual = np.asarray([samples[i]["remaining_tokens"] for i in selector])
            bias = float(np.median(actual - raw[selector]))
            predicted = np.maximum(0, raw + bias)
            selector_rows = per_request_rows([samples[i] for i in selector], predicted[selector])
            score = metrics(selector_rows, "candidate")
            candidates.append((score["median_absolute_error_tokens"], model, predicted, {
                "target": target, "weight_decay": decay, "bias": bias, "selector": score,
            }))
    _, model, predicted, selected = min(candidates, key=lambda c: c[0])
    held = roles["evaluation"]
    _, baseline_work = baseline.arrays([observations[i] for i in held], embeddings[held])
    evaluation = per_request_rows([samples[i] for i in held], predicted[held])
    for row, old in zip(evaluation, baseline_work):
        row["baseline"] = float(old[1])
    interval = roles["interval"]
    margin = float(np.quantile([
        abs(predicted[i]-samples[i]["remaining_tokens"]) for i in interval
    ], .8))
    torch.save({
        "state_dict": model.state_dict(), "center": center, "scale": scale,
        "input_dim": data.shape[1], "selected": selected,
        "interval_margin_tokens": margin,
    }, args.output / "conditional_work_candidate.pt")
    report = {
        "scope": "CPU development work-only experiment; not deployed or sealed validation",
        "phase_artifact_sha256": hashlib.sha256(args.phase_artifact.read_bytes()).hexdigest(),
        "phase_and_encoder_frozen": True, "fit_samples": len(train),
        "excluded_intervened_tasks": sorted(excluded), "selected": selected,
        "selection": "Astropy selector workflows only; Sphinx not used for model selection",
        "held_last_snapshot": {name: metrics(evaluation, name) for name in ("baseline", "candidate")},
        "held_near_end": {
            str(limit): {name: metrics([
                r for r in evaluation if r["actual_remaining_tokens"] <= limit
            ], name) for name in ("baseline", "candidate")} for limit in (32, 64, 128)
        },
        "candidates": [c[3] for c in candidates],
        "note": "Structural balance is an observed tail feature, not proof of complete syntax.",
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    with (args.output / "work_comparison.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=list(evaluation[0]))
        writer.writeheader(); writer.writerows(evaluation)
    print(json.dumps({k: report[k] for k in (
        "selected", "held_last_snapshot", "held_near_end",
    )}, indent=2))


if __name__ == "__main__":
    main()
