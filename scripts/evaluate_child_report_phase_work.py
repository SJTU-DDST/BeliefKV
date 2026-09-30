#!/usr/bin/env python3
"""Project-held-out delivered-token phase classification and remaining work."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
import time

import numpy as np
import orjson
import torch
from torch.nn import functional as F

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.predictor.child_report_phase import (
    ChildReportPredictor, PHASES, ReportObservation, ReportPhaseNetwork,
    fit_vocabulary, pinball_loss, tensors,
)
from scripts.audit_native_final_stage_notice import _interval_union
from scripts.pilot_child_stream_service_progress import service_rows


CHECKPOINTS = (0, 128, 512, 1024)


def event_states(root: Path) -> dict[tuple[str, str], list[dict]]:
    indexed = defaultdict(list)
    for path in sorted(root.glob("*/runtime_events.deepagents.jsonl")):
        with path.open("rb") as stream:
            for line in stream:
                event = orjson.loads(line)
                child = event.get("invocation_id")
                if child:
                    indexed[(path.parent.name, child)].append(event)
    return {key: sorted(value, key=lambda row: row["ts_ms"])
            for key, value in indexed.items()}


def observation_for(
    row: dict, snap: dict, events: list[dict],
) -> ReportObservation:
    notice = None
    tools, rounds = 0, 0
    for event in events:
        if event["ts_ms"] > snap["ts_ms"]:
            break
        attrs = event.get("attributes") or {}
        kind = event["kind"]
        if kind == "tool_start":
            tools += 1
            if attrs.get("tool_name") != "announce_completion_intent":
                notice = None
        elif kind == "llm_submit" and not attrs.get("runtime_internal"):
            rounds += 1
            if event.get("context_id") != row["context_id"]:
                notice = None
        elif kind in ("return", "invocation_cancel", "context_compact"):
            notice = None
        elif (
            kind == "structured_action"
            and attrs.get("child_completion_signal_kind") == "stage"
            and attrs.get("beliefkv_child_completion_intent") is True
            and event.get("context_id") == row["context_id"]
            and type(event.get("context_epoch")) is int
            and event["context_epoch"] <= row["context_epoch"]
        ):
            notice = attrs.get("estimated_final_report_tokens")
            notice = notice if type(notice) is int and notice >= 0 else 0
    return ReportObservation(
        row["rid"], row["invocation_id"], row["context_id"],
        row["context_epoch"], float(snap["ts_ms"]),
        snap.get("delivered_text_history", snap["content_tail"]),
        snap["content_chars"], snap["observed_output_tokens"],
        notice_active=notice is not None,
        estimated_report_tokens=notice or 0,
        prior_tool_calls=tools, prior_model_rounds=rounds,
    )


def physical_labels(run: Path, rows: list[dict]) -> tuple[dict, dict]:
    by_rid = {row["rid"]: row for row in rows}
    results, intervals = {}, defaultdict(list)
    incomplete = set()
    with (run / "server/runtime_events.sglang.jsonl").open("rb") as stream:
        for line in stream:
            event = orjson.loads(line)
            attrs = event.get("attributes") or {}
            rid = attrs.get("request_id")
            row = by_rid.get(rid)
            if (
                event.get("kind") == "llm_result" and row
                and event.get("invocation_id") == row["invocation_id"]
                and event.get("context_id") == row["context_id"]
                and event.get("context_epoch") == row["context_epoch"]
                and type(attrs.get("output_tokens")) is int
            ):
                results[rid] = {
                    "output_tokens": attrs["output_tokens"],
                    "server_result_ms": float(event["ts_ms"]),
                }
    with (run / "server/runtime_audit.jsonl").open("rb") as stream:
        for line in stream:
            event = orjson.loads(line)
            if event.get("event") != "gpu_service_sample":
                continue
            start, end = event.get("service_start_ts_ms"), event.get("complete_ts_ms")
            for sample in event.get("request_samples") or ():
                rid = sample.get("request_id")
                row = by_rid.get(rid)
                if row is None or any(
                    sample.get(key) != row[key]
                    for key in ("invocation_id", "context_id", "context_epoch")
                ):
                    continue
                if (
                    type(start) not in (int, float)
                    or type(end) not in (int, float)
                    or not np.isfinite(start) or not np.isfinite(end)
                    or end < start
                ):
                    incomplete.add(rid)
                    continue
                intervals[rid].append((float(start), float(end), event["phase"]))
    for rid, result in results.items():
        result["intervals"] = [
            interval for interval in intervals[rid]
            if interval[1] <= result["server_result_ms"]
        ]
        result["intervals_complete"] = rid not in incomplete
    return results, {
        "matched_result_counts": len(results),
        "incomplete_interval_requests": len(incomplete),
    }


def collect_run(run: Path) -> tuple[list[dict], dict]:
    roots = [
        path for path in (*run.glob("client_*/workflows"), run / "workloads/workflows")
        if path.is_dir()
    ]
    if len(roots) != 1:
        raise ValueError(f"expected one client workflow root: {run}")
    rows, coverage = service_rows([roots[0]], [run])
    histories = event_states(roots[0])
    labels, coverage["physical_label_coverage"] = physical_labels(run, rows)
    samples, exclusions = [], Counter()
    for row in rows:
        selected = {}
        for threshold in CHECKPOINTS:
            snap = next(
                (snap for snap in row["snapshots"] if snap["content_chars"] >= threshold),
                None,
            )
            if snap is not None:
                selected.setdefault(snap["ts_ms"], (snap, []))[1].append(threshold)
        for snap, thresholds in selected.values():
            child_events = histories.get((row["task"], row["invocation_id"]), [])
            observation = observation_for(
                row, snap, child_events,
            )
            # Future result metadata is a training label only, never an observation.
            native_result = next((
                event for event in child_events
                if event["kind"] == "llm_result"
                and (event.get("attributes") or {}).get("request_id") == row["rid"]
            ), None)
            notice_round = bool(
                native_result is not None
                and native_result["attributes"].get("structured_action_names")
                == ["announce_completion_intent"]
            )
            label = labels.get(row["rid"])
            work, future_active, wait, past_per_token = None, None, None, None
            if row["label"] == "return":
                if label is None or label["output_tokens"] < observation.observed_output_tokens:
                    exclusions["missing_or_invalid_terminal_token_label"] += 1
                else:
                    work = label["output_tokens"] - observation.observed_output_tokens
                    when = snap["observed_decode_server_ts_ms"]
                    if label["intervals_complete"] and when < label["server_result_ms"]:
                        spans = label["intervals"]
                        future_all = _interval_union([
                            (max(start, when), end) for start, end, _ in spans
                            if end > when
                        ])
                        future_active = _interval_union([
                            (max(start, when), end) for start, end, phase in spans
                            if end > when and phase == "decode"
                        ])
                        past_decode = _interval_union([
                            (start, min(end, when)) for start, end, phase in spans
                            if start < when and phase == "decode"
                        ])
                        past_per_token = past_decode / max(
                            observation.observed_output_tokens, 1,
                        )
                        wait = max(0., label["server_result_ms"] - when - future_all)
            samples.append({
                "observation": observation,
                "delivered_text_history_gaps": snap.get("delivered_text_history_gaps", 0),
                "task": row["task"], "project": row["project"],
                "thresholds": thresholds,
                "is_return": row["label"] == "return",
                "phase_label": 2 if row["label"] == "return" else 1 if notice_round else 0,
                "remaining_tokens": work,
                "remaining_client_wall_ms": (
                    row["return_ts"] - snap["ts_ms"] if row["label"] == "return" else None
                ),
                "future_decode_interval_ms": future_active,
                "future_no_service_ms": wait,
                "observed_decode_ms_per_token": past_per_token,
                "run": str(run),
            })
    coverage["work_label_exclusions"] = dict(exclusions)
    coverage["training_snapshots"] = len(samples)
    coverage["snapshots_with_text_history_gaps"] = sum(
        row["delivered_text_history_gaps"] > 0 for row in samples
    )
    return samples, coverage


def fit(
    samples: list[dict], *, with_events: bool, epochs: int, seed: int,
    balance_phases: bool = False,
) -> tuple[ChildReportPredictor, dict]:
    torch.manual_seed(seed)
    observations = [row["observation"] for row in samples]
    vocabulary = fit_vocabulary(observations)
    raw = np.asarray([row.features(with_events=with_events) for row in observations],
                     dtype=np.float32)
    center, scale = raw.mean(axis=0), np.maximum(raw.std(axis=0), 0.25)
    ids, numeric = tensors(
        observations, vocabulary, with_events=with_events, center=center, scale=scale,
    )
    labels = torch.tensor([row["phase_label"] for row in samples], dtype=torch.long)
    work_mask = torch.tensor([row["remaining_tokens"] is not None for row in samples])
    target = torch.tensor([
        np.log1p(row["remaining_tokens"] or 0) for row in samples
    ], dtype=torch.float32)
    if len(torch.unique(labels)) < 2 or not work_mask.any():
        raise ValueError("multiple phase classes and terminal work labels are required")
    counts = Counter(row["task"] for row in samples)
    weights = torch.tensor([1. / counts[row["task"]] for row in samples])
    weights /= weights.mean()
    class_mass = torch.stack([
        weights[labels == i].sum() for i in range(len(PHASES))
    ]).clamp(min=1e-4)
    class_weights = torch.ones(len(PHASES))
    if balance_phases:
        class_weights = torch.sqrt(class_mass.sum() / class_mass)
        class_weights /= (class_weights * class_mass).sum() / class_mass.sum()
    network = ReportPhaseNetwork(len(vocabulary) + 2)
    initial = np.quantile(target[work_mask].numpy(), (.1, .5, .9))
    increments = np.maximum(np.diff(np.r_[0., initial]), 0.01)
    with torch.no_grad():
        network.work.bias.copy_(torch.tensor(np.log(np.expm1(increments)),
                                            dtype=torch.float32))
        prior = class_mass * class_weights
        network.stage.bias.copy_(torch.log(prior / prior.sum()))
    optimizer = torch.optim.AdamW(network.parameters(), lr=0.003, weight_decay=0.01)
    started = time.perf_counter()
    network.train()
    for _ in range(epochs):
        order = torch.randperm(len(samples))
        for batch in order.split(128):
            logits, quantiles = network(ids[batch], numeric[batch])
            loss = (F.cross_entropy(
                logits, labels[batch], reduction="none",
            ) * weights[batch] * class_weights[labels[batch]]).mean()
            selected = work_mask[batch]
            if selected.any():
                loss = loss + (
                    pinball_loss(quantiles[selected], target[batch][selected])
                    * weights[batch][selected]
                ).sum() / weights[batch][selected].sum()
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(network.parameters(), 5.)
            optimizer.step()
    return ChildReportPredictor(
        network, vocabulary, center, scale, with_events=with_events,
    ), {
        "train_seconds": time.perf_counter() - started,
        "parameters": sum(value.numel() for value in network.parameters()),
        "vocabulary": len(vocabulary),
        "training_samples": len(samples),
        "training_phase_counts": [int(sum(labels == i)) for i in range(len(PHASES))],
        "phase_loss_weights": class_weights.tolist(),
    }


def quality(records: list[dict], model: str, *, threshold: int | None = None) -> dict:
    truth = np.asarray([row["is_return"] for row in records], dtype=bool)
    scores = np.asarray([row["scores"][model] for row in records])
    selected = scores >= 0.5
    true = int(sum(truth & selected))
    false = int(sum(~truth & selected))
    work = [row for row in records if row["remaining_tokens"] is not None]
    errors, cover, service_errors, waits, widths, accepted_errors = [], [], [], [], [], []
    for row in work:
        low, middle, high = (
            row["prior_work_by_checkpoint"][str(threshold)]
            if model == "notice_only" and threshold is not None
            else row["work"][model]
        )
        errors.append(abs(middle - row["remaining_tokens"]))
        cover.append(low <= row["remaining_tokens"] <= high)
        widths.append(high - low)
        if row["scores"][model] >= .5:
            accepted_errors.append(abs(middle - row["remaining_tokens"]))
        if row["future_decode_interval_ms"] is not None:
            service_errors.append(abs(
                middle * row["observed_decode_ms_per_token"]
                - row["future_decode_interval_ms"]
            ))
            waits.append(row["future_no_service_ms"])
    return {
        "snapshots": len(records),
        "natural_return_snapshots": int(sum(truth)),
        "tool_snapshots": int(sum(~truth)),
        "selected": true + false,
        "precision": true / (true + false) if true + false else None,
        "return_recall": true / int(sum(truth)) if any(truth) else None,
        "tool_false_positive_rate": false / int(sum(~truth)) if any(~truth) else None,
        "uncalibrated_brier": float(np.mean((scores - truth) ** 2)) if len(truth) else None,
        "conditional_work_count": len(work),
        "remaining_token_mae": float(np.mean(errors)) if errors else None,
        "remaining_token_median_absolute_error": float(np.median(errors)) if errors else None,
        "selected_natural_return_work_count": len(accepted_errors),
        "selected_natural_return_token_mae": (
            float(np.mean(accepted_errors)) if accepted_errors else None
        ),
        "nominal_p10_p90_work_coverage": float(np.mean(cover)) if cover else None,
        "nominal_p10_p90_mean_width_tokens": float(np.mean(widths)) if widths else None,
        "active_decode_interval_mae_ms": float(np.mean(service_errors)) if service_errors else None,
        "unmodeled_wait_p50_ms": float(np.median(waits)) if waits else None,
    }


def evaluate(
    current: list[dict], extra: list[dict], output: Path, *, epochs: int,
    balance_phases: bool = False,
) -> tuple[dict, list[dict]]:
    records, folds = [], {}
    for project in sorted({row["project"] for row in current}):
        train = [row for row in (*current, *extra) if row["project"] != project]
        held = [row for row in current if row["project"] == project]
        if {row["observation"].request_id for row in train} & {
            row["observation"].request_id for row in held
        }:
            raise ValueError("training/evaluation request identity overlap")
        models, timings = {}, {}
        for name, with_events in (("content_progress", False), ("content_events", True)):
            model, timing = fit(
                train, with_events=with_events, epochs=epochs, seed=21,
                balance_phases=balance_phases,
            )
            models[name] = model
            model.save(
                output / f"{project}-{name}.pt",
                training_projects=[row["project"] for row in train],
            )
            observations = [row["observation"] for row in held]
            for _ in range(10):
                model.predict(observations[:1])
            costs = []
            for i in range(100):
                start = time.perf_counter_ns()
                model.predict(observations[i % len(observations):i % len(observations) + 1])
                costs.append((time.perf_counter_ns() - start) / 1e6)
            timing["single_observation_cpu_p50_ms"] = float(np.median(costs))
            timing["single_observation_cpu_p95_ms"] = float(np.quantile(costs, .95))
            timings[name] = timing
        predictions = {
            name: model.predict([row["observation"] for row in held])
            for name, model in models.items()
        }
        priors = {}
        for threshold in CHECKPOINTS:
            values = [
                row["remaining_tokens"] for row in train
                if threshold in row["thresholds"] and row["remaining_tokens"] is not None
            ]
            priors[threshold] = tuple(float(v) for v in np.quantile(values, (.1, .5, .9)))
        for i, row in enumerate(held):
            observation = row["observation"]
            work_prior = priors[row["thresholds"][0]]
            records.append({
                **{key: value for key, value in row.items() if key != "observation"},
                "request_id": observation.request_id,
                "invocation_id": observation.invocation_id,
                "snapshot_ts_ms": observation.ts_ms,
                "observed_output_tokens": observation.observed_output_tokens,
                "notice_active": observation.notice_active,
                "prior_work_by_checkpoint": {
                    str(threshold): value for threshold, value in priors.items()
                },
                "scores": {
                    **{name: value[i].final_report_score for name, value in predictions.items()},
                    "notice_only": float(observation.notice_active),
                },
                "phase_scores": {
                    name: {
                        "final_report": value[i].final_report_score,
                        "completion_notice": value[i].completion_notice_score,
                        "continue_work": max(
                            0., 1. - value[i].final_report_score
                            - value[i].completion_notice_score,
                        ),
                    } for name, value in predictions.items()
                },
                "predicted_phases": {
                    name: value[i].predicted_phase
                    for name, value in predictions.items()
                },
                "work": {
                    **{name: value[i].conditional_remaining_tokens
                       for name, value in predictions.items()},
                    "notice_only": work_prior,
                },
            })
        folds[project] = {
            "training_projects": sorted({row["project"] for row in train}),
            "held_snapshots": len(held), "models": timings,
        }
    report = {
        "status": "offline_project_held_out_phase_and_work_diagnostic",
        "folds": folds,
        "fixed_stage_score_threshold": 0.5,
        "training_phase_balance": balance_phases,
        "by_checkpoint": {
            str(threshold): {
                name: quality(
                    [row for row in records if threshold in row["thresholds"]],
                    name, threshold=threshold,
                )
                for name in ("notice_only", "content_progress", "content_events")
            }
            for threshold in CHECKPOINTS
        },
    }
    def first_trigger_quality(name: str, score_cut: float) -> dict:
        grouped = defaultdict(list)
        for row in records:
            grouped[row["request_id"]].append(row)
        selected = []
        for group in grouped.values():
            trigger = next((
                row for row in sorted(group, key=lambda item: item["snapshot_ts_ms"])
                if row["scores"][name] >= score_cut
            ), None)
            if trigger:
                selected.append(trigger)
        positives = sum(group[0]["is_return"] for group in grouped.values())
        true = [row for row in selected if row["is_return"]]
        return {
            "request_denominator": len(grouped), "natural_returns": positives,
            "true_first_triggers": len(true),
            "tool_false_first_triggers": len(selected) - len(true),
            "return_recall": len(true) / positives if positives else None,
            "precision": len(true) / len(selected) if selected else None,
            "actual_lead_at_least_500ms": sum(
                row["remaining_client_wall_ms"] >= 500 for row in true
            ),
            "actual_lead_p50_ms": float(np.median([
                row["remaining_client_wall_ms"] for row in true
            ])) if true else None,
        }
    names = ("notice_only", "content_progress", "content_events")
    report["rolling_first_trigger"] = {
        name: first_trigger_quality(name, .5) for name in names
    }
    report["fixed_score_operating_curves"] = {
        str(cut): {
            name: first_trigger_quality(name, cut) for name in names
        }
        for cut in (.5, .8, .95)
    }
    report["baseline_work_semantics"] = (
        "notice_only uses a training-only checkpoint work prior, not the child hint"
    )
    report["no_runtime_guard_or_threshold_added"] = True
    report["three_phase_confusion"] = {}
    for name in ("content_progress", "content_events"):
        matrix = [[0] * len(PHASES) for _ in PHASES]
        for row in records:
            matrix[row["phase_label"]][PHASES.index(row["predicted_phases"][name])] += 1
        report["three_phase_confusion"][name] = {
            "labels": PHASES, "actual_rows_predicted_columns": matrix,
        }
    return report, records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--extra-train-run", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--balance-phases", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    if args.epochs < 1:
        raise ValueError("epochs must be positive")
    torch.set_num_threads(1)
    current, current_coverage = collect_run(args.run)
    extra, extra_coverage = [], {}
    for run in args.extra_train_run:
        samples, extra_coverage[str(run)] = collect_run(run)
        extra.extend(samples)
    args.output.mkdir(parents=True)
    report, records = evaluate(
        current, extra, args.output, epochs=args.epochs,
        balance_phases=args.balance_phases,
    )
    report["current_coverage"] = current_coverage
    report["extra_training_coverage"] = extra_coverage
    report["semantics"] = (
        "Stage scores are uncalibrated, not H2D permits. Work targets are final "
        "server output-token count minus last causally observed same-request "
        "count, not RETURN wall time. Work errors are oracle-RETURN conditional "
        "and must be read together with tool false triggers. Scheduler/worker "
        "decode intervals are not CUDA kernel time; future no-service gaps are "
        "offline accounting only, never model inputs. Only delivered-text "
        "rounds with verified active service are eligible; coverage includes "
        "silent tool rounds and other excluded observations. No physical "
        "migration or online end-to-end benefit is demonstrated."
    )
    (args.output / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    with (args.output / "rows.jsonl").open("w", encoding="utf-8") as stream:
        for row in records:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(report["rolling_first_trigger"], indent=2))


if __name__ == "__main__":
    main()
