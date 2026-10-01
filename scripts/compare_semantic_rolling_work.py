#!/usr/bin/env python3
"""Compare frozen heads on identical causal snapshots from held-out projects."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import median
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.predictor.child_semantic_work import FrozenTextEncoder, SemanticHead
from scripts.train_child_semantic_work import cached_embeddings, cached_samples, first_triggers, split_roles


def metrics(rows: list[dict], name: str) -> dict:
    grouped = {}
    for row in rows:
        if row["actual_remaining_tokens"] is None:
            continue
        previous = grouped.get(row["request_id"])
        if previous is None or row["snapshot_ts_ms"] > previous["snapshot_ts_ms"]:
            grouped[row["request_id"]] = row
    errors = [
        row[name] - row["actual_remaining_tokens"] for row in grouped.values()
    ]
    return {
        "request_count": len(errors),
        "median_absolute_error_tokens": median(map(abs, errors)) if errors else None,
        "median_signed_error_tokens": median(errors) if errors else None,
        "p90_absolute_error_tokens": float(np.quantile(np.abs(errors), .9)) if errors else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidate_report = json.loads((args.candidate.parent / "report.json").read_text())
    baseline_report = json.loads((args.baseline.parent / "report.json").read_text())
    plan = candidate_report["plan"]
    previous = json.loads(args.baseline.read_text())
    if previous["metadata"]["adapted_encoder"]["weights_sha256"] != plan["encoder"]["revision"]:
        raise ValueError("comparison requires identical frozen semantic encoders")
    samples = []
    for run in plan["training_runs"] + plan["calibration_evaluation_runs"]:
        rows, _ = cached_samples(
            ROOT / run, args.cache, snapshot_policy=plan["snapshot_policy"],
        )
        samples.extend(rows)
    roles = split_roles(samples, plan)
    held = [samples[i] for i in roles["evaluation"]]
    encoder = FrozenTextEncoder(
        plan["encoder"]["local_snapshot"], max_tokens=plan["encoder"]["max_tokens"],
    )
    embeddings = cached_embeddings(held, encoder, plan["encoder"]["revision"], args.cache)
    observations = [row["observation"] for row in held]
    outputs = {
        name: SemanticHead.load(path).arrays(observations, embeddings)
        for name, path in (("baseline", args.baseline), ("candidate", args.candidate))
    }
    records = [{
        "project": row["project"], "task": row["task"],
        "request_id": row["observation"].request_id,
        "snapshot_ts_ms": row["observation"].ts_ms,
        "actual_remaining_tokens": row["remaining_tokens"],
        "is_return": row["is_return"],
        "remaining_client_wall_ms": row["remaining_client_wall_ms"],
        "scores": {name: float(value[0][i, 2]) for name, value in outputs.items()},
        **{name: float(value[1][i, 1]) for name, value in outputs.items()},
    } for i, row in enumerate(held)]
    report = {
        "scope": "same-snapshot development comparison; no new sealed test or wall-clock precision claim",
        "evaluation_projects": plan["evaluation_projects"],
        "snapshot_count": len(records),
        "last_snapshot_per_natural_return_request": {
            name: metrics(records, name) for name in outputs
        },
        "near_end_last_snapshot_per_request": {
            str(limit): {
                name: metrics([
                    row for row in records
                    if row["actual_remaining_tokens"] is not None
                    and row["actual_remaining_tokens"] <= limit
                ], name) for name in outputs
            } for limit in (32, 64, 128)
        },
        "first_crossings_with_each_frozen_calibration_threshold": {
            name: first_triggers(
                records, name,
                source["calibration"]["semantic_event"]["request_operating_point"]["threshold"],
            ) for name, source in (
                ("baseline", baseline_report), ("candidate", candidate_report)
            )
        },
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    with args.output.with_suffix(".csv").open("w") as stream:
        fields = [
            "project", "task", "request_id", "snapshot_ts_ms",
            "actual_remaining_tokens", "baseline", "candidate",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
