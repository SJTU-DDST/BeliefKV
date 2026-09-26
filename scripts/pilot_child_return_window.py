#!/usr/bin/env python3
"""Project-isolated, causal screen for a short child RETURN transfer window.

This is an offline development probe. A positive label means a natural RETURN
is 500-3000 ms away at the snapshot; it does not authorize physical prefetch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from statistics import median

import numpy as np

if __package__:
    from scripts.pilot_child_terminal_threshold import fit_head
    from scripts.pilot_hidden_eta_project_split import features, fit_ridge
    from scripts.pilot_real_child_hidden_eta import (
        at_stage, content_cues, first_features, load_batch_records,
    )
else:
    from pilot_child_terminal_threshold import fit_head
    from pilot_hidden_eta_project_split import features, fit_ridge
    from pilot_real_child_hidden_eta import (
        at_stage, content_cues, first_features, load_batch_records,
    )


STAGE_TOKENS = 512
TARGET_START_MS = 500
TARGET_END_MS = 3000
THRESHOLDS = tuple(round(i / 100, 2) for i in range(50, 96, 5))
CONFIRMATION_DELAYS_MS = (0, 250, 500, 750, 1000, 1500, 2000)


def window_cues(workflows: Path) -> dict[str, dict[str, float]]:
    cues = content_cues(workflows)
    for path in workflows.glob("*/runtime_events.deepagents.jsonl"):
        requests = {}
        invalidations = {}
        contexts = {}
        context_events = {}
        submitted = {}
        with path.open() as stream:
            for line in stream:
                event = json.loads(line)
                kind = event.get("kind")
                invocation = event.get("invocation_id")
                ts = event.get("ts_ms")
                context_id = event.get("context_id")
                epoch = event.get("context_epoch")
                attrs = event.get("attributes") or {}
                rid = attrs.get("request_id")
                if kind in {"llm_submit", "llm_result"} and rid and invocation:
                    requests[rid] = invocation
                    if kind == "llm_submit":
                        submitted[rid] = min(
                            ts, submitted.get(rid, float("inf")),
                        )
                    if context_id is not None:
                        contexts.setdefault(rid, (context_id, epoch))
                    if kind == "llm_result":
                        cue = cues.setdefault(rid, {})
                        cue["result"] = min(ts, cue.get("result", float("inf")))
                if kind in {"return", "invocation_cancel"} and invocation:
                    invalidations.setdefault(invocation, []).append(ts)
                if invocation and context_id is not None:
                    context_events.setdefault(invocation, []).append(
                        (ts, context_id, epoch),
                    )
        for rid, invocation in requests.items():
            if invocation in invalidations:
                earliest = min(
                    (ts for ts in invalidations[invocation]
                     if ts >= submitted.get(rid, float("inf"))),
                    default=float("inf"),
                )
                if earliest < float("inf"):
                    cue = cues.setdefault(rid, {})
                    cue["invalidated"] = min(
                        earliest, cue.get("invalidated", float("inf")),
                    )
            if rid in contexts:
                context_id, epoch = contexts[rid]
                other_epoch = (
                    ts for ts, observed_id, observed_epoch
                    in context_events.get(invocation, [])
                    if ts >= submitted.get(rid, float("inf"))
                    if observed_id != context_id or observed_epoch != epoch
                )
                earliest = min(other_epoch, default=float("inf"))
                if earliest < float("inf"):
                    cue = cues.setdefault(rid, {})
                    cue["invalidated"] = min(
                        earliest, cue.get("invalidated", float("inf")),
                    )
    return cues


def score(model: tuple, samples: list[tuple]) -> np.ndarray:
    mean, scale, weights, intercept = model
    return np.clip(
        (features(samples, True) - mean) / scale @ weights + intercept,
        0, 1,
    )


def restrict_to_live_delivery(
    records: list[dict], delivery_path: Path,
) -> tuple[list[dict], dict]:
    """Use only samples the host actually received before request completion."""
    delivered = {}
    with delivery_path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if "received_monotonic_ns" not in row:
                raise ValueError("live replay requires actual receiver timestamps")
            key = (row["request_sha256"], int(row["token_count"]))
            if key in delivered:
                raise ValueError("duplicate live child snapshot")
            delivered[key] = row
    retained = []
    missed = late = observed = 0
    for record in records:
        digest = hashlib.sha256(record["rid"].encode()).hexdigest()
        samples = []
        for ordinal, elapsed_ms, _lead, vector in record["samples"]:
            row = delivered.get((digest, ordinal * 32))
            if row is None:
                missed += 1
                continue
            received_ms = int(row["received_monotonic_ns"]) / 1e6
            sent_ms = record["first_arrival_ms"] + elapsed_ms
            if abs(received_ms - sent_ms - float(row["transport_age_ms"])) > .002:
                raise ValueError("live snapshot time does not match NPZ sample")
            if (record["terminal"]
                    and received_ms >= record["return_ms"]):
                late += 1
                continue
            samples.append((
                ordinal, received_ms - record["first_arrival_ms"],
                record["return_ms"] - received_ms if record["terminal"] else None,
                vector,
            ))
            observed += 1
        if samples:
            retained.append({**record, "samples": samples})
    return retained, {
        "received_retained_samples": observed,
        "missing_retained_samples": missed,
        "after_child_return": late,
        "eligible_rounds_with_delivery": len(retained),
        "delivery_rows": len(delivered),
        "clock": "receiver_monotonic_ns",
    }


def causal_observations(record: dict, cues: dict) -> list[tuple[float, tuple]]:
    cue = cues.get(record["rid"], {})
    content_ts = cue.get("content")
    if content_ts is None:
        return []
    end_ts = min(
        cue.get("tool", float("inf")),
        cue.get("result", float("inf")),
        cue.get("invalidated", float("inf")),
    )
    first_arrival = record["first_arrival_ms"]
    observations = []
    previous = None
    for sample in record["samples"]:
        sample_ts = first_arrival + sample[1]
        if sample_ts < content_ts:
            previous = sample
            continue
        if previous is not None:
            if content_ts < end_ts:
                lead = (
                    record["return_ms"] - content_ts if record["terminal"]
                    else None
                )
                observations.append((
                    content_ts,
                    (previous[0], previous[1], lead, previous[3]),
                ))
            previous = None
        if sample_ts >= end_ts:
            break
        lead = record["return_ms"] - sample_ts if record["terminal"] else None
        observations.append((
            sample_ts, (sample[0], sample[1], lead, sample[3]),
        ))
    if previous is not None and content_ts < end_ts:
        lead = record["return_ms"] - content_ts if record["terminal"] else None
        observations.append((
            content_ts, (previous[0], previous[1], lead, previous[3]),
        ))
    return observations


def fit_window(records: list[dict], cues: dict) -> tuple:
    positive = []
    negative_nonterminal = []
    negative_timing = []
    for record in records:
        # The most recent pre-content state first becomes usable at content arrival.
        for index, (_, sample) in enumerate(causal_observations(record, cues)):
            if index >= 8 and index % 4:
                continue
            lead = sample[2]
            if record["terminal"] and TARGET_START_MS <= lead <= TARGET_END_MS:
                positive.append(sample)
            elif not record["terminal"]:
                negative_nonterminal.append(sample)
            elif lead < TARGET_START_MS or lead > TARGET_END_MS:
                negative_timing.append(sample)
    if len(positive) < 10 or not negative_timing:
        raise ValueError("too few observable positive/negative window labels")
    random.Random(61).shuffle(negative_nonterminal)
    random.Random(67).shuffle(negative_timing)
    # Keep both wrong-round and wrong-time negatives in every fit.
    limit = 3 * len(positive)
    negatives = (
        negative_nonterminal[:limit] + negative_timing[:limit]
    )
    examples = positive + negatives
    random.Random(71).shuffle(examples)
    labels = np.asarray(
        [float(TARGET_START_MS <= sample[2] <= TARGET_END_MS)
         if sample[2] is not None else 0. for sample in examples],
    )
    return fit_ridge(features(examples, True), labels)


def scored_sequences(
    records: list[dict], terminal_head: tuple, window_head: tuple,
    cues: dict,
) -> list[dict]:
    if not records:
        return []
    mean, scale, weights, intercept = terminal_head
    stage_scores = np.clip(
        (first_features(records, True) - mean) / scale @ weights + intercept,
        0, 1,
    )
    sequences = []
    for record, stage_score in zip(records, stage_scores):
        if stage_score < 0.5:
            continue
        eligible = causal_observations(record, cues)
        if not eligible:
            continue
        scores = score(window_head, [sample for _, sample in eligible])
        observations = [
            (sample_ts, float(value))
            for (sample_ts, _), value in zip(eligible, scores)
        ]
        if observations:
            sequences.append({
                "project": record["project"],
                "rid": record["rid"],
                "terminal": record["terminal"],
                "return_ms": record["return_ms"],
                "join_last": record.get("join_last", False),
                "observations": observations,
                "tool_ms": cues.get(record["rid"], {}).get("tool", float("inf")),
                "result_ms": cues.get(record["rid"], {}).get("result"),
                "invalidated_ms": cues.get(record["rid"], {}).get(
                    "invalidated", float("inf"),
                ),
            })
    return sequences


def report(
    sequences: list[dict], threshold: float, confirmation_samples: int = 1,
    delay_ms: int = 0,
) -> dict:
    if confirmation_samples < 1:
        raise ValueError("confirmation_samples must be positive")
    if delay_ms < 0:
        raise ValueError("delay_ms must be nonnegative")
    leads = []
    join_leads = []
    false_rounds = 0
    cancelled_tool = cancelled_result = cancelled_identity = missing_result = 0
    projects = set()
    for sequence in sequences:
        candidate = next(
            (
                at for index, (at, value) in enumerate(sequence["observations"])
                if index + 1 >= confirmation_samples
                and value >= threshold
                and all(
                    previous >= threshold for _, previous in
                    sequence["observations"][
                        index + 1 - confirmation_samples:index
                    ]
                )
            ),
            None,
        )
        if candidate is None:
            continue
        trigger = candidate + delay_ms
        if delay_ms:
            # An absent end event cannot certify that the request still exists
            # when the confirmation timer fires.
            result_ms = sequence.get("result_ms")
            if result_ms is None:
                missing_result += 1
                continue
            first_end = min(
                (sequence.get("tool_ms", float("inf")), "tool"),
                (result_ms, "result"),
                (sequence.get("invalidated_ms", float("inf")), "identity"),
            )
            if first_end[0] <= trigger:
                if first_end[1] == "tool":
                    cancelled_tool += 1
                elif first_end[1] == "result":
                    cancelled_result += 1
                else:
                    cancelled_identity += 1
                continue
        projects.add(sequence["project"])
        if sequence["terminal"]:
            lead = sequence["return_ms"] - trigger
            leads.append(lead)
            if sequence["join_last"]:
                join_leads.append(lead)
        else:
            false_rounds += 1
    total = len(leads) + false_rounds
    in_window = sum(TARGET_START_MS <= x <= TARGET_END_MS for x in leads)
    return {
        "threshold": threshold,
        "confirmation_samples": confirmation_samples,
        "delay_ms": delay_ms,
        "first_trigger_count": total,
        "projects_with_triggers": len(projects),
        "nonterminal_false_triggers": false_rounds,
        "natural_return_triggers": len(leads),
        "window_true_triggers": in_window,
        "over_3000ms": sum(x > TARGET_END_MS for x in leads),
        "under_500ms": sum(x < TARGET_START_MS for x in leads),
        "join_last_window_triggers": sum(
            TARGET_START_MS <= x <= TARGET_END_MS for x in join_leads
        ),
        "join_last_triggers": len(join_leads),
        "cancelled_tool": cancelled_tool,
        "cancelled_result": cancelled_result,
        "cancelled_identity": cancelled_identity,
        "missing_result": missing_result,
        "window_precision": round(in_window / total, 4) if total else None,
        "lead_p50_ms": round(median(leads), 2) if leads else None,
    }


def select_threshold(sequences: list[dict]) -> tuple[float, int] | None:
    eligible = []
    for samples in (1, 2, 3):
        for threshold in THRESHOLDS:
            result = report(sequences, threshold, samples)
            if (
                result["window_true_triggers"] >= 8
                and result["projects_with_triggers"] >= 2
                and result["window_precision"] is not None
                and result["window_precision"] >= 0.90
            ):
                eligible.append((result["window_true_triggers"], threshold, samples))
    if not eligible:
        return None
    _, threshold, samples = max(eligible, key=lambda row: (row[0], row[1], -row[2]))
    return threshold, samples


def select_delayed_rule(
    sequences: list[dict],
) -> tuple[float, int, int] | None:
    eligible = []
    for delay_ms in CONFIRMATION_DELAYS_MS:
        for samples in (1, 2, 3):
            for threshold in THRESHOLDS:
                result = report(sequences, threshold, samples, delay_ms)
                if (
                    result["window_true_triggers"] >= 8
                    and result["projects_with_triggers"] >= 2
                    and result["window_precision"] is not None
                    and result["window_precision"] >= 0.90
                ):
                    eligible.append((
                        result["window_true_triggers"],
                        threshold, samples, delay_ms,
                    ))
    if not eligible:
        return None
    _, threshold, samples, delay_ms = max(
        eligible, key=lambda row: (row[0], row[1], -row[2], -row[3]),
    )
    return threshold, samples, delay_ms


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-tokens", type=int, default=STAGE_TOKENS)
    parser.add_argument("--train-traces", type=Path, action="append", required=True)
    parser.add_argument("--heldout-traces", type=Path, required=True)
    parser.add_argument("--train-workflows", type=Path, action="append", required=True)
    parser.add_argument("--heldout-workflows", type=Path, action="append", required=True)
    parser.add_argument(
        "--exclude-train-project", action="append", default=[],
        help="Exclude a project from fit and threshold selection before evaluation.",
    )
    parser.add_argument(
        "--heldout-live-delivery", type=Path,
        help="Require actual host delivery time for heldout snapshots; diagnostic only.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.stage_tokens < 32 or args.stage_tokens % 32:
        parser.error("stage-tokens must be a positive multiple of 32")
    if len(args.train_traces) != len(args.train_workflows):
        parser.error("provide one --train-traces per --train-workflows, in the same order")
    train, train_counts = load_batch_records(
        args.train_workflows, args.train_traces,
    )
    excluded = set(args.exclude_train_project)
    train = [
        record for record in train
        if record["project"] not in excluded
    ]
    heldout, heldout_counts = load_batch_records(
        args.heldout_workflows, args.heldout_traces,
    )
    live_counts = None
    if args.heldout_live_delivery:
        heldout, live_counts = restrict_to_live_delivery(
            heldout, args.heldout_live_delivery,
        )
        if not heldout:
            parser.error("heldout had no matched live snapshots")
    train_projects = {row["project"] for row in train}
    heldout_projects = {row["project"] for row in heldout}
    if train_projects & heldout_projects or len(train_projects) < 3:
        parser.error("need at least three training projects and a disjoint heldout project")
    stage_train = at_stage(train, args.stage_tokens)
    train_cues = {}
    for root in args.train_workflows:
        train_cues.update(window_cues(root))
    folds = []
    for project in sorted(train_projects):
        fit = [row for row in stage_train if row["project"] != project]
        validation = [row for row in stage_train if row["project"] == project]
        folds.extend(scored_sequences(
            validation, fit_head(fit), fit_window(fit, train_cues), train_cues,
        ))
    selected = select_threshold(folds)
    delayed = select_delayed_rule(folds)
    result = {
        "diagnostic_only": True,
        "train_projects": sorted(train_projects),
        "excluded_train_projects": sorted(excluded),
        "heldout_projects": sorted(heldout_projects),
        "train": train_counts,
        "used_train_rounds_after_exclusion": len(train),
        "used_train_terminal_rounds_after_exclusion": sum(
            record["terminal"] for record in train
        ),
        "heldout": heldout_counts,
        "heldout_live_delivery": live_counts,
        "stage_tokens": args.stage_tokens,
        "window_ms": [TARGET_START_MS, TARGET_END_MS],
        "threshold_chosen_on_train_project_cv": selected,
        "delayed_rule_chosen_on_train_project_cv": delayed,
        "project_cv": {
            str(samples): {
                str(value): report(folds, value, samples) for value in THRESHOLDS
            } for samples in (1, 2, 3)
        },
        "heldout_at_frozen_threshold": None,
        "heldout_at_frozen_delayed_rule": None,
    }
    if selected is not None or delayed is not None:
        heldout_cues = {}
        for root in args.heldout_workflows:
            heldout_cues.update(window_cues(root))
        sequences = scored_sequences(
            at_stage(heldout, args.stage_tokens),
            fit_head(stage_train), fit_window(stage_train, train_cues),
            heldout_cues,
        )
        if selected is not None:
            result["heldout_at_frozen_threshold"] = report(sequences, *selected)
        if delayed is not None:
            result["heldout_at_frozen_delayed_rule"] = report(sequences, *delayed)
    result["delayed_project_cv"] = {
        str(delay_ms): {
            str(samples): {
                str(value): report(folds, value, samples, delay_ms)
                for value in THRESHOLDS
            } for samples in (1, 2, 3)
        } for delay_ms in CONFIRMATION_DELAYS_MS
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
