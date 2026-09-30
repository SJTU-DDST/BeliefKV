#!/usr/bin/env python3
"""Frozen train/calibration/test project roles for semantic child report heads."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
import torch
import scipy
import transformers

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.predictor.child_report_phase import ReportObservation
from beliefkv.predictor.child_semantic_work import (
    FrozenTextEncoder, calibrate_scores_and_bias, calibrate_work_bounds,
    choose_request_threshold, fit_head,
)
from scripts.evaluate_child_report_phase_work import CHECKPOINTS, collect_run, fit, quality


def cached_samples(run: Path, cache: Path) -> tuple[list[dict], dict]:
    sources = [
        run / "server/runtime_audit.jsonl",
        run / "server/runtime_events.sglang.jsonl",
        *run.glob("client_*/workflows/*/child_stream_content.jsonl"),
        *run.glob("client_*/workflows/*/runtime_events.deepagents.jsonl"),
        *run.glob("client_*/workflows/*/child_stream_content_stats.json"),
        *run.glob("client_*/workflows/*/child_reports.json"),
        *run.glob("workloads/workflows/*/child_stream_content.jsonl"),
        *run.glob("workloads/workflows/*/runtime_events.deepagents.jsonl"),
        *run.glob("workloads/workflows/*/child_stream_content_stats.json"),
        *run.glob("workloads/workflows/*/child_reports.json"),
    ]
    for source in (
        ROOT / "scripts/evaluate_child_report_phase_work.py",
        ROOT / "scripts/pilot_child_stream_service_progress.py",
        ROOT / "scripts/pilot_child_stream_content.py",
        ROOT / "scripts/child_stream_service_index.py",
    ):
        sources.append(source)
    signature = hashlib.sha256(json.dumps([
        (str(path), path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(sources)
    ]).encode()).hexdigest()
    path = cache / f"samples-{signature}.json.gz"
    if path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            raw = json.load(stream)
        rows = [
            {**row, "observation": ReportObservation(**row["observation"])}
            for row in raw["rows"]
        ]
        return rows, raw["coverage"]
    rows, coverage = collect_run(run)
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump({
            "rows": [
                {**row, "observation": asdict(row["observation"])} for row in rows
            ],
            "coverage": coverage,
        }, stream)
    return rows, coverage


def cached_embeddings(
    samples: list[dict], encoder: FrozenTextEncoder, revision: str, cache: Path,
) -> np.ndarray:
    texts = [row["observation"].content_tail for row in samples]
    signature = hashlib.sha256(json.dumps(
        [revision, encoder.max_tokens, texts], ensure_ascii=False,
    ).encode()).hexdigest()
    path = cache / f"embeddings-{signature}.npz"
    if path.exists():
        with np.load(path, allow_pickle=False) as values:
            return values["embeddings"]
    values = encoder.encode(texts)
    np.savez_compressed(path, embeddings=values)
    return values


def split_roles(samples: list[dict], plan: dict) -> dict[str, np.ndarray]:
    sets = {
        role: set(plan[f"{role}_projects"])
        for role in ("training", "calibration", "evaluation")
    }
    if any(sets[a] & sets[b] for a, b in (
        ("training", "calibration"), ("training", "evaluation"),
        ("calibration", "evaluation"),
    )):
        raise ValueError("project roles overlap")
    unknown = {row["project"] for row in samples} - set.union(*sets.values())
    if unknown:
        raise ValueError(f"unassigned projects: {sorted(unknown)}")
    result = {
        role: np.asarray([i for i, row in enumerate(samples) if row["project"] in projects])
        for role, projects in sets.items()
    }
    if any(not len(indices) for indices in result.values()):
        raise ValueError("every frozen project role needs observed samples")
    calibration_tasks = sorted(
        {samples[i]["task"] for i in result["calibration"]},
        key=lambda task: hashlib.sha256(task.encode()).hexdigest(),
    )
    selector = set(calibration_tasks[::2])
    interval = set(calibration_tasks[1::2])
    result["selector"] = np.asarray([
        i for i in result["calibration"] if samples[i]["task"] in selector
    ])
    result["interval"] = np.asarray([
        i for i in result["calibration"] if samples[i]["task"] in interval
    ])
    return result


def first_triggers(records: list[dict], name: str, threshold: float | None) -> dict:
    if threshold is None:
        return {"status": "no_calibration_operating_point"}
    grouped = defaultdict(list)
    for row in records:
        grouped[row["request_id"]].append(row)
    selected = []
    for group in grouped.values():
        first = next((
            row for row in sorted(group, key=lambda value: value["snapshot_ts_ms"])
            if row["scores"][name] >= threshold
        ), None)
        if first:
            selected.append(first)
    true = [row for row in selected if row["is_return"]]
    count = sum(rows[0]["is_return"] for rows in grouped.values())
    return {
        "threshold": threshold, "request_denominator": len(grouped),
        "natural_returns": count, "true_first_triggers": len(true),
        "tool_false_first_triggers": len(selected) - len(true),
        "precision": len(true) / len(selected) if selected else None,
        "return_recall": len(true) / count if count else None,
        "actual_lead_at_least_500ms": sum(
            row["remaining_client_wall_ms"] >= 500 for row in true
        ),
    }


def interval_quality(records: list[dict], name: str) -> dict:
    workflows = defaultdict(list)
    widths = []
    for row in records:
        if row["remaining_tokens"] is not None:
            low, _, high = row["work"][name]
            workflows[row["task"]].append(low <= row["remaining_tokens"] <= high)
            widths.append(high - low)
    return {
        "final_report_workflows": len(workflows),
        "all_observed_snapshots_workflow_coverage": (
            sum(all(values) for values in workflows.values()) / len(workflows)
            if workflows else None
        ),
        "mean_width_tokens": float(np.mean(widths)) if widths else None,
        "scope": "Observed qualified snapshots only, not the entire unsampled trajectory.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=ROOT / "experiments/processed/child_semantic_stage2_cache")
    parser.add_argument("--adapt-encoder", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    plan = json.loads(args.plan.read_text())
    torch.set_num_threads(2)
    args.cache.mkdir(parents=True, exist_ok=True)
    samples, coverage = [], {}
    for run in plan["training_runs"] + plan["calibration_evaluation_runs"]:
        print(f"Collecting {run}", flush=True)
        rows, coverage[run] = cached_samples(ROOT / run, args.cache)
        samples.extend(rows)
    if len({(row["observation"].request_id, row["observation"].ts_ms) for row in samples}) != len(samples):
        raise ValueError("repeated observations across source runs")
    roles = split_roles(samples, plan)
    encoder = FrozenTextEncoder(
        plan["encoder"]["local_snapshot"], max_tokens=plan["encoder"]["max_tokens"],
    )
    args.output.mkdir(parents=True)
    adaptation = None
    revision = plan["encoder"]["revision"]
    if args.adapt_encoder:
        from beliefkv.predictor.semantic_phase_adaptation import adapt_encoder

        adaptation = adapt_encoder(
            encoder, [samples[i] for i in roles["training"]],
            args.output / "adapted_encoder", epochs=4,
        )
        with (args.output / "adapted_encoder/model.safetensors").open("rb") as stream:
            revision = hashlib.file_digest(stream, "sha256").hexdigest()
        adaptation["weights_sha256"] = revision
    print(f"Encoding {len(samples)} label-free delivered texts", flush=True)
    embeddings = cached_embeddings(samples, encoder, revision, args.cache)
    train = [samples[i] for i in roles["training"]]
    selector = [samples[i] for i in roles["selector"]]
    interval = [samples[i] for i in roles["interval"]]
    held = [samples[i] for i in roles["evaluation"]]
    observations = [row["observation"] for row in held]
    all_outputs, calibrations, heads = {}, {}, {}
    for name, dimensions in (("progress_event", 0), ("semantic_event", plan["pca_dimensions"])):
        print(f"Fitting {name}", flush=True)
        head = fit_head(
            train, embeddings[roles["training"]], dimensions=dimensions,
            phase_regularization=plan["phase_regularization"],
            work_regularization=plan["work_regularization"],
        )
        all_outputs[f"{name}_raw"] = head.arrays(observations, embeddings[roles["evaluation"]])
        metadata = {
            "plan": plan, "training_projects": plan["training_projects"],
            "calibration_projects": plan["calibration_projects"],
            "evaluation_projects": plan["evaluation_projects"],
            "adapted_encoder": adaptation,
        }
        head.save(args.output / f"{name}_raw.json", metadata=metadata)
        calibration = calibrate_scores_and_bias(
            head, selector, embeddings[roles["selector"]],
        )
        calibration["work_bounds"] = calibrate_work_bounds(
            head, interval, embeddings[roles["interval"]],
            coverage=plan["nominal_workflow_interval_coverage"],
        )
        scores, _ = head.arrays(
            [row["observation"] for row in selector], embeddings[roles["selector"]],
        )
        calibration["request_operating_point"] = choose_request_threshold(
            selector, scores[:, 2], precision=plan["calibration_precision_target"],
            minimum=plan["minimum_selected_calibration_requests"],
        )
        calibrations[name] = calibration
        all_outputs[f"{name}_calibrated"] = head.arrays(observations, embeddings[roles["evaluation"]])
        head.save(args.output / f"{name}_calibrated.json", metadata=metadata)
        heads[name] = head
    print("Fitting matched lightweight CNN baseline", flush=True)
    cnn, cnn_info = fit(
        train, with_events=True, epochs=20, seed=21, balance_phases=True,
    )
    cnn.save(args.output / "cnn_baseline.pt", training_projects=plan["training_projects"])
    cnn_outputs = cnn.predict(observations)
    all_outputs["cnn_raw"] = (
        np.asarray([
            [1. - row.final_report_score - row.completion_notice_score,
             row.completion_notice_score, row.final_report_score]
            for row in cnn_outputs
        ]),
        np.asarray([row.conditional_remaining_tokens for row in cnn_outputs]),
    )
    priors = {}
    for threshold in CHECKPOINTS:
        values = [
            row["remaining_tokens"] for row in train
            if threshold in row["thresholds"] and row["remaining_tokens"] is not None
        ]
        priors[str(threshold)] = tuple(float(v) for v in np.quantile(values, (.1, .5, .9)))
    records = []
    for index, row in enumerate(held):
        observation = row["observation"]
        records.append({
            **{key: value for key, value in row.items() if key != "observation"},
            "request_id": observation.request_id, "invocation_id": observation.invocation_id,
            "snapshot_ts_ms": observation.ts_ms,
            "prior_work_by_checkpoint": priors,
            "scores": {
                **{name: float(scores[index, 2]) for name, (scores, _) in all_outputs.items()},
                "notice_only": float(observation.notice_active),
            },
            "work": {
                **{name: list(work[index]) for name, (_, work) in all_outputs.items()},
                "notice_only": priors[str(row["thresholds"][0])],
            },
        })
    names = (*all_outputs.keys(), "notice_only")
    report = {
        "plan": plan,
        "adapted_encoder": adaptation,
        "environment": {
            "python": sys.version, "torch": torch.__version__,
            "numpy": np.__version__, "scipy": scipy.__version__,
            "transformers": transformers.__version__,
        },
        "plan_sha256": hashlib.sha256(args.plan.read_bytes()).hexdigest(),
        "examples_sha256": hashlib.sha256(json.dumps([
            {**row, "observation": asdict(row["observation"])} for row in samples
        ], sort_keys=True).encode()).hexdigest(),
        "status": "retrospective_project_disjoint_semantic_calibration_validation",
        "source_coverage": coverage,
        "split_sizes": {
            name: {
                "snapshots": len(indices),
                "projects": sorted({samples[i]["project"] for i in indices}),
                "workflows": len({samples[i]["task"] for i in indices}),
                "requests": len({samples[i]["observation"].request_id for i in indices}),
            }
            for name, indices in roles.items()
        },
        "calibration": calibrations,
        "fixed_threshold_first_triggers": {
            str(cut): {name: first_triggers(records, name, cut) for name in names}
            for cut in (.5, .9, .95)
        },
        "calibration_operating_point_test": {
            name: first_triggers(
                records, f"{name}_calibrated",
                calibrations[name]["request_operating_point"]["threshold"],
            ) for name in heads
        },
        "by_checkpoint": {
            str(threshold): {
                name: quality(
                    [row for row in records if threshold in row["thresholds"]],
                    name, threshold=threshold,
                ) for name in names
            } for threshold in CHECKPOINTS
        },
        "workflow_interval_quality": {
            name: interval_quality(records, name) for name in names
        },
        "cnn_training": cnn_info,
        "cost": {},
        "limitations": (
            "Historical projects were seen in previous research, not a new sealed "
            "workload. Model fitting, score/bias calibration, interval calibration "
            "and validation roles are disjoint as recorded. Workflow-split bounds "
            "do not guarantee cross-project exchangeability. Interval widening "
            "is not point-ETA improvement. Native notifications may differ across "
            "source harness versions. No inference changes the child protocol, "
            "no new guard, no RETURN-time output and no H2D authorization."
        ),
    }
    print("Benchmarking full semantic pipeline and cached-feature head", flush=True)
    full_cost, cached_cost = [], []
    for i in range(min(40, len(held))):
        observation = observations[i]
        start = time.perf_counter_ns()
        encoded = encoder.encode([observation.content_tail])
        heads["semantic_event"].predictions([observation], encoded)
        full_cost.append((time.perf_counter_ns() - start) / 1e6)
        start = time.perf_counter_ns()
        heads["semantic_event"].predictions([observation], encoded)
        cached_cost.append((time.perf_counter_ns() - start) / 1e6)
    report["cost"] = {
        "cpu_threads": 2,
        "full_encoder_head_p50_ms": float(np.median(full_cost)),
        "full_encoder_head_p95_ms": float(np.quantile(full_cost, .95)),
        "cached_embedding_head_p50_ms": float(np.median(cached_cost)),
        "cached_embedding_head_p95_ms": float(np.quantile(cached_cost, .95)),
    }
    (args.output / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )
    with (args.output / "rows.jsonl").open("w", encoding="utf-8") as stream:
        for row in records:
            stream.write(json.dumps(row, allow_nan=False) + "\n")
    print(json.dumps(report["calibration_operating_point_test"], indent=2))


if __name__ == "__main__":
    main()
