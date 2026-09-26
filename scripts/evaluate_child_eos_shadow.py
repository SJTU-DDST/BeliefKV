#!/usr/bin/env python3
"""Evaluate delivered EOS top-logprob cues without future-text features."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import median

try:
    from beliefkv.runtime.eos_shadow import (
        EOS_LOW_PROB_THRESHOLDS, EOS_PROB_THRESHOLDS,
    )
    from scripts.evaluate_child_intent_stream_milestones import collect
    from scripts.evaluate_child_return_intent_timing import _metrics
except ModuleNotFoundError:
    from beliefkv.runtime.eos_shadow import (
        EOS_LOW_PROB_THRESHOLDS, EOS_PROB_THRESHOLDS,
    )
    from evaluate_child_intent_stream_milestones import collect
    from evaluate_child_return_intent_timing import _metrics


def load(
    workflows: Path,
    thresholds: tuple[float, ...] = EOS_PROB_THRESHOLDS,
) -> tuple[list[dict], dict]:
    if any(threshold in EOS_LOW_PROB_THRESHOLDS for threshold in thresholds):
        manifest = workflows.parent / "manifest.json"
        if (
            not manifest.is_file()
            or not (json.loads(manifest.read_text(encoding="utf-8"))
                    .get("config") or {}).get("child_eos_low_prob_shadow")
        ):
            raise ValueError(f"low-probability EOS was not collected in {workflows}")
    stages, counts = collect(
        workflows, 0 if any(
            t in EOS_LOW_PROB_THRESHOLDS for t in thresholds
        ) else 64,
    )
    events_by_task = {
        path.parent.name: [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        for path in workflows.glob("*/runtime_events.deepagents.jsonl")
    }
    for row in stages:
        events = events_by_task[row["task_id"]]
        earliest_cue_ts = (
            row.get("observed_first_content_ts_ms")
            if any(t in EOS_LOW_PROB_THRESHOLDS for t in thresholds)
            and row.get("observed_first_content_ts_ms") is not None
            else row["signal_ts_ms"]
        )
        result = next((
            event for event in events
            if event.get("kind") == "llm_result"
            and event.get("invocation_id") == row["invocation_id"]
            and (event.get("attributes") or {}).get("request_id") == row["request_id"]
        ), None)
        attrs = (result or {}).get("attributes") or {}
        row["scored_tokens"] = attrs.get("eos_shadow_scored_tokens")
        row["top_hits"] = attrs.get("eos_shadow_top_hits")
        stage_64 = [
            float(event["ts_ms"]) for event in events
            if event.get("kind") == "structured_action"
            and (event.get("attributes") or {}).get(
                "beliefkv_child_substantial_content_shadow"
            )
            and (event.get("attributes") or {}).get("content_threshold_chars") == 64
            and (event.get("attributes") or {}).get("request_id") == row["request_id"]
            and event.get("invocation_id") == row["invocation_id"]
            and (event.get("context_id"), event.get("context_epoch"))
            == (row["context_id"], row["context_epoch"])
        ]
        row["first_64_ts_ms"] = min(stage_64) if stage_64 else None
        row["first_eos_ts"] = {}
        for event in events:
            attrs = event.get("attributes") or {}
            threshold = attrs.get("eos_top_probability_threshold")
            if (
                event.get("kind") != "structured_action"
                or not attrs.get("beliefkv_child_eos_shadow")
                or type(threshold) not in {int, float}
                or threshold not in thresholds
                or attrs.get("request_id") != row["request_id"]
                or event.get("invocation_id") != row["invocation_id"]
                or (event.get("context_id"), event.get("context_epoch"))
                != (row["context_id"], row["context_epoch"])
                or float(event["ts_ms"]) < earliest_cue_ts
            ):
                continue
            ts = float(event["ts_ms"])
            current = row["first_eos_ts"].get(threshold)
            if current is None or ts < current:
                row["first_eos_ts"][threshold] = ts
    return stages, counts


def _task_median(rows: list[dict], key: str) -> float:
    values = defaultdict(list)
    for row in rows:
        values[row["task_id"]].append(row[key])
    return median(median(group) for group in values.values())


def score(train: list[dict], test: list[dict], threshold: float) -> dict:
    train_stage = [row for row in train if row["label"] == "true"]
    if not train_stage:
        raise ValueError("no natural training stage returns")
    stage_prior = _task_median(train_stage, "lead_ms")
    first_content_train = [
        {
            **row,
            "first_content_lead_ms": row["return_ts_ms"]
            - row["observed_first_content_ts_ms"],
        }
        for row in train_stage
        if row.get("observed_first_content_ts_ms") is not None
    ]
    first_content_prior = (
        _task_median(first_content_train, "first_content_lead_ms")
        if first_content_train else None
    )
    train_cues = [
        {**row, "cue_lead": row["return_ts_ms"] - row["first_eos_ts"][threshold]}
        for row in train_stage if threshold in row["first_eos_ts"]
        and row["return_ts_ms"] > row["first_eos_ts"][threshold]
    ]
    cue_prior = _task_median(train_cues, "cue_lead") if train_cues else None
    observed = [row for row in test if threshold in row["first_eos_ts"]]
    natural = [
        row for row in observed if row["label"] == "true"
        and row["return_ts_ms"] > row["first_eos_ts"][threshold]
    ]

    def timing(rows: list[dict]) -> dict:
        actual = [
            row["return_ts_ms"] - row["first_eos_ts"][threshold] for row in rows
        ]
        after_stage = [
            row for row in rows
            if row.get("stage_threshold_chars", 64) == 64
            and row["first_eos_ts"][threshold] >= row["signal_ts_ms"]
        ]
        from_content = [
            row for row in rows
            if row.get("observed_first_content_ts_ms") is not None
            and row["first_eos_ts"][threshold]
            >= row["observed_first_content_ts_ms"]
        ]
        return {
            "train_eos_prior": (
                _metrics(actual, [cue_prior] * len(rows))
                if cue_prior is not None else None
            ),
            "train_first_64_prior_at_cue": _metrics([
                row["return_ts_ms"] - row["first_eos_ts"][threshold]
                for row in after_stage
            ], [
                max(0., stage_prior - (
                    row["first_eos_ts"][threshold] - row["signal_ts_ms"]
                )) for row in after_stage
            ]),
            "train_first_content_prior_at_cue": (
                _metrics([
                    row["return_ts_ms"] - row["first_eos_ts"][threshold]
                    for row in from_content
                ], [
                    max(0., first_content_prior - (
                        row["first_eos_ts"][threshold]
                        - row["observed_first_content_ts_ms"]
                    )) for row in from_content
                ]) if first_content_prior is not None else None
            ),
            "immediate_return_at_cue": _metrics(actual, [0.] * len(rows)),
        }

    return {
        "eligible_stage": len(test),
        "eligible_first_64_stage": sum(
            row.get("stage_threshold_chars", 64) == 64 for row in test
        ),
        "eligible_first_content_stage": sum(
            row.get("stage_threshold_chars") == 0 for row in test
        ),
        "stage_threshold_chars": (
            test[0].get("stage_threshold_chars", 64) if test else None
        ),
        "natural_stage": sum(row["label"] == "true" for row in test),
        "scored_tokens_available": sum(
            type(row["scored_tokens"]) is int and row["scored_tokens"] > 0
            for row in test
        ),
        "eos_top_hit_available": sum(
            type(row["top_hits"]) is int and row["top_hits"] > 0
            for row in test
        ),
        "first_trigger": len(observed),
        "first_trigger_before_64_chars": sum(
            (
                row.get("first_64_ts_ms", row["signal_ts_ms"])
                is None
                or row["first_eos_ts"][threshold]
                < row.get("first_64_ts_ms", row["signal_ts_ms"])
            )
            for row in observed
        ),
        "first_trigger_labels": dict(Counter(row["label"] for row in observed)),
        "after_return_trigger": sum(
            row["label"] == "true"
            and row["first_eos_ts"][threshold] >= row["return_ts_ms"]
            for row in observed
        ),
        "natural_first_trigger": len(natural),
        "natural_without_trigger": sum(
            row["label"] == "true" and threshold not in row["first_eos_ts"]
            for row in test
        ),
        "natural_trigger_lead_at_least_500ms": sum(
            row["return_ts_ms"] - row["first_eos_ts"][threshold] >= 500
            for row in natural
        ),
        "natural_trigger_lead_at_least_2000ms": sum(
            row["return_ts_ms"] - row["first_eos_ts"][threshold] >= 2000
            for row in natural
        ),
        "natural_join_last_trigger": sum(row["join_last"] for row in natural),
        "train_trigger_rows": len(train_cues),
        "train_trigger_workflows": len({row["task_id"] for row in train_cues}),
        "train_eos_prior_ms": cue_prior,
        "return_timing_same_triggers": timing(natural),
        "join_last_timing_same_triggers": timing([
            row for row in natural if row["join_last"]
        ]),
    }


def audit(
    workflows: Path,
    thresholds: tuple[float, ...] = EOS_PROB_THRESHOLDS,
) -> dict:
    rows, counts = load(workflows, thresholds)
    by_threshold = {}
    for threshold in thresholds:
        triggered = [
            row for row in rows if threshold in row["first_eos_ts"]
        ]
        natural = [
            row for row in triggered if row["label"] == "true"
            and row["first_eos_ts"][threshold] < row["return_ts_ms"]
        ]
        leads = [
            row["return_ts_ms"] - row["first_eos_ts"][threshold]
            for row in natural
        ]
        by_threshold[str(threshold)] = {
            "first_trigger": len(triggered),
            "natural_first_trigger": len(natural),
            "false_first_trigger": sum(
                row["label"] == "false" for row in triggered
            ),
            "censored_first_trigger": sum(
                row["label"] == "censored" for row in triggered
            ),
            "after_return_trigger": sum(
                row["label"] == "true"
                and row["first_eos_ts"][threshold] >= row["return_ts_ms"]
                for row in triggered
            ),
            "natural_with_500ms_lead": sum(lead >= 500 for lead in leads),
            "natural_with_2000ms_lead": sum(lead >= 2000 for lead in leads),
            "natural_join_last_trigger": sum(row["join_last"] for row in natural),
            "natural_lead_ms": (
                {"min": min(leads), "median": median(leads), "max": max(leads)}
                if leads else None
            ),
        }
    return {
        "diagnostic_only": True,
        "collector": counts,
        "stage_threshold_chars": (
            0 if any(t in EOS_LOW_PROB_THRESHOLDS for t in thresholds) else 64
        ),
        "eligible_stage": len(rows),
        "eligible_first_64_stage": sum(
            row.get("stage_threshold_chars", 64) == 64 for row in rows
        ),
        "eligible_first_content_stage": sum(
            row.get("stage_threshold_chars") == 0 for row in rows
        ),
        "labels": dict(Counter(row["label"] for row in rows)),
        "observed_child_returns_total": counts["observed_child_returns_total"],
        "observed_join_last_total": counts["observed_join_last_total"],
        "natural_child_returns_total": counts["natural_child_returns_total"],
        "natural_join_last_total": counts["natural_join_last_total"],
        "natural_join_last": sum(
            row["join_last"] and row["label"] == "true" for row in rows
        ),
        "thresholds": by_threshold,
        "eos_scored_tokens_available": sum(
            type(row["scored_tokens"]) is int and row["scored_tokens"] > 0
            for row in rows
        ),
        "eos_top_hit_available": sum(
            type(row["top_hits"]) is int and row["top_hits"] > 0
            for row in rows
        ),
        "scope": (
            "Notice-bound first 64-character stages only; no causal fit or "
            "physical transfer. A top-k miss is not zero EOS probability."
        ),
    }


def evaluate(
    train_roots: list[Path], heldout_root: Path,
    thresholds: tuple[float, ...] = EOS_PROB_THRESHOLDS,
) -> dict:
    train, train_info, tasks, projects = [], {}, set(), set()
    for root in train_roots:
        rows, counts = load(root, thresholds)
        ids = {row["task_id"] for row in rows}
        if ids & tasks:
            raise ValueError("training workflows overlap")
        tasks |= ids
        projects |= {row["project"] for row in rows}
        train.extend(rows)
        train_info[str(root)] = counts
    heldout, heldout_info = load(heldout_root, thresholds)
    eval_projects = {row["project"] for row in heldout}
    if (
        not projects or not eval_projects or projects & eval_projects
        or tasks & {row["task_id"] for row in heldout}
    ):
        raise ValueError("train and held-out projects/tasks must be disjoint")
    return {
        "diagnostic_only": True,
        "train_projects": sorted(projects),
        "heldout_projects": sorted(eval_projects),
        "train_collectors": train_info,
        "heldout_collector": heldout_info,
        "thresholds": {
            str(threshold): {
                "evaluation": score(train, heldout, threshold),
                "by_project": {
                    project: score(
                        train, [
                            row for row in heldout if row["project"] == project
                        ], threshold,
                    ) for project in sorted(eval_projects)
                },
            } for threshold in thresholds
        },
        "scope": (
            "Scores delivered unsampled EOS top-logprob crossings only after "
            "a notice-bound first 64-character stage. Priors use training "
            "projects exclusively. No action readiness or H2D benefit implied."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-workflows", type=Path)
    parser.add_argument("--train-workflows", type=Path, action="append")
    parser.add_argument("--heldout-workflows", type=Path)
    parser.add_argument(
        "--include-low-prob", action="store_true",
        help="Evaluate 0.01%%/0.1%% signals only when both splits collected them.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    thresholds = (
        EOS_LOW_PROB_THRESHOLDS + EOS_PROB_THRESHOLDS
        if args.include_low_prob else EOS_PROB_THRESHOLDS
    )
    if args.audit_workflows:
        if args.train_workflows or args.heldout_workflows:
            parser.error("--audit-workflows cannot be combined with evaluation")
        result = audit(args.audit_workflows, thresholds)
    else:
        if not args.train_workflows or args.heldout_workflows is None:
            parser.error("evaluation requires train and held-out workflows")
        result = evaluate(
            args.train_workflows, args.heldout_workflows, thresholds,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
