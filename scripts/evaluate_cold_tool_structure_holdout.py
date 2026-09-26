#!/usr/bin/env python3
"""Project-disjoint, read-only cold child execute timing ablation."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sys

import lightgbm as lgb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.audit_repeated_tool_timing import _quantile, _read_workflow


BUCKETS = (
    "nodes", "loops", "calls", "functions", "comprehensions", "exception_blocks"
)
MODES = ("class", "shape", "structure")
THRESHOLDS = (.2, .3, .5)
BEHAVIOR_INTERVENTIONS = frozenset({
    "agent_tool_duplicate_suppressed",
    "agent_guard_finalization_attempt",
    "agent_terminal_regular_tool_call_rejected",
})
PRE_TOOL_RECOVERY = "agent_empty_reasoning_retry"


def cold_calls(workflows: Path) -> tuple[list[dict], dict]:
    rows = []
    open_starts = 0
    completed_status = Counter()
    excluded_intervened = 0
    missing_input_chars = 0
    recovered_success = 0
    recovered_long = 0
    for path in sorted(workflows.glob("*/runtime_events.deepagents.jsonl")):
        interventions = {}
        recovered = {}
        audit = path.parent / "sandbox_audit.jsonl"
        if audit.exists():
            with audit.open(encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    event_name = event.get("event")
                    if (
                        event_name not in BEHAVIOR_INTERVENTIONS
                        and event_name != PRE_TOOL_RECOVERY
                    ):
                        continue
                    scope = str(event.get("agent_scope") or "")
                    invocation = scope.partition("deepagents-invocation:")[2]
                    if scope and not invocation:
                        continue
                    key = (
                        "deepagents-invocation:" + invocation
                        if invocation else "*"
                    )
                    ts = float(event["ts_ms"])
                    target = (
                        recovered if event_name == PRE_TOOL_RECOVERY
                        else interventions
                    )
                    target[key] = min(target.get(key, ts), ts)
        for row in _read_workflow(path):
            if row["is_child"] is not True or (
                row["previous"] is not None and row["previous"][2] == "success"
            ):
                continue
            completed_status[row["status"]] += 1
            if row["terminal_ts_ms"] >= min(
                interventions.get(row["invocation"], float("inf")),
                interventions.get("*", float("inf")),
            ):
                excluded_intervened += 1
                continue
            if row["status"] == "success":
                if type(row["input_chars"]) is int:
                    rows.append(row)
                    if min(
                        recovered.get(row["invocation"], float("inf")),
                        recovered.get("*", float("inf")),
                    ) < row["start_ts_ms"]:
                        recovered_success += 1
                        recovered_long += row["duration_ms"] >= 2_000
                else:
                    missing_input_chars += 1
        # Open child execute calls are never assumed to be short negatives.
        starts = {}
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                event = json.loads(line)
                attrs = event.get("attributes") or {}
                if attrs.get("tool_name") != "execute":
                    continue
                key = attrs.get("tool_call_id")
                if event.get("kind") == "tool_start" and attrs.get("is_child") is True:
                    starts[key] = event
                elif event.get("kind") == "tool_end":
                    starts.pop(key, None)
        open_starts += len(starts)
    return rows, {
        "open_child_execute_unlabeled": open_starts,
        "completed_cold_child_status": dict(sorted(completed_status.items())),
        "completed_cold_child_excluded_after_intervention": excluded_intervened,
        "successful_cold_child_missing_input_chars": missing_input_chars,
        "successful_cold_child_after_pre_tool_recovery": recovered_success,
        "long_success_after_pre_tool_recovery": recovered_long,
    }


def _features(rows: list[dict], mode: str, vocabulary: dict[str, int]) -> np.ndarray:
    result = np.zeros((len(rows), 2 + (len(BUCKETS) if mode == "structure" else 0)))
    for index, row in enumerate(rows):
        result[index, 0] = vocabulary.get(
            row["class"] if mode == "class" else row["shape"], -1
        )
        result[index, 1] = math.log1p(max(0, row["input_chars"]))
        if mode == "structure":
            structure = row.get("inline_structure")
            for offset, key in enumerate(BUCKETS, 2):
                value = structure.get(key) if isinstance(structure, dict) else None
                result[index, offset] = value if type(value) is int else -1
    return result


def _fit(train: list[dict], mode: str, *, task: str):
    names = sorted({
        row["class"] if mode == "class" else row["shape"]
        for row in train
    })
    vocabulary = {name: index for index, name in enumerate(names)}
    label = np.asarray(
        [row["duration_ms"] >= 2_000 for row in train], dtype=np.int32
    ) if task == "binary" else np.log1p(
        [row["duration_ms"] for row in train]
    )
    model = lgb.train(
        {
            "objective": "binary" if task == "binary" else "regression_l1",
            "learning_rate": .04, "num_leaves": 7,
            "min_data_in_leaf": 12, "lambda_l2": 8,
            "max_bin": 127, "seed": 42, "num_threads": 2, "verbosity": -1,
        },
        lgb.Dataset(
            _features(train, mode, vocabulary), label=label,
            categorical_feature=[0],
        ),
        num_boost_round=80,
    )
    return model, vocabulary


def _predict(fitted, rows: list[dict], mode: str, *, task: str) -> np.ndarray:
    model, vocabulary = fitted
    values = model.predict(_features(rows, mode, vocabulary), num_threads=2)
    if task == "binary":
        return values
    return np.maximum(0, np.expm1(values))


def _threshold(rows: list[dict], scores: np.ndarray) -> dict:
    eligible = []
    checks = {}
    for threshold in THRESHOLDS:
        selected = [
            row for row, score in zip(rows, scores) if score >= threshold
        ]
        correct = [row for row in selected if row["duration_ms"] >= 2_000]
        stats = {
            "selected": len(selected), "true_long": len(correct),
            "precision": len(correct) / len(selected) if selected else None,
            "true_long_workflows": len({row["workflow"] for row in correct}),
        }
        checks[str(threshold)] = stats
        if (
            len(correct) >= 8 and stats["true_long_workflows"] >= 2
            and stats["precision"] is not None and stats["precision"] >= .7
        ):
            eligible.append((len(correct), threshold))
    chosen = max(eligible, default=None)
    return {
        "train_workflow_cv": checks,
        "frozen_threshold": chosen[1] if chosen else None,
    }


def _timing(rows: list[dict], estimates: np.ndarray) -> dict:
    errors = [abs(row["duration_ms"] - float(estimate))
              for row, estimate in zip(rows, estimates)]
    workflows = defaultdict(list)
    for row, error in zip(rows, errors):
        workflows[row["workflow"]].append(error)
    return {
        "count": len(rows),
        "p50_error_ms": _quantile(errors, .5),
        "p90_error_ms": _quantile(errors, .9),
        "within_500ms": sum(e <= 500 for e in errors),
        "workflow_count": len(workflows),
        "workflow_weighted_p50_error_ms": _quantile(
            [_quantile(group, .5) for group in workflows.values()], .5
        ),
    }


def _paired_long_gain(
    rows: list[dict], baseline: np.ndarray, candidate: np.ndarray,
    *, draws: int = 2_000,
) -> dict:
    if len(rows) != len(baseline) or len(rows) != len(candidate):
        raise ValueError("paired timing requires identical long-call support")
    clusters = defaultdict(list)
    for row, reference, estimate in zip(rows, baseline, candidate):
        clusters[row["workflow"]].append((
            abs(row["duration_ms"] - float(reference)),
            abs(row["duration_ms"] - float(estimate)),
        ))
    groups = list(clusters.values())
    if len(groups) < 5:
        return {
            "status": "insufficient_independent_workflows",
            "long_calls": len(rows),
            "workflows": len(groups),
            "p50_error_gain_ms": None,
            "ci95_lower_ms": None,
            "ci95_upper_ms": None,
            "paired_positive_95pct": False,
        }

    def gain(pairs: list[tuple[float, float]]) -> float:
        return _quantile([pair[0] for pair in pairs], .5) - _quantile(
            [pair[1] for pair in pairs], .5
        )

    random = np.random.default_rng(42)
    samples = []
    for _ in range(draws):
        chosen = random.integers(0, len(groups), size=len(groups))
        samples.append(gain([
            pair for index in chosen for pair in groups[index]
        ]))
    lower = _quantile(samples, .025)
    return {
        "status": "workflow_cluster_bootstrap",
        "long_calls": len(rows),
        "workflows": len(groups),
        "p50_error_gain_ms": gain([
            pair for group in groups for pair in group
        ]),
        "ci95_lower_ms": lower,
        "ci95_upper_ms": _quantile(samples, .975),
        "paired_positive_95pct": lower > 0,
    }


def _useful_long_trigger(head: dict) -> bool:
    return (
        head["frozen_threshold"] is not None
        and head["true_long"] >= 5
        and head["true_long_workflows"] >= 3
        and head["long_precision"] >= .7
        and head["long_recall"] >= .5
    )


def evaluate(train: list[dict], heldout: list[dict]) -> dict:
    train_projects = {row["project"] for row in train}
    heldout_projects = {row["project"] for row in heldout}
    if not train or not heldout or train_projects & heldout_projects:
        raise ValueError("training and heldout projects must be nonempty and disjoint")
    long_train = [row for row in train if row["duration_ms"] >= 2_000]
    result = {
        "status": "read_only_project_disjoint_development_not_online",
        "train_projects": sorted(train_projects),
        "heldout_projects": sorted(heldout_projects),
        "train_count": len(train),
        "train_long": len(long_train),
        "train_long_by_project": dict(Counter(row["project"] for row in long_train)),
        "train_long_workflows": len({row["workflow"] for row in long_train}),
        "train_inline_structure_count": sum(
            isinstance(row.get("inline_structure"), dict) for row in train
        ),
        "heldout": {},
        "heads": {},
        "evidence_gates": {
            "train_long_from_two_projects": (
                len({row["project"] for row in long_train}) >= 2
            ),
            "train_long_from_five_workflows": (
                len({row["workflow"] for row in long_train}) >= 5
            ),
        },
        "note": (
            "Only successful completed calls are scored; open and failed calls "
            "are not negatives. Prediction features exist at TOOL_START. "
            "Oracle long subsets do not measure selection precision or H2D benefit."
        ),
    }
    if len(long_train) < 10 or len(train) - len(long_train) < 20:
        result["status"] = "insufficient_train_long_or_short"
        return result
    fold_ids = sorted({row["workflow"] for row in train})
    fitted = {}
    for mode in MODES:
        cv_rows = []
        cv_scores = []
        skipped_folds = []
        for workflow in fold_ids:
            fit = [row for row in train if row["workflow"] != workflow]
            validation = [row for row in train if row["workflow"] == workflow]
            if sum(row["duration_ms"] >= 2_000 for row in fit) < 10:
                skipped_folds.append(workflow)
                continue
            cv_rows.extend(validation)
            cv_scores.extend(
                _predict(_fit(fit, mode, task="binary"), validation, mode,
                         task="binary")
            )
        gate = _threshold(cv_rows, np.asarray(cv_scores))
        if skipped_folds:
            gate["frozen_threshold"] = None
        gate["cv_scored_count"] = len(cv_rows)
        gate["cv_total_count"] = len(train)
        gate["cv_skipped_workflows"] = skipped_folds
        fitted[mode] = {
            "binary": _fit(train, mode, task="binary"),
            "regression": _fit(train, mode, task="regression"),
            "threshold": gate["frozen_threshold"],
        }
        result["heads"][mode] = gate
    for project in sorted(heldout_projects):
        rows = [row for row in heldout if row["project"] == project]
        long_indices = [index for index, row in enumerate(rows)
                        if row["duration_ms"] >= 2_000]
        project_result = {
            "count": len(rows), "long": len(long_indices),
            "long_workflows": len({rows[i]["workflow"] for i in long_indices}),
            "inline_structure_count": sum(
                isinstance(row.get("inline_structure"), dict) for row in rows
            ),
            "heads": {},
            "paired_long_gain_vs_baselines": {},
            "zero_duration_oracle_long_baseline": _timing(
                [rows[i] for i in long_indices], np.zeros(len(long_indices))
            ),
        }
        durations_by_mode = {}
        for mode in MODES:
            head = fitted[mode]
            scores = _predict(head["binary"], rows, mode, task="binary")
            durations = _predict(head["regression"], rows, mode, task="regression")
            durations_by_mode[mode] = durations
            cutoff = head["threshold"]
            selected = [i for i, value in enumerate(scores)
                        if cutoff is not None and value >= cutoff]
            true_selected = [i for i in selected if i in long_indices]
            project_result["heads"][mode] = {
                "frozen_threshold": cutoff,
                "selected": len(selected),
                "true_long": len(true_selected),
                "true_long_workflows": len({
                    rows[i]["workflow"] for i in true_selected
                }),
                "false_short": len(selected) - len(true_selected),
                "long_precision": (
                    len(true_selected) / len(selected) if selected else None
                ),
                "long_recall": (
                    len(true_selected) / len(long_indices) if long_indices else None
                ),
                "all_timing": _timing(rows, durations),
                "oracle_long_timing": _timing(
                    [rows[i] for i in long_indices], durations[long_indices]
                ),
                "selected_long_timing": _timing(
                    [rows[i] for i in true_selected], durations[true_selected]
                ),
                "selected_long_at_least_500ms_actual_lead": sum(
                    rows[i]["duration_ms"] - max(0, durations[i] - 500) >= 500
                    for i in true_selected
                ),
            }
        for baseline in ("class", "shape"):
            project_result["paired_long_gain_vs_baselines"][baseline] = (
                _paired_long_gain(
                    [rows[i] for i in long_indices],
                    durations_by_mode[baseline][long_indices],
                    durations_by_mode["structure"][long_indices],
                )
            )
        result["heldout"][project] = project_result
    result["evidence_gates"]["heldout_each_project_five_long"] = all(
        group["long"] >= 5 for group in result["heldout"].values()
    )
    result["evidence_gates"]["structure_p50_improves_both_projects_20pct"] = all(
        (
            (structure := group["heads"]["structure"]["oracle_long_timing"])[
                "p50_error_ms"
            ] is not None
            and structure["p50_error_ms"] <= .8 * min(
                group["heads"][mode]["oracle_long_timing"]["p50_error_ms"]
                for mode in ("class", "shape")
            )
            and structure["p90_error_ms"] <= min(
                group["heads"][mode]["oracle_long_timing"]["p90_error_ms"]
                for mode in ("class", "shape")
            )
        )
        for group in result["heldout"].values()
    )
    result["evidence_gates"]["structure_paired_gain_positive_95pct"] = all(
        comparison["paired_positive_95pct"]
        for group in result["heldout"].values()
        for comparison in group["paired_long_gain_vs_baselines"].values()
    )
    result["evidence_gates"]["frozen_structure_trigger_covers_long"] = all(
        _useful_long_trigger(group["heads"]["structure"])
        for group in result["heldout"].values()
    )
    result["evidence_gates"]["all_met"] = all(result["evidence_gates"].values())
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", required=True, type=Path)
    parser.add_argument("--heldout-workflows", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    train, train_censored = cold_calls(args.train_workflows)
    heldout, heldout_censored = cold_calls(args.heldout_workflows)
    result = evaluate(train, heldout)
    result["train_censor"] = train_censored
    result["heldout_censor"] = heldout_censored
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
