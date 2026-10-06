#!/usr/bin/env python3
"""Refit conditional work while freezing the deployed semantic phase head."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import sys

import torch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.predictor.child_semantic_work import (
    FrozenTextEncoder, SemanticHead, calibrate_scores_and_bias, calibrate_work_bounds, fit_head,
    encoder_snapshot,
)
from beliefkv.predictor.conditional_work import LEGACY_WORK_PROJECTION, SIGNED_WORK_PROJECTION
from scripts.compare_semantic_rolling_work import metrics
from scripts.train_child_semantic_work import cached_embeddings, cached_samples, split_roles
from scripts.summarize_semantic_h2d_ab import records


def work_rows(samples, work):
    return [{
        "request_id": row["observation"].request_id,
        "snapshot_ts_ms": row["observation"].ts_ms,
        "actual_remaining_tokens": row["remaining_tokens"],
        "candidate": float(prediction[1]),
    } for row, prediction in zip(samples, work)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--phase-artifact", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--work-interval-projection",
        choices=(LEGACY_WORK_PROJECTION, SIGNED_WORK_PROJECTION),
        default=SIGNED_WORK_PROJECTION,
    )
    args = parser.parse_args()
    torch.set_num_threads(2)
    args.cache.mkdir(parents=True, exist_ok=True)
    plan = json.loads(args.plan.read_text())
    frozen = json.loads(args.phase_artifact.read_text())
    encoder_sha = frozen["metadata"]["adapted_encoder"]["weights_sha256"]
    if plan["encoder"]["revision"] != encoder_sha:
        raise ValueError("work refit must reuse the frozen phase encoder")
    samples, excluded = [], {}
    for run in plan["training_runs"] + plan["calibration_evaluation_runs"]:
        print(f"Collecting {run}", flush=True)
        rows, _ = cached_samples(ROOT / run, args.cache, snapshot_policy=plan["snapshot_policy"])
        if plan.get("exclude_runtime_interventions", False):
            rejected = {
                path.parent.name for path in (
                    *(ROOT / run).glob("client_*/workflows/*/sandbox_audit.jsonl"),
                    *(ROOT / run).glob("workloads/workflows/*/sandbox_audit.jsonl"),
                ) if any(row.get("event") in (
                    "agent_graph_budget_finalization", "agent_guard_finalization_attempt",
                    "agent_protocol_repair_attempt",
                ) for row in records(path))
            }
            excluded[run] = sorted(rejected)
            rows = [row for row in rows if row["task"] not in rejected]
        samples.extend(rows)
    roles = split_roles(samples, plan)
    encoder = FrozenTextEncoder(plan["encoder"]["local_snapshot"], max_tokens=256)
    embeddings = cached_embeddings(samples, encoder, encoder_sha, args.cache)
    train = [samples[i] for i in roles["training"]]
    selector = [samples[i] for i in roles["selector"]]
    interval = [samples[i] for i in roles["interval"]]
    held = [samples[i] for i in roles["evaluation"]]
    args.output.mkdir(parents=True)
    heads, calibration = {}, {}
    for target in ("remaining", "total"):
        head = fit_head(
            train, embeddings[roles["training"]], dimensions=plan["pca_dimensions"],
            phase_regularization=plan["phase_regularization"],
            work_regularization=plan["work_regularization"], work_target=target,
            work_interval_projection=args.work_interval_projection,
        )
        bias = calibrate_scores_and_bias(head, selector, embeddings[roles["selector"]])
        bounds = calibrate_work_bounds(
            head, interval, embeddings[roles["interval"]],
            coverage=plan["nominal_workflow_interval_coverage"],
        )
        _, work = head.arrays([row["observation"] for row in selector], embeddings[roles["selector"]])
        calibration[target] = {
            "bias": bias, "bounds": bounds,
            "last_snapshot": metrics(work_rows(selector, work), "candidate"),
        }
        head.save(args.output / f"work_{target}.json", metadata={
            "plan": plan, "purpose": "conditional_work_only; phase coefficients unused",
        })
        heads[target] = head
    selected = min(
        heads,
        key=lambda target: calibration[target]["last_snapshot"]["median_absolute_error_tokens"],
    )
    work_path = args.output / f"work_{selected}.json"
    composite = copy.deepcopy(frozen)
    snapshot = encoder_snapshot(frozen, args.phase_artifact)
    composite["metadata"]["adapted_encoder"]["snapshot"] = str(snapshot)
    composite["conditional_work_head"] = {
        "path": work_path.name,
        "sha256": hashlib.sha256(work_path.read_bytes()).hexdigest(),
    }
    composite_path = args.output / "semantic_event_calibrated.json"
    composite_path.write_text(json.dumps(composite, indent=2) + "\n")
    phase = SemanticHead.load(args.phase_artifact)
    reference = frozen.get("conditional_work_head")
    baseline = SemanticHead.load(
        args.phase_artifact.parent / reference["path"],
    ) if reference else phase
    observations = [row["observation"] for row in held]
    phase_scores, _ = phase.arrays(observations, embeddings[roles["evaluation"]])
    _, old_work = baseline.arrays(observations, embeddings[roles["evaluation"]])
    _, new_work = heads[selected].arrays(observations, embeddings[roles["evaluation"]])
    rows = [{
        **row,
        "baseline": float(old[1]), "candidate": float(new[1]),
        "frozen_phase_score": float(score[2]),
    } for row, old, new, score in zip(work_rows(held, new_work), old_work, new_work, phase_scores)]
    report = {
        "scope": "work-only development refit; no sealed test or transfer benefit",
        "phase_frozen_sha256": hashlib.sha256(args.phase_artifact.read_bytes()).hexdigest(),
        "encoder_weights_sha256": encoder_sha,
        "plan": plan, "selected_work_target": selected,
        "selection_basis": "calibration selector workflows only",
        "work_interval_projection": args.work_interval_projection,
        "baseline_work_head": reference,
        "excluded_intervened_tasks_by_run": excluded,
        "role_snapshot_counts": {role: len(indices) for role, indices in roles.items()},
        "role_workflow_counts": {
            role: len({samples[i]["task"] for i in indices})
            for role, indices in roles.items()
        },
        "work_calibration": calibration,
        "evaluation_projects": plan["evaluation_projects"],
        "evaluation_last_snapshot": {name: metrics(rows, name) for name in ("baseline", "candidate")},
        "evaluation_near_end": {
            str(limit): {name: metrics([
                row for row in rows if row["actual_remaining_tokens"] is not None
                and row["actual_remaining_tokens"] <= limit
            ], name) for name in ("baseline", "candidate")}
            for limit in (32, 64, 128)
        },
        "calibration": copy.deepcopy(json.loads(
            (args.phase_artifact.parent / "report.json").read_text()
        )["calibration"]),
    }
    report["calibration"]["semantic_event"]["work_bounds"] = calibration[selected]["bounds"]
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    with (args.output / "work_comparison.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({key: report[key] for key in (
        "selected_work_target", "work_calibration", "evaluation_last_snapshot",
    )}, indent=2))


if __name__ == "__main__":
    main()
