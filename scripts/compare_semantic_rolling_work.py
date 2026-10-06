#!/usr/bin/env python3
"""Compare frozen heads on identical causal snapshots from held-out projects."""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
from statistics import median
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.predictor.child_semantic_work import SemanticReportPredictor
from scripts.train_child_semantic_work import cached_embeddings, cached_samples, first_triggers, split_roles
from scripts.summarize_semantic_h2d_ab import records as json_records


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


def work_arrays(model, observations, embeddings):
    scores, work = model.head.arrays(observations, embeddings)
    if model.work_head is not None:
        _, work = model.work_head.arrays(observations, embeddings)
    elif model.neural_work is not None:
        work = model.neural_work.arrays(observations, embeddings, model.head)
    return scores, work


def work_triggers(
    rows: list[dict], name: str, phase_threshold: float, *,
    token_horizon: int | None = None, time_horizon_ms: float | None = None,
    statistic: str = "upper",
) -> dict:
    if statistic not in ("upper", "center"):
        raise ValueError("work statistic must be upper or center")
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["request_id"]].append(row)
    selected = []
    for group in grouped.values():
        for row in sorted(group, key=lambda value: value["snapshot_ts_ms"]):
            if (
                not row["notice_active"] or row["observed_output_tokens"] < 16
                or row["scores"][name] < phase_threshold
            ):
                continue
            work = max(1., row[f"{name}_bounds"][2 if statistic == "upper" else 1])
            if token_horizon is not None and work <= token_horizon:
                selected.append(row)
                break
            rate = row["sampled_tokens_per_second"]
            if time_horizon_ms is not None and rate and work * 1000 / rate <= time_horizon_ms:
                selected.append(row)
                break
    final = [row for row in selected if row["actual_remaining_tokens"] is not None]
    leads = [row["remaining_client_wall_ms"] for row in final]
    errors = [row[name] - row["actual_remaining_tokens"] for row in final]
    return {
        "scope": "first sampled semantic trigger; no Host/capacity check or physical action",
        "request_count": len(grouped),
        "natural_return_requests": sum(
            group[0]["actual_remaining_tokens"] is not None for group in grouped.values()
        ),
        "requests_with_notice_observation": sum(
            any(row["notice_active"] for row in group) for group in grouped.values()
        ),
        "first_trigger_count": len(selected),
        "natural_first_trigger_count": len(final),
        "tool_round_false_trigger_count": len(selected) - len(final),
        "positive_work_at_trigger_count": sum(row["actual_remaining_tokens"] > 0 for row in final),
        "upper_underestimated_at_trigger_count": sum(
            row["actual_remaining_tokens"] > row[f"{name}_bounds"][2] for row in final
        ),
        "median_point_error_at_trigger_tokens": median(errors) if errors else None,
        "median_snapshot_lead_to_return_ms": median(leads) if leads else None,
        "lead_0_to_1000ms_count": sum(0 < lead <= 1000 for lead in leads),
        "lead_100_to_1000ms_count": sum(100 <= lead <= 1000 for lead in leads),
        "lead_over_2000ms_count": sum(lead > 2000 for lead in leads),
        "token_horizon": token_horizon, "time_horizon_ms": time_horizon_ms,
        "work_statistic": statistic,
        "first_trigger_rows": [{
            "request_id": row["request_id"], "snapshot_ts_ms": row["snapshot_ts_ms"],
            "actual_remaining_tokens": row["actual_remaining_tokens"],
            "predicted_center_tokens": row[name],
            "sampled_tokens_per_second": row["sampled_tokens_per_second"],
            "lead_to_return_ms": row["remaining_client_wall_ms"],
            "lead_to_native_eos_ms": row.get("lead_to_native_eos_ms"),
        } for row in selected],
    }


def replay_csv(path: Path) -> list[dict]:
    rows = []
    with path.open() as stream:
        for raw in csv.DictReader(stream):
            rows.append({
                "request_id": raw["request_id"],
                **{key: float(raw[key]) if raw[key] else None for key in (
                    "snapshot_ts_ms", "actual_remaining_tokens", "baseline", "candidate",
                    "sampled_tokens_per_second", "remaining_client_wall_ms",
                )},
                "scores": {name: float(raw[f"{name}_phase_score"]) for name in ("baseline", "candidate")},
                **{f"{name}_bounds": json.loads(raw[f"{name}_bounds"]) for name in ("baseline", "candidate")},
                "notice_active": raw["notice_active"] == "True",
                "observed_output_tokens": int(raw["observed_output_tokens"]),
            })
    return rows


def timing_replay(rows: list[dict], thresholds: dict, windows: Path | None) -> dict:
    cohorts = {"all_observed_requests": rows}
    if windows is not None:
        targets = json.loads(windows.read_text())["rows"]
        census = next(row for row in json_records(
            windows.parent / "opportunities/admission_opportunities.jsonl",
        ) if row["event"] == "safe_point_census")
        offset = census["ts_ms"] - census["monotonic_ms"]
        by_request = {row["final_request_id"]: row for row in targets}
        for row in rows:
            target = by_request.get(row["request_id"])
            if target is not None:
                row["lead_to_native_eos_ms"] = (
                    target["native_eos_ts_ms"] - row["snapshot_ts_ms"] - offset
                )
        ids = {
            row["final_request_id"] for row in targets
            if row["restore_target_samples_before_eos"] > 0
        }
        cohorts["observed_host_only_request_cohort"] = [
            row for row in rows if row["request_id"] in ids
        ]
    result = {}
    for cohort, values in cohorts.items():
        result[cohort] = {}
        for name in thresholds:
            result[cohort][name] = {}
            for horizon in (1000., 500., 250., 100.):
                report = work_triggers(
                    values, name, thresholds[name],
                    time_horizon_ms=horizon, statistic="center",
                )
                leads = [
                    row["lead_to_native_eos_ms"] for row in report["first_trigger_rows"]
                    if row["lead_to_native_eos_ms"] is not None
                ]
                report["observed_pre_eos_count"] = sum(lead > 0 for lead in leads)
                report["observed_at_least_150ms_before_eos_count"] = sum(lead >= 150 for lead in leads)
                report["median_snapshot_lead_to_native_eos_ms"] = median(leads) if leads else None
                result[cohort][name][str(int(horizon))] = report
    return {
        "scope": (
            "First sampled trigger, without inference/transport delay, reservation, "
            "continuous Host residency or live capacity proof. EOS/RETURN are "
            "evaluation labels only; the Host-only cohort is retrospective."
        ),
        "cohorts": result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", action="append", type=Path, default=[])
    parser.add_argument("--snapshot-policy", choices=("rolling_250ms", "rolling_100ms"))
    parser.add_argument("--replay-csv", type=Path)
    parser.add_argument("--window-audit", type=Path)
    args = parser.parse_args()
    candidate_report = json.loads((args.candidate.parent / "report.json").read_text())
    baseline_report = json.loads((args.baseline.parent / "report.json").read_text())
    if args.replay_csv is not None:
        thresholds = {
            name: source["calibration"]["semantic_event"]["request_operating_point"]["threshold"]
            for name, source in (("baseline", baseline_report), ("candidate", candidate_report))
        }
        report = timing_replay(replay_csv(args.replay_csv), thresholds, args.window_audit)
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        print(json.dumps({
            cohort: {
                name: {
                    horizon: {key: value for key, value in result.items() if key != "first_trigger_rows"}
                    for horizon, result in values.items()
                } for name, values in models.items()
            } for cohort, models in report["cohorts"].items()
        }, indent=2))
        return
    plan = candidate_report["plan"]
    previous = json.loads(args.baseline.read_text())
    if previous["metadata"]["adapted_encoder"]["weights_sha256"] != plan["encoder"]["revision"]:
        raise ValueError("comparison requires identical frozen semantic encoders")
    samples = []
    policy = args.snapshot_policy or plan["snapshot_policy"]
    runs = args.run or [
        ROOT / run for run in plan["training_runs"] + plan["calibration_evaluation_runs"]
    ]
    args.cache.mkdir(parents=True, exist_ok=True)
    coverage = {}
    excluded = {}
    for run in runs:
        rows, run_coverage = cached_samples(
            ROOT / run, args.cache, snapshot_policy=policy,
        )
        if plan.get("exclude_runtime_interventions", False):
            from scripts.fit_child_completion_windows import load_samples

            rows, excluded[str(run)] = load_samples(ROOT / run, args.cache, policy)
        coverage[str(run)] = run_coverage
        samples.extend(rows)
    if args.run:
        held = samples
    else:
        roles = split_roles(samples, plan)
        held = [samples[i] for i in roles["evaluation"]]
    if not held or not any(row["remaining_tokens"] is not None for row in held):
        raise ValueError("comparison needs causal snapshots with natural work labels")
    models = {
        name: SemanticReportPredictor.load(path)
        for name, path in (("baseline", args.baseline), ("candidate", args.candidate))
    }
    encoder = models["baseline"].encoder
    embeddings = cached_embeddings(held, encoder, plan["encoder"]["revision"], args.cache)
    observations = [row["observation"] for row in held]
    outputs = {
        name: work_arrays(model, observations, embeddings) for name, model in models.items()
    }
    records = [{
        "project": row["project"], "task": row["task"],
        "request_id": row["observation"].request_id,
        "snapshot_ts_ms": row["observation"].ts_ms,
        "actual_remaining_tokens": row["remaining_tokens"],
        "is_return": row["is_return"],
        "remaining_client_wall_ms": row["remaining_client_wall_ms"],
        "notice_active": row["observation"].notice_active,
        "observed_output_tokens": row["observation"].observed_output_tokens,
        "scores": {name: float(value[0][i, 2]) for name, value in outputs.items()},
        **{f"{name}_phase_score": float(value[0][i, 2]) for name, value in outputs.items()},
        **{name: float(value[1][i, 1]) for name, value in outputs.items()},
        **{f"{name}_bounds": list(map(float, value[1][i])) for name, value in outputs.items()},
    } for i, row in enumerate(held)]
    progress = defaultdict(list)
    for row in sorted(records, key=lambda value: value["snapshot_ts_ms"]):
        history = progress[row["request_id"]]
        now, tokens = row["snapshot_ts_ms"], row["observed_output_tokens"]
        rate = None
        if history:
            before = next((value for value in history if value[0] >= now - 500), history[0])
            if before[0] < now and before[1] < tokens:
                rate = min(500., max(1., (tokens - before[1]) * 1000 / (now - before[0])))
        row["sampled_tokens_per_second"] = rate
        history.append((now, tokens))
        del history[:-128]
    thresholds = {
        name: source["calibration"]["semantic_event"]["request_operating_point"]["threshold"]
        for name, source in (("baseline", baseline_report), ("candidate", candidate_report))
    }
    report = {
        "scope": "same-snapshot development comparison; no new sealed test or wall-clock precision claim",
        "evaluation_projects": sorted({row["project"] for row in held}),
        "explicit_replay_runs": list(map(str, args.run)), "coverage": coverage,
        "excluded_intervened_tasks_by_run": excluded,
        "snapshot_policy": policy,
        "phase_scores_identical": bool(np.array_equal(
            outputs["baseline"][0], outputs["candidate"][0],
        )),
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
                records, name, thresholds[name],
            ) for name in outputs
        },
        "work_upper_first_triggers": {
            name: {
                **{f"{horizon}_tokens": work_triggers(
                    records, name, thresholds[name], token_horizon=horizon,
                ) for horizon in (16, 32, 64)},
                "1000ms_sampled_rate": work_triggers(
                    records, name, thresholds[name], time_horizon_ms=1000.,
                ),
            } for name in outputs
        },
        "work_center_first_triggers": {
            name: work_triggers(
                records, name, thresholds[name], time_horizon_ms=1000., statistic="center",
            ) for name in outputs
        },
        "interval_snapshot_coverage": {
            name: float(np.mean([
                row[f"{name}_bounds"][0] <= row["actual_remaining_tokens"] <= row[f"{name}_bounds"][2]
                for row in records if row["actual_remaining_tokens"] is not None
            ])) for name in outputs
        },
        "timing_scope": (
            "Rate uses only preceding delivered snapshots, not future GPU service. "
            "It is sparse client-stream replay, not the native per-step rate or "
            "continuous restore-target/capacity evidence."
        ),
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    with args.output.with_suffix(".csv").open("w") as stream:
        fields = [
            "project", "task", "request_id", "snapshot_ts_ms",
            "actual_remaining_tokens", "baseline", "candidate",
            "baseline_bounds", "candidate_bounds", "notice_active",
            "observed_output_tokens", "sampled_tokens_per_second", "remaining_client_wall_ms",
            "baseline_phase_score", "candidate_phase_score",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
