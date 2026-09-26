#!/usr/bin/env python3
"""Test whether pre-notice SGLang load improves child RETURN timing."""

from __future__ import annotations

import argparse
from bisect import bisect_right
import json
from pathlib import Path

import numpy as np

try:
    from scripts.evaluate_child_return_intent_timing import (
        _metrics, _ridge_predict, load_episodes,
    )
except ModuleNotFoundError:
    from evaluate_child_return_intent_timing import (
        _metrics, _ridge_predict, load_episodes,
    )


MAX_METRIC_AGE_MS = 2000


def _metrics_at_notice(path: Path, notice_ms: float) -> tuple[float, float, float]:
    if not path.is_file():
        raise FileNotFoundError(f"missing contemporaneous metrics: {path}")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    times = [float(row["monotonic_ts_ms"]) for row in rows]
    if times != sorted(times):
        raise ValueError(f"metrics timestamps are not ordered: {path}")
    index = bisect_right(times, notice_ms) - 1
    if index < 0 or notice_ms - times[index] > MAX_METRIC_AGE_MS:
        raise ValueError(f"no recent metric at notice: {path}")
    row = rows[index]
    running, queued = row.get("num_running_reqs"), row.get("num_queue_reqs")
    if type(running) not in (int, float) or type(queued) not in (int, float):
        raise ValueError(f"missing scheduler load fields: {path}")
    if not np.isfinite(running) or not np.isfinite(queued) or min(running, queued) < 0:
        raise ValueError(f"invalid scheduler load fields: {path}")
    return float(running), float(queued), float(notice_ms - times[index])


def add_as_of_load(episodes: list[dict]) -> list[dict]:
    result = []
    for row in episodes:
        running, queued, age = _metrics_at_notice(
            row["metrics_path"], row["notice_ms"],
        )
        result.append({
            **row,
            "features": [*row["features"], running, queued],
            "as_of_load": {"running": running, "queued": queued, "age_ms": age},
        })
    return result


def _evaluate_methods(train: list[dict], test: list[dict]) -> dict:
    target = [row["lead_ms"] for row in test]
    source = [row["lead_ms"] for row in train]
    base = {
        "train_median": [float(np.median(source))] * len(test),
        "causal_ridge": _ridge_predict(train, test),
        "causal_ridge_plus_asof_load": _ridge_predict(
            add_as_of_load(train), add_as_of_load(test),
        ),
    }
    return {
        method: {
            "return": _metrics(target, estimates),
            "join_last_child": _metrics(
                [row["lead_ms"] for row in test if row["join_last"]],
                [estimate for row, estimate in zip(test, estimates)
                 if row["join_last"]],
            ),
        }
        for method, estimates in base.items()
    }


def evaluate(fit_root: Path, heldout_root: Path) -> dict:
    fit, fit_counts = load_episodes(fit_root)
    heldout, heldout_counts = load_episodes(heldout_root)
    fit_projects = {row["project"] for row in fit}
    heldout_projects = {row["project"] for row in heldout}
    if not fit or not heldout or fit_projects & heldout_projects:
        raise ValueError("need disjoint fit and held-out projects with natural intents")
    # Do not silently zero-fill unavailable host-observed service load.
    fit_with_load, test_with_load = add_as_of_load(fit), add_as_of_load(heldout)
    return {
        "diagnostic_only": True,
        "hypothesis_formed_after_observing_heldout_project": True,
        "fit_projects": sorted(fit_projects),
        "development_project": sorted(heldout_projects),
        "fit_counts": fit_counts,
        "development_counts": heldout_counts,
        "max_metric_age_ms": MAX_METRIC_AGE_MS,
        "pre_notice_load": {
            cohort: {
                "running_p50": float(np.median([
                    row["as_of_load"]["running"] for row in rows
                ])),
                "queued_p50": float(np.median([
                    row["as_of_load"]["queued"] for row in rows
                ])),
                "metric_age_p90_ms": float(np.percentile([
                    row["as_of_load"]["age_ms"] for row in rows
                ], 90)),
            }
            for cohort, rows in (
                ("fit", fit_with_load), ("development", test_with_load),
            )
        },
        "results": _evaluate_methods(fit, heldout),
        "scope": (
            "Only metrics observed before notice; no future tokens or "
            "completion state. Load hypothesis is post-hoc relative to this "
            "development project and requires a new frozen task/project holdout."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fit-root", required=True, type=Path)
    parser.add_argument("--development-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(evaluate(args.fit_root, args.development_root), indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
