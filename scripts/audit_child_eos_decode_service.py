#!/usr/bin/env python3
"""Read-only, request-matched decode service audit for sampled child EOS cues."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
from statistics import median
import sys

import orjson

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pilot_child_stream_eos_joint import load_rows
from scripts.pilot_join_service_progress import CLOCK_GUARD_MS, clock_bracket


THRESHOLDS = (0.05, 0.1)
PRIOR_WINDOW_MS = 2000.


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lo = math.floor(index)
    return round(
        ordered[lo] + (ordered[math.ceil(index)] - ordered[lo]) * (index - lo),
        2,
    )


def _candidate(row: dict, threshold: float) -> dict | None:
    floor = math.log(threshold)
    for snapshot in row["snapshots"]:
        value = snapshot["eos_shadow_max_logprob_since_previous_snapshot"]
        if (
            type(value) in (float, int) and math.isfinite(value)
            and value >= floor
        ):
            return {
                "row": row,
                "threshold": threshold,
                "trigger_ms": float(snapshot["ts_ms"]),
                "content_chars": snapshot["content_chars"],
                "prior": [],
                "after": [],
            }
    return None


def _audit_run(run: Path, rows: list[dict]) -> dict:
    workflows = run / "workloads/workflows"
    server = run / "server"
    server_events = server / "runtime_events.sglang.jsonl"
    lower, upper, bracket = clock_bracket(
        workflows, server_events,
    )
    watched: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        for threshold in THRESHOLDS:
            candidate = _candidate(row, threshold)
            if candidate is not None:
                watched[row["rid"]].append(candidate)

    counts = Counter()
    eligible = {row["rid"]: row for row in rows}
    server_results = {}
    with server_events.open("rb") as stream:
        for line in stream:
            event = orjson.loads(line)
            if event.get("kind") == "llm_result":
                rid = (event.get("attributes") or {}).get("request_id")
                if rid in eligible:
                    server_results[rid] = event
    matched_server_results = {}
    for rid, row in eligible.items():
        result = server_results.get(rid)
        if result is None:
            counts["server_result_missing"] += 1
            continue
        if (
            result.get("invocation_id") != row["invocation_id"]
            or result.get("context_id") != row["context_id"]
            or result.get("context_epoch") != row["context_epoch"]
        ):
            counts["server_result_identity_mismatch"] += 1
            continue
        server_done = float(result["ts_ms"])
        matched_server_results[rid] = server_done
    for rid, candidates in watched.items():
        server_done = matched_server_results.get(rid)
        if server_done is None:
            continue
        for candidate in candidates:
            trigger = candidate["trigger_ms"]
            candidate["server_result_ts_ms"] = server_done
            candidate["server_result_position"] = (
                "finished_before_cue"
                if server_done < trigger + lower - CLOCK_GUARD_MS
                else "finished_after_cue"
                if server_done > trigger + upper + CLOCK_GUARD_MS
                else "clock_ambiguous"
            )
    with (server / "runtime_audit.jsonl").open("rb") as stream:
        for line in stream:
            event = orjson.loads(line)
            if (
                event.get("event") != "gpu_service_sample"
                or event.get("phase") != "decode"
            ):
                continue
            when = float(event["ts_ms"])
            for sample in event.get("request_samples") or ():
                candidates = watched.get(sample.get("request_id"))
                if not candidates:
                    continue
                row = candidates[0]["row"]
                if (
                    sample.get("invocation_id") != row["invocation_id"]
                    or sample.get("context_id") != row["context_id"]
                    or sample.get("context_epoch") != row["context_epoch"]
                ):
                    counts["identity_mismatch"] += 1
                    continue
                before, delta = (
                    sample.get("output_tokens_before"),
                    sample.get("token_delta"),
                )
                if (
                    sample.get("token_delta_semantics")
                    != "observed_output_ids_delta"
                    or type(before) is not int or before < 0
                    or type(delta) is not int or delta < 0
                ):
                    counts["invalid_decode_sample"] += 1
                    continue
                counts["matched_decode_samples"] += 1
                for candidate in candidates:
                    trigger = candidate["trigger_ms"]
                    prior_cutoff = trigger + lower - CLOCK_GUARD_MS
                    if prior_cutoff - PRIOR_WINDOW_MS <= when < prior_cutoff:
                        candidate["prior"].append((when, before + delta))
                    row = candidate["row"]
                    if (
                        row["label"] == "return"
                        and when >= trigger + upper + CLOCK_GUARD_MS
                        and when < row["return_ts"] + lower - CLOCK_GUARD_MS
                    ):
                        candidate["after"].append((when, delta))

    grouped: dict[tuple[str, float], list[dict]] = defaultdict(list)
    for candidates in watched.values():
        for candidate in candidates:
            grouped[
                candidate["row"]["project"], candidate["threshold"]
            ].append(candidate)
    projects = sorted({row["project"] for row in rows})
    results = {}
    delivery = {}
    for project in projects:
        project_rows = [row for row in rows if row["project"] == project]
        for label in ("return", "tool"):
            labeled = [row for row in project_rows if row["label"] == label]
            matched = [
                row for row in labeled if row["rid"] in matched_server_results
            ]
            first_before = [
                row for row in matched
                if row["snapshots"][0]["ts_ms"] + upper + CLOCK_GUARD_MS
                < matched_server_results[row["rid"]]
            ]
            last_after = [
                row for row in matched
                if row["snapshots"][-1]["ts_ms"] + lower - CLOCK_GUARD_MS
                > matched_server_results[row["rid"]]
            ]
            delivery[f"{project}|{label}"] = {
                "rounds_with_content": len(labeled),
                "server_result_matched": len(matched),
                "first_content_before_server_end": len(first_before),
                "last_content_after_server_end": len(last_after),
                "first_to_last_content_p50_ms": _quantile([
                    row["snapshots"][-1]["ts_ms"]
                    - row["snapshots"][0]["ts_ms"]
                    for row in matched
                ], .5),
                "last_content_after_server_end_min_p50_ms": _quantile([
                    row["snapshots"][-1]["ts_ms"] + lower
                    - matched_server_results[row["rid"]]
                    for row in last_after
                ], .5),
                "last_content_after_server_end_min_p90_ms": _quantile([
                    row["snapshots"][-1]["ts_ms"] + lower
                    - matched_server_results[row["rid"]]
                    for row in last_after
                ], .9),
            }
        for threshold in THRESHOLDS:
            candidates = grouped[project, threshold]
            natural = [
                candidate for candidate in candidates
                if candidate["row"]["label"] == "return"
            ]
            early = [
                candidate for candidate in natural
                if candidate["row"]["return_ts"] - candidate["trigger_ms"] > 2000.
            ]
            timely = [
                candidate for candidate in natural
                if 500. <= candidate["row"]["return_ts"]
                - candidate["trigger_ms"] <= 2000.
            ]
            result = {
                "natural_with_content": sum(
                    row["label"] == "return" for row in project_rows
                ),
                "tools_with_content": sum(
                    row["label"] == "tool" for row in project_rows
                ),
                "triggered_natural": len(natural),
                "triggered_join_last": sum(
                    candidate["row"]["join_last"] for candidate in natural
                ),
                "triggered_tools": sum(
                    candidate["row"]["label"] == "tool"
                    for candidate in candidates
                ),
                "window_hits": len(timely),
                "early_over_2000ms": len(early),
                "prior_decode_2plus": sum(
                    len(candidate["prior"]) >= 2 for candidate in candidates
                ),
                "prior_decode_last_age_p50_ms": _quantile([
                    candidate["trigger_ms"] + lower - CLOCK_GUARD_MS
                    - max(when for when, _ in candidate["prior"])
                    for candidate in candidates if candidate["prior"]
                ], .5),
            }
            for name, group in (("early", early), ("window", timely)):
                result[name + "_lead_p50_ms"] = _quantile([
                    candidate["row"]["return_ts"] - candidate["trigger_ms"]
                    for candidate in group
                ], .5)
                result[name + "_server_result_position"] = dict(Counter(
                    candidate.get("server_result_position", "missing")
                    for candidate in group
                ))
                result[name + "_server_end_to_cue_min_p50_ms"] = _quantile([
                    candidate["trigger_ms"] + lower
                    - candidate["server_result_ts_ms"]
                    for candidate in group
                    if candidate.get("server_result_position")
                    == "finished_before_cue"
                ], .5)
                result[name + "_server_end_to_cue_min_p90_ms"] = _quantile([
                    candidate["trigger_ms"] + lower
                    - candidate["server_result_ts_ms"]
                    for candidate in group
                    if candidate.get("server_result_position")
                    == "finished_before_cue"
                ], .9)
                result[name + "_trigger_to_llm_result_p50_ms"] = _quantile([
                    candidate["row"]["result_ts"] - candidate["trigger_ms"]
                    for candidate in group
                ], .5)
                result[name + "_llm_result_to_return_p50_ms"] = _quantile([
                    candidate["row"]["return_ts"] - candidate["row"]["result_ts"]
                    for candidate in group
                ], .5)
                result[name + "_trigger_to_llm_result_over_2000ms"] = sum(
                    candidate["row"]["result_ts"] - candidate["trigger_ms"]
                    > 2000. for candidate in group
                )
                result[name + "_with_post_decode"] = sum(
                    bool(candidate["after"]) for candidate in group
                )
                result[name + "_post_decode_tokens_p50"] = _quantile([
                    sum(delta for _, delta in candidate["after"])
                    for candidate in group
                ], .5)
                result[name + "_post_decode_tokens_p90"] = _quantile([
                    sum(delta for _, delta in candidate["after"])
                    for candidate in group
                ], .9)
                result[name + "_largest_observed_sample_gap_p50_ms"] = (
                    _quantile([
                        max(
                            b - a for a, b in zip(
                                [
                                    candidate["trigger_ms"]
                                    + upper + CLOCK_GUARD_MS,
                                    *sorted(when for when, _ in candidate["after"]),
                                ],
                                [
                                    *sorted(when for when, _ in candidate["after"]),
                                    candidate["row"]["return_ts"]
                                    + lower - CLOCK_GUARD_MS,
                                ],
                            )
                        )
                        for candidate in group
                        if candidate["after"]
                    ], .5)
                )
            results[f"{project}|{threshold}"] = result
    return {
        "run": run.name,
        "clock_bracket": bracket,
        "decode_counts": dict(counts),
        "content_delivery": delivery,
        "projects": results,
    }


def evaluate(runs: list[Path]) -> dict:
    if len(set(runs)) != len(runs):
        raise ValueError("runs must be distinct")
    workflows = [run / "workloads/workflows" for run in runs]
    rows, counts, manifests, sampled = load_rows(workflows)
    if not sampled:
        raise ValueError("sampled EOS snapshots are required")
    by_task = {run: {
        path.parent.name for path in
        (run / "workloads/workflows").glob("*/runtime_events.deepagents.jsonl")
    } for run in runs}
    return {
        "scope": (
            "development-only first EOS cue by request; future decode samples "
            "are used only to explain errors, never as prediction features. "
            "Service sample gaps are not GPU idle-time measurements."
        ),
        "clock_rule": (
            "Pre-trigger sample must precede the lower clock bound minus "
            "100 ms; post-trigger sample must follow the upper clock bound "
            "plus 100 ms and precede the conservative RETURN bound. Server "
            "result before/after cue uses both bracket bounds and a 100 ms "
            "guard; future server result is diagnostic, not an online input."
        ),
        "manifests": manifests,
        "collection_counts": dict(counts),
        "runs": [
            _audit_run(run, [row for row in rows if row["task"] in by_task[run]])
            for run in runs
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = evaluate(args.run)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
