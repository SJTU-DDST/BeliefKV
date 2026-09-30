#!/usr/bin/env python3
"""Development-only RETURN trigger screen with causally prior GPU decode work."""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import re
import sys

import numpy as np
import orjson

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.child_stream_service_index import snapshot_status
from scripts.pilot_child_stream_content import (
    _service_indices,
    centroid_scores,
    collect,
    features,
)
from scripts.pilot_join_service_progress import CLOCK_GUARD_MS


_HEADING = re.compile(r"(?:^|\n)#{1,4}\s+\S")
_RECENT_MS = 500.


def delivered_history(
    previous: str, previous_chars: int, snap: dict, *, limit: int = 1024,
) -> tuple[str, bool]:
    delta = snap["content_chars"] - previous_chars
    if delta <= 0:
        return previous, False
    tail = snap["content_tail"]
    gap = delta > len(tail)
    added = (" [unobserved_text_gap] " + tail) if gap else tail[-delta:]
    return (previous + added)[-limit:], gap


def service_rows(
    roots: list[Path], runs: list[Path],
) -> tuple[list[dict], dict]:
    rows, counts = collect(roots, min_snapshot_chars=32)
    indexed, reports = _service_indices(roots, runs, rows)
    by_rid = {row["rid"]: row for row in rows}
    decoded: dict[str, list[tuple[float, int, int]]] = defaultdict(list)
    excluded = Counter()
    for root, run in zip(roots, runs):
        ids = {
            row["rid"] for row in rows
            if (root / row["task"] / "child_stream_content.jsonl").exists()
        }
        with (run / "server/runtime_audit.jsonl").open("rb") as stream:
            for line in stream:
                event = orjson.loads(line)
                if (
                    event.get("event") != "gpu_service_sample"
                    or event.get("phase") != "decode"
                ):
                    continue
                for sample in event.get("request_samples") or ():
                    rid = sample.get("request_id")
                    if rid not in ids:
                        continue
                    row = by_rid[rid]
                    if (
                        sample.get("invocation_id") != row["invocation_id"]
                        or sample.get("context_id") != row["context_id"]
                        or sample.get("context_epoch") != row["context_epoch"]
                        or sample.get("token_delta_semantics")
                        != "observed_output_ids_delta"
                    ):
                        excluded["decode_identity_or_semantics"] += 1
                        continue
                    before = sample.get("output_tokens_before")
                    delta = sample.get("token_delta")
                    if (
                        type(before) is not int or before < 0
                        or type(delta) is not int or delta <= 0
                    ):
                        excluded["invalid_decode_delta"] += 1
                        continue
                    decoded[rid].append((
                        float(event["ts_ms"]), before + delta,
                        int(event.get("batch_size") or 0),
                    ))

    selected = []
    for row in rows:
        state = indexed.get(row["rid"])
        if state is None:
            excluded["missing_server_result"] += 1
            continue
        samples = sorted(
            (ts, tokens, batch) for ts, tokens, batch in decoded.get(row["rid"], [])
            if ts <= state["server_end_ms"]
        )
        times = [ts for ts, _, _ in samples]
        seen_heading = False
        history, previous_chars, history_gaps = "", 0, 0
        kept = []
        for snap in row["snapshots"]:
            history, gap = delivered_history(history, previous_chars, snap)
            previous_chars = snap["content_chars"]
            history_gaps += gap
            seen_heading |= bool(_HEADING.search(snap["content_tail"]))
            if snapshot_status(state, snap["ts_ms"]) != (
                "unfinished_with_recent_decode"
            ):
                continue
            cutoff = (
                snap["ts_ms"] + state["offset_lower_ms"] - CLOCK_GUARD_MS
            )
            end = bisect_left(times, cutoff)
            start = bisect_left(times, cutoff - _RECENT_MS)
            if end - start < 2 or cutoff - times[end - 1] > _RECENT_MS:
                continue
            first_ts, first_tokens, _ = samples[start]
            last_ts, last_tokens, last_batch = samples[end - 1]
            if last_ts <= first_ts or last_tokens < first_tokens:
                excluded["invalid_prior_progress"] += 1
                continue
            rate = (last_tokens - first_tokens) * 1000 / (last_ts - first_ts)
            initial_ts, initial_tokens, _ = samples[0]
            lifetime_rate = (
                (last_tokens - initial_tokens) * 1000
                / max(last_ts - initial_ts, 1.)
            )
            kept.append({
                **snap,
                "observed_output_tokens": last_tokens,
                "observed_decode_server_ts_ms": last_ts,
                "delivered_text_history": history,
                "delivered_text_history_gaps": history_gaps,
                "decode_features": (
                    math.log1p(last_tokens),
                    math.log1p(rate),
                    math.log1p(cutoff - last_ts),
                    float(seen_heading),
                ),
                "decode_history_features": (
                    math.log1p(max(0., last_ts - initial_ts)),
                    math.log1p(max(0., lifetime_rate)),
                    math.log1p(last_batch),
                ),
            })
        if kept:
            selected.append({**row, "snapshots": kept})
        else:
            excluded[f"{row['label']}_without_causal_service"] += 1
    return selected, {
        "all_rounds": len(rows),
        "supported_rounds": len(selected),
        "by_project_label": dict(sorted(Counter(
            f"{row['project']}_{row['label']}" for row in selected
        ).items())),
        "excluded": dict(excluded),
        "service_runs": reports,
        "stream_counts": dict(counts),
    }


def evaluate(roots: list[Path], runs: list[Path], heldout: str) -> dict:
    rows, coverage = service_rows(roots, runs)
    train = [row for row in rows if row["project"] != heldout]
    held = [row for row in rows if row["project"] == heldout]
    if {row["label"] for row in train} != {"tool", "return"} or not held:
        return {"status": "insufficient_project_disjoint_samples", **coverage}
    _, train_size, y, train_idx = features(train, target_window_ms=2000)
    _, held_size, _, held_idx = features(held, target_window_ms=2000)
    train_decode = np.asarray([
        snap["decode_features"] for row in train for snap in row["snapshots"]
    ], dtype=float)
    held_decode = np.asarray([
        snap["decode_features"] for row in held for snap in row["snapshots"]
    ], dtype=float)
    size_mean, size_std = train_size.mean(axis=0), np.maximum(
        train_size.std(axis=0), 1e-9,
    )
    decode_mean, decode_std = train_decode.mean(axis=0), np.maximum(
        train_decode.std(axis=0), 1e-9,
    )
    train_size = (train_size - size_mean) / size_std
    held_size = (held_size - size_mean) / size_std
    train_decode = (train_decode - decode_mean) / decode_std
    held_decode = (held_decode - decode_mean) / decode_std
    task_counts = Counter(row["task"] for row in train)
    weights = np.asarray([
        1 / (task_counts[train[i]["task"]] * len(train[i]["snapshots"]))
        for i in train_idx
    ])
    results, triggered_by_name = {}, {}
    for name, tr_x, held_x in (
        ("size_only", train_size, held_size),
        ("size_plus_decode", np.column_stack((train_size, train_decode[:, :3])),
         np.column_stack((held_size, held_decode[:, :3]))),
        ("size_plus_decode_heading", np.column_stack((train_size, train_decode)),
         np.column_stack((held_size, held_decode))),
    ):
        train_scores, held_scores = centroid_scores(tr_x, held_x, y, weights)
        bad_max = [float("-inf")] * len(train)
        for score, i, snap in zip(
            train_scores, train_idx,
            (s for row in train for s in row["snapshots"]),
        ):
            row = train[i]
            if row["label"] == "tool" or row["return_ts"] - snap["ts_ms"] > 2000:
                bad_max[i] = max(bad_max[i], float(score))
        bad_max = [score for score in bad_max if np.isfinite(score)]
        if not bad_max:
            raise ValueError("no bad rounds for threshold selection")
        threshold = float(np.nextafter(np.quantile(bad_max, .95), 1.0))
        triggers: dict[int, float] = {}
        for score, i, snap in zip(
            held_scores, held_idx,
            (s for row in held for s in row["snapshots"]),
        ):
            if score >= threshold and i not in triggers:
                triggers[i] = snap["ts_ms"]
        returns = [i for i, row in enumerate(held) if row["label"] == "return"]
        tools = [i for i, row in enumerate(held) if row["label"] == "tool"]
        hits = Counter(
            held[i]["task"] for i in returns
            if i in triggers
            and 500 <= held[i]["return_ts"] - triggers[i] <= 2000
        )
        results[name] = {
            "threshold_selected_on_train": threshold,
            "heldout_return_rounds": len(returns),
            "heldout_tool_rounds": len(tools),
            "live_window_hits": sum(hits.values()),
            "join_last_live_window_hits": sum(
                held[i]["join_last"] for i in returns
                if i in triggers
                and 500 <= held[i]["return_ts"] - triggers[i] <= 2000
            ),
            "early_over_2000ms": sum(
                held[i]["return_ts"] - triggers[i] > 2000
                for i in returns if i in triggers
            ),
            "tool_false_first_triggers": sum(i in triggers for i in tools),
            "live_window_hits_by_workflow": dict(sorted(hits.items())),
        }
        triggered_by_name[name] = hits

    workflow_returns = Counter(
        row["task"] for row in held if row["label"] == "return"
    )
    groups = sorted(workflow_returns)
    denominator = np.asarray([workflow_returns[task] for task in groups])
    base = triggered_by_name["size_only"]
    rng = np.random.default_rng(42)
    draws = rng.integers(0, len(groups), size=(10000, len(groups)))
    gains = {}
    for name, hits in triggered_by_name.items():
        if name == "size_only":
            continue
        differences = np.asarray([
            hits[task] - base[task] for task in groups
        ])
        bootstrap = (
            differences[draws].sum(axis=1) / denominator[draws].sum(axis=1)
        )
        gains[name] = {
            "absolute_recall_gain": float(
                differences.sum() / denominator.sum()
            ),
            "workflow_bootstrap_95pct_ci": [
                float(np.quantile(bootstrap, p)) for p in (.025, .975)
            ],
        }
    return {
        "status": "development_only_training_threshold_no_online_progress",
        "heldout_project": heldout,
        "coverage": coverage,
        "results": results,
        "paired_vs_size_only": gains,
        "limitations": (
            "Only same-request, same-context causally prior decode samples "
            "are model inputs; server completion is used retrospectively "
            "to ensure an active request. Baseline and candidates share "
            "the same service-eligible rounds. Server progress is not "
            "exposed to the online scheduler; no H2D claim."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, action="append", required=True)
    parser.add_argument("--service-run", type=Path, action="append", required=True)
    parser.add_argument("--heldout-project", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    if len(args.workflows) != len(args.service_run):
        raise ValueError("workflow and service run counts must match")
    result = evaluate(args.workflows, args.service_run, args.heldout_project)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
