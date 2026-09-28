#!/usr/bin/env python3
"""Project-disjoint first-trigger screen using only delivered stream content."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
from statistics import median
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_child_hidden_trace import (
    blocked_child_invocations, index_workflow,
)


def collect(workflows: Path) -> tuple[list[dict], Counter]:
    rows: list[dict] = []
    counts: Counter = Counter()
    for path in sorted(workflows.glob("*/child_stream_content.jsonl")):
        stats = json.loads(
            (path.parent / "child_stream_content_stats.json").read_text()
        )
        if not stats["complete"]:
            counts["incomplete_workflows"] += 1
            continue
        task = path.parent.name
        project = task.split("__", 1)[0]
        with (path.parent / "runtime_events.deepagents.jsonl").open() as stream:
            events = [json.loads(line) for line in stream]
        terminal, join_last = index_workflow(
            events, blocked_invocations=blocked_child_invocations(path.parent),
        )
        with path.open() as stream:
            observations = [json.loads(line) for line in stream]
        by_rid: dict[str, list[dict]] = defaultdict(list)
        for row in observations:
            by_rid[row["request_id"]].append(row)
        for rid, sequence in by_rid.items():
            results = [row for row in sequence if row["event"] == "child_stream_result"]
            if len(results) != 1:
                counts["censored_or_unfinished_rounds"] += 1
                continue
            result = results[0]
            if result["tool_call_count"] or result["invalid_tool_call_count"]:
                label = "tool"
                counts["tool_rounds"] += 1
            elif rid in terminal and result["finish_reason"] == "stop":
                label = "return"
                counts["natural_return_rounds"] += 1
            else:
                counts["ambiguous_final_rounds"] += 1
                continue
            child, return_ts = terminal.get(rid, (None, None))
            snapshots = []
            last_chars = -1
            for row in sequence:
                if row["event"] != "child_stream_content" or row["tool_chunk"]:
                    continue
                if row["content_chars"] < 128 or row["content_chars"] == last_chars:
                    continue
                if row["finish_reason"]:
                    continue
                if row["ts_ms"] >= result["ts_ms"]:
                    continue
                last_chars = row["content_chars"]
                snapshots.append(row)
            if not snapshots:
                counts[f"{label}_without_eligible_snapshot"] += 1
                continue
            rows.append({
                "task": task, "project": project, "rid": rid, "label": label,
                "join_last": child in join_last if child else False,
                "return_ts": return_ts, "snapshots": snapshots,
            })
    return rows, counts


def features(rows: list[dict]) -> tuple[list[str], np.ndarray, np.ndarray, list[int]]:
    texts, sizes, labels, call_index = [], [], [], []
    for index, row in enumerate(rows):
        for snap in row["snapshots"]:
            texts.append(snap["content_tail"])
            sizes.append([np.log1p(snap["content_chars"])])
            labels.append(int(row["label"] == "return"))
            call_index.append(index)
    return texts, np.asarray(sizes), np.asarray(labels), call_index


def text_features(train: list[str], held: list[str]) -> tuple[np.ndarray, np.ndarray]:
    def ngrams(text: str) -> Counter[str]:
        words = re.findall(r"[a-z_][a-z_0-9]*|[^\s]", text.lower())
        return Counter(
            words + [f"{left} {right}" for left, right in zip(words, words[1:])]
        )

    train_counts = [ngrams(text) for text in train]
    held_counts = [ngrams(text) for text in held]
    document_counts = Counter(token for row in train_counts for token in row)
    vocab = {
        token: index for index, (token, count) in enumerate(
            sorted(
                ((token, count) for token, count in document_counts.items()
                 if count >= 2),
                key=lambda pair: (-pair[1], pair[0]),
            )[:1024]
        )
    }
    if not vocab:
        raise ValueError("not enough repeated training terms")

    def encode(rows: list[Counter[str]]) -> np.ndarray:
        matrix = np.zeros((len(rows), len(vocab)))
        for i, row in enumerate(rows):
            for token, count in row.items():
                if token in vocab:
                    matrix[i, vocab[token]] = np.log1p(count)
        norms = np.linalg.norm(matrix, axis=1)
        return matrix / np.maximum(norms[:, None], 1e-12)

    return encode(train_counts), encode(held_counts)


def evaluate(workflows: Path, heldout_project: str) -> dict:
    rows, counts = collect(workflows)
    train = [row for row in rows if row["project"] != heldout_project]
    held = [row for row in rows if row["project"] == heldout_project]
    if {row["label"] for row in train} != {"return", "tool"} or not held:
        return {
            "status": "insufficient_project_disjoint_samples",
            "counts": dict(counts),
            "train_rounds": len(train), "heldout_rounds": len(held),
        }
    train_text, train_size, train_y, train_idx = features(train)
    held_text, held_size, _, held_idx = features(held)
    train_text_x, held_text_x = text_features(train_text, held_text)
    size_mean, size_std = train_size.mean(), max(train_size.std(), 1e-9)
    held_size = (held_size - size_mean) / size_std
    train_size = (train_size - size_mean) / size_std
    results = {}
    for name, x, x_held in (
        ("size_only", train_size, held_size),
        ("delivered_tail_plus_size",
         np.column_stack((train_text_x, train_size)),
         np.column_stack((held_text_x, held_size))),
    ):
        weights = np.asarray([
            1 / len(train[idx]["snapshots"]) for idx in train_idx
        ])
        positive = np.average(x[train_y == 1], axis=0, weights=weights[train_y == 1])
        negative = np.average(x[train_y == 0], axis=0, weights=weights[train_y == 0])
        direction = positive - negative
        predicted_train = x @ direction
        predicted_held = x_held @ direction
        # Select once on training projects; count false starts by request,
        # never by highly correlated decoding snapshots.
        train_neg_max = [
            max(predicted_train[i] for i, idx in enumerate(train_idx) if idx == j)
            for j, row in enumerate(train) if row["label"] == "tool"
        ]
        if not train_neg_max:
            raise ValueError("training set lacks tool-negative requests")
        threshold = float(np.nextafter(np.quantile(train_neg_max, 0.95), 1.0))
        triggered = defaultdict(list)
        for score, idx, snap in zip(predicted_held, held_idx,
                                    (s for row in held for s in row["snapshots"])):
            if score >= threshold:
                triggered[idx].append(snap["ts_ms"])
        positives = [i for i, row in enumerate(held) if row["label"] == "return"]
        negatives = [i for i, row in enumerate(held) if row["label"] == "tool"]
        lead = [
            held[i]["return_ts"] - min(triggered[i])
            for i in positives if triggered[i]
        ]
        results[name] = {
            "threshold_selected_on_train": threshold,
            "train_tool_round_false_starts": sum(
                score >= threshold for score in train_neg_max
            ),
            "heldout_return_rounds": len(positives),
            "heldout_tool_rounds": len(negatives),
            "heldout_true_first_triggers": len(lead),
            "heldout_false_first_triggers": sum(
                bool(triggered[i]) for i in negatives
            ),
            "heldout_join_last_rounds": sum(held[i]["join_last"] for i in positives),
            "heldout_join_last_triggered": sum(
                bool(triggered[i]) and held[i]["join_last"] for i in positives
            ),
            "lead_ms_median": median(lead) if lead else None,
            "lead_at_least_500ms": sum(t >= 500 for t in lead),
            "lead_at_most_10000ms": sum(t <= 10000 for t in lead),
        }
    return {
        "status": "diagnostic_project_disjoint_in_sample_threshold",
        "heldout_project": heldout_project,
        "counts": dict(counts),
        "train_projects": sorted({row["project"] for row in train}),
        "train_rounds": len(train),
        "heldout_rounds": len(held),
        "results": results,
        "limitations": (
            "Training threshold is selected in sample; no nested validation. "
            "Streamed-mode-only observations; no physical prefetch or H2D claim."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--heldout-project", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.workflows, args.heldout_project)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
