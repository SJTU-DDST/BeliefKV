#!/usr/bin/env python3
"""Project-held-out timing diagnostic for opt-in child completion notices."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

import numpy as np

try:
    from scripts.audit_child_hidden_trace import (
        blocked_child_invocations, index_workflow,
    )
except ModuleNotFoundError:
    from audit_child_hidden_trace import blocked_child_invocations, index_workflow


FEATURES = ("child_age_ms", "prior_llm_results", "prior_tool_ends")


def _workflow_files(root: Path) -> list[Path]:
    direct = sorted(root.glob("workflows/*/runtime_events.deepagents.jsonl"))
    return direct or sorted(root.glob(
        "*/workflows/*/runtime_events.deepagents.jsonl",
    ))


def load_episodes(root: Path) -> tuple[list[dict], dict[str, int]]:
    files = _workflow_files(root)
    if not files:
        raise FileNotFoundError(f"missing workflow events in {root}")
    episodes = []
    counts: Counter[str] = Counter(workflows=len(files))
    for path in files:
        workflow = path.parent
        events = [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        ]
        audit_path = workflow / "sandbox_audit.jsonl"
        if not audit_path.is_file():
            raise FileNotFoundError(f"missing sandbox audit for {workflow}")
        notices = defaultdict(list)
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("event") == "child_return_intent_shadow":
                notices[row["invocation_id"]].append(float(row["ts_ms"]))
        blocked = blocked_child_invocations(workflow)
        terminals, join_last = index_workflow(
            events, blocked_invocations=blocked,
        )
        returned = {
            child: float(ts) for _, (child, ts) in terminals.items()
        }
        children = {
            row["target_invocation_id"]
            for row in events
            if row["kind"] == "spawn" and row.get("target_invocation_id")
        }
        counts["natural_returns"] += len(returned)
        for child, times in notices.items():
            if child not in children:
                raise ValueError(f"unbound child intent in {workflow}")
            counts["announced_children"] += 1
            counts["repeated_notices"] += len(times) - 1
            notice = min(times)
            end = returned.get(child)
            later_tool = any(
                row["kind"] == "tool_start"
                and row.get("invocation_id") == child
                and (row.get("attributes") or {}).get("tool_name")
                != "announce_completion_intent"
                and notice < float(row["ts_ms"]) < (
                    end if end is not None else float("inf")
                )
                for row in events
            )
            if later_tool or (end is not None and end <= notice):
                counts["revoked_or_late"] += 1
                continue
            if end is None:
                if child in blocked or any(
                    row["kind"] == "invocation_cancel"
                    and row.get("invocation_id") == child
                    and float(row["ts_ms"]) > notice
                    for row in events
                ):
                    counts["nonterminal_or_blocked"] += 1
                else:
                    counts["censored_without_terminal"] += 1
                continue
            prior = [
                row for row in events
                if row.get("invocation_id") == child
                and float(row["ts_ms"]) <= notice
            ]
            creates = [
                float(row["ts_ms"]) for row in prior
                if row["kind"] == "invocation_create"
            ]
            if len(creates) != 1 or notice < creates[0]:
                raise ValueError(f"missing or invalid child start in {workflow}")
            submitted = sorted(
                float(row["ts_ms"]) for row in events
                if row["kind"] == "llm_submit"
                and row.get("invocation_id") == child
                and not (row.get("attributes") or {}).get("runtime_internal")
                and notice < float(row["ts_ms"]) < end
            )
            completed = sorted(
                (float(row["ts_ms"]), row.get("attributes") or {})
                for row in events
                if row["kind"] == "llm_result"
                and row.get("invocation_id") == child
                and not (row.get("attributes") or {}).get("runtime_internal")
                and notice < float(row["ts_ms"]) < end
            )
            post_notice = None
            if (len(submitted) == 1 and len(completed) == 1
                    and submitted[0] <= completed[0][0]):
                result_ts, attrs = completed[0]
                post_notice = {
                    "notice_to_llm_submit_ms": submitted[0] - notice,
                    "llm_submit_to_result_ms": result_ts - submitted[0],
                    "llm_result_to_return_ms": end - result_ts,
                    "final_output_tokens": (
                        attrs.get("output_tokens")
                        if type(attrs.get("output_tokens")) in (int, float)
                        else None
                    ),
                }
            episodes.append({
                "project": workflow.name.split("__", 1)[0],
                "task_id": workflow.name,
                "lead_ms": end - notice,
                "join_last": child in join_last,
                "post_notice": post_notice,
                "notice_ms": notice,
                "metrics_path": workflow.parent.parent / "sglang_metrics.jsonl",
                "features": [
                    notice - creates[0],
                    sum(row["kind"] == "llm_result" and not
                        (row.get("attributes") or {}).get("runtime_internal")
                        for row in prior),
                    sum(row["kind"] == "tool_end" and
                        (row.get("attributes") or {}).get("tool_name")
                        != "announce_completion_intent" for row in prior),
                ],
            })
    counts["valid_intents"] = len(episodes)
    for key in (
        "announced_children", "repeated_notices", "revoked_or_late",
        "nonterminal_or_blocked", "censored_without_terminal",
    ):
        counts.setdefault(key, 0)
    return episodes, dict(counts)


def _metrics(actual: list[float], predicted: list[float]) -> dict | None:
    if not actual:
        return None
    target = np.asarray(actual)
    prediction = np.asarray(predicted)
    errors = np.abs(target - prediction)
    return {
        "count": len(actual),
        "mae_ms": float(np.mean(errors)),
        "median_absolute_error_ms": float(np.median(errors)),
        "p90_absolute_error_ms": float(np.percentile(errors, 90)),
        "within_500ms": int(np.sum(errors <= 500)),
        "forecast_over_500ms_too_early": int(np.sum(target - prediction > 500)),
        "forecast_over_500ms_too_late": int(np.sum(prediction - target > 500)),
        "predicted_500_to_3000ms": int(np.sum(
            (prediction >= 500) & (prediction <= 3000),
        )),
        "actual_500_to_3000ms": int(np.sum(
            (target >= 500) & (target <= 3000),
        )),
        "prediction_over_3000ms": int(np.sum(prediction > 3000)),
        "actual_over_3000ms": int(np.sum(target > 3000)),
    }


def _post_notice_components(episodes: list[dict]) -> dict:
    complete = [row["post_notice"] for row in episodes if row["post_notice"]]
    fields = (
        "notice_to_llm_submit_ms", "llm_submit_to_result_ms",
        "llm_result_to_return_ms", "final_output_tokens",
    )
    return {
        "valid": len(complete),
        "missing_or_ambiguous": len(episodes) - len(complete),
        "posthoc_not_prediction_features": {
            field: {
                "p50": float(np.percentile(values, 50)),
                "p90": float(np.percentile(values, 90)),
            } if (values := [row[field] for row in complete
                            if row[field] is not None]) else None
            for field in fields
        },
    }


def _ridge_predict(train: list[dict], test: list[dict]) -> list[float]:
    # Regularize the small-sample residual against the train-only median prior.
    x_train = np.log1p(np.asarray([row["features"] for row in train]))
    x_test = np.log1p(np.asarray([row["features"] for row in test]))
    center = x_train.mean(axis=0)
    scale = np.maximum(x_train.std(axis=0), 1.)
    x_train = (x_train - center) / scale
    x_test = (x_test - center) / scale
    target = np.log1p([row["lead_ms"] for row in train])
    prior = float(np.median(target))
    fitted = np.linalg.solve(
        x_train.T @ x_train + 8. * np.eye(x_train.shape[1]),
        x_train.T @ (target - prior),
    )
    return list(np.maximum(0., np.expm1(prior + x_test @ fitted)))


def evaluate(root: Path) -> dict:
    episodes, counts = load_episodes(root)
    projects = sorted({row["project"] for row in episodes})
    if len(projects) < 3:
        raise ValueError("need at least three projects with valid intent episodes")
    methods: dict[str, dict[str, list[float]]] = {
        name: {"actual": [], "predicted": [], "join_actual": [],
               "join_predicted": []}
        for name in ("train_median", "causal_ridge")
    }
    folds = []
    for project in projects:
        train = [row for row in episodes if row["project"] != project]
        test = [row for row in episodes if row["project"] == project]
        fixed = [float(np.median([row["lead_ms"] for row in train]))] * len(test)
        candidates = {"train_median": fixed, "causal_ridge": _ridge_predict(train, test)}
        fold = {"heldout_project": project, "train_children": len(train),
                "test_children": len(test)}
        for name, predictions in candidates.items():
            actual = [row["lead_ms"] for row in test]
            join_pairs = [
                (row["lead_ms"], predicted)
                for row, predicted in zip(test, predictions) if row["join_last"]
            ]
            metrics = methods[name]
            metrics["actual"].extend(actual)
            metrics["predicted"].extend(predictions)
            metrics["join_actual"].extend(pair[0] for pair in join_pairs)
            metrics["join_predicted"].extend(pair[1] for pair in join_pairs)
            fold[name] = {
                "return": _metrics(actual, predictions),
                "join_last_child": _metrics(
                    [pair[0] for pair in join_pairs],
                    [pair[1] for pair in join_pairs],
                ),
            }
        folds.append(fold)
    return {
        "diagnostic_only": True,
        "counts": counts,
        "causal_features": list(FEATURES),
        "protocol": (
            "Leave-one-project-out; fit median and fixed log-linear ridge "
            "(penalty=8) on other projects only. Notice-time features only. "
            "No threshold/model selection using held-out projects."
        ),
        "folds": folds,
        "pooled": {
            name: {
                "return": _metrics(values["actual"], values["predicted"]),
                "join_last_child": _metrics(
                    values["join_actual"], values["join_predicted"],
                ),
            }
            for name, values in methods.items()
        },
        "post_notice_decomposition": _post_notice_components(episodes),
        "scope": (
            "Development pilot, not sealed test; opt-in tool changes agent "
            "trajectory. Revoked and censored episodes excluded from point "
            "errors but counted separately. No physical H2D/D2H claim."
        ),
    }


def evaluate_heldout(train_root: Path, heldout_root: Path) -> dict:
    train, train_counts = load_episodes(train_root)
    test, test_counts = load_episodes(heldout_root)
    train_projects = {row["project"] for row in train}
    test_projects = {
        path.parent.name.split("__", 1)[0]
        for path in _workflow_files(heldout_root)
    }
    if train_projects & test_projects:
        raise ValueError("held-out projects overlap with fit projects")
    if not train or not test_projects:
        raise ValueError("need fit episodes and held-out workflow events")
    fixed = [float(np.median([row["lead_ms"] for row in train]))] * len(test)
    predictions = {
        "train_median": fixed,
        "causal_ridge": _ridge_predict(train, test) if test else [],
    }
    return {
        "diagnostic_only": True,
        "protocol": (
            "Fit on specified prior projects only; evaluate separately on "
            "the new project(s), with no threshold or feature selection."
        ),
        "train_projects": sorted(train_projects),
        "heldout_projects": sorted(test_projects),
        "train_counts": train_counts,
        "heldout_counts": test_counts,
        "post_notice_decomposition": _post_notice_components(test),
        "results": {
            name: {
                "return": _metrics(
                    [row["lead_ms"] for row in test], estimates,
                ),
                "join_last_child": _metrics(
                    [row["lead_ms"] for row in test if row["join_last"]],
                    [estimate for row, estimate in zip(test, estimates)
                     if row["join_last"]],
                ),
            }
            for name, estimates in predictions.items()
        },
        "scope": (
            "Development project holdout, not sealed test; tool changes "
            "trajectory. Censored/revoked episodes do not count as point "
            "forecasts. No physical migration."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-root", type=Path, required=True)
    parser.add_argument("--heldout-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = (
        evaluate_heldout(args.pilot_root, args.heldout_root)
        if args.heldout_root else evaluate(args.pilot_root)
    )
    args.output.write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8",
    )


if __name__ == "__main__":
    main()
