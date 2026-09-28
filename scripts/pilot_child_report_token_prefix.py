#!/usr/bin/env python3
"""Read-only screen for information in an observed final-report text prefix.

This scores only natural RETURNs with an identity-matched 1024-character
milestone. Saved final reports stand in for prefixes; they do not prove that
text was delivered to the scheduler at the milestone.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import re
from statistics import mean
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_child_report_prefix_length import load, score
from scripts.audit_oracle_stream_length_bound import THRESHOLD_CHARS, _task_median
from scripts.evaluate_child_return_intent_timing import _metrics


def load_text_prefixes(root: Path) -> tuple[list[dict], dict]:
    rows, audit = load(root)
    reports = {}
    for task in {row["task_id"] for row in rows}:
        for item in json.loads(
            (root / task / "child_reports.json").read_text(encoding="utf-8")
        ):
            summary = (item.get("semantic_completion") or {}).get("summary")
            if isinstance(summary, str):
                key = (task, item["invocation_id"])
                if key in reports:
                    raise ValueError(f"duplicate child report: {key}")
                reports[key] = summary
    result = []
    for row in rows:
        summary = reports[(row["task_id"], row["invocation_id"])]
        if (
            len(summary) < THRESHOLD_CHARS
            or abs(len(summary) - row["final_output_chars_oracle"]) > 5
        ):
            raise ValueError("report does not match observed stream milestone")
        result.append({**row, "observed_prefix": summary[:THRESHOLD_CHARS]})
    return result, audit


def _ngrams(text: str, analyzer: str) -> Counter[str]:
    if analyzer == "word":
        words = re.findall(r"[a-z_][a-z_0-9]*|[^\s]", text.lower())
        return Counter(words + [
            f"{left} {right}" for left, right in zip(words, words[1:])
        ])
    if analyzer == "char":
        text = text.lower()
        return Counter(
            text[index:index + width]
            for width in (3, 4, 5)
            for index in range(len(text) - width + 1)
        )
    raise ValueError("unsupported n-gram analyzer")


def _matrices(
    train: list[dict], test: list[dict], analyzer: str,
) -> tuple[np.ndarray, np.ndarray]:
    train_counts = [_ngrams(row["observed_prefix"], analyzer) for row in train]
    test_counts = [_ngrams(row["observed_prefix"], analyzer) for row in test]
    document_counts = Counter(
        term for row in train_counts for term in row
    )
    terms = sorted(
        (term for term, count in document_counts.items() if count >= 2),
        key=lambda term: (-document_counts[term], term),
    )[:4096]
    if not terms:
        raise ValueError("no supported observed-prefix n-grams")
    vocab = {term: index for index, term in enumerate(terms)}
    idf = np.array([
        1.0 + math.log((len(train) + 1) / (document_counts[term] + 1))
        for term in terms
    ])

    def encode(rows: list[Counter[str]]) -> np.ndarray:
        matrix = np.zeros((len(rows), len(terms)), dtype=np.float64)
        for index, counts in enumerate(rows):
            for term, count in counts.items():
                column = vocab.get(term)
                if column is not None:
                    matrix[index, column] = (1.0 + math.log(count)) * idf[column]
        norm = np.linalg.norm(matrix, axis=1)
        matrix /= np.maximum(norm[:, None], 1e-12)
        return matrix

    return encode(train_counts), encode(test_counts)


def _predict_remaining(
    train: list[dict], test: list[dict], *, analyzer: str, alpha: float
) -> np.ndarray:
    matrix, test_matrix = _matrices(train, test, analyzer)
    target = np.log1p([
        max(0, row["final_output_chars_oracle"] - THRESHOLD_CHARS)
        for row in train
    ])
    counts = defaultdict(int)
    for row in train:
        counts[row["task_id"]] += 1
    weights = np.asarray([1 / counts[row["task_id"]] for row in train])
    weights /= weights.sum()
    center = np.average(matrix, axis=0, weights=weights)
    centered = matrix - center
    centered_test = test_matrix - center
    baseline = float(np.dot(weights, target))
    weighted = centered * np.sqrt(weights[:, None])
    dual = np.linalg.solve(
        weighted @ weighted.T + alpha * np.eye(len(train)),
        np.sqrt(weights) * (target - baseline),
    )
    return np.clip(
        np.expm1(baseline + centered_test @ weighted.T @ dual),
        0, 20_000,
    )


def _time_predictions(
    train: list[dict], test: list[dict], *, analyzer: str, alpha: float
) -> list[float]:
    tail = max(0.0, _task_median(
        train, lambda row: row["lead_ms"]
        - (row["final_output_chars_oracle"] - THRESHOLD_CHARS)
        * row["ms_per_char"],
    ))
    remaining = _predict_remaining(
        train, test, analyzer=analyzer, alpha=alpha
    )
    return [
        max(0.0, float(chars) * row["ms_per_char"] + tail)
        for chars, row in zip(remaining, test)
    ]


def choose_on_train(train: list[dict]) -> tuple[str, float, dict]:
    projects = sorted({row["project"] for row in train})
    if len(projects) < 3:
        raise ValueError("need three training projects for project holdout")
    candidates = {}
    for analyzer in ("word", "char"):
        for alpha in (0.1, 1.0, 10.0):
            by_task = defaultdict(list)
            for project in projects:
                fit = [row for row in train if row["project"] != project]
                held = [row for row in train if row["project"] == project]
                predicted = _time_predictions(
                    fit, held, analyzer=analyzer, alpha=alpha
                )
                for row, estimate in zip(held, predicted):
                    by_task[row["task_id"]].append(
                        abs(row["lead_ms"] - estimate)
                    )
            candidates[f"{analyzer}:{alpha}"] = mean(
                mean(errors) for errors in by_task.values()
            )
    best = min(candidates, key=lambda item: candidates[item])
    analyzer, alpha = best.split(":")
    return analyzer, float(alpha), candidates


def evaluate(train_roots: list[Path], heldout_root: Path) -> dict:
    train, audits, train_tasks = [], {}, set()
    for root in train_roots:
        rows, audits[str(root)] = load_text_prefixes(root)
        tasks = {row["task_id"] for row in rows}
        if train_tasks & tasks:
            raise ValueError("duplicate training workflow")
        train_tasks.update(tasks)
        train.extend(rows)
    held, held_audit = load_text_prefixes(heldout_root)
    if (
        train_tasks & {row["task_id"] for row in held}
        or {row["project"] for row in train}
        & {row["project"] for row in held}
    ):
        raise ValueError("evaluation overlaps training tasks or projects")
    if len(train) < 20 or not held:
        raise ValueError("insufficient identity-matched report prefixes")
    analyzer, alpha, cv_scores = choose_on_train(train)
    predicted = _time_predictions(
        train, held, analyzer=analyzer, alpha=alpha
    )
    join = [index for index, row in enumerate(held) if row["join_last"]]
    return {
        "status": "conditional_final_report_prefix_only",
        "train_count": len(train),
        "heldout_count": len(held),
        "heldout_workflows": len({row["task_id"] for row in held}),
        "join_last_count": len(join),
        "train_projects": sorted({row["project"] for row in train}),
        "heldout_projects": sorted({row["project"] for row in held}),
        "train_audits": audits,
        "heldout_audit": held_audit,
        "train_project_loo_task_mae_ms": cv_scores,
        "selected_text_model": {"analyzer": analyzer, "alpha": alpha},
        "token_prefix": {
            "return": _metrics(
                [row["lead_ms"] for row in held], predicted
            ),
            "join_last_child": _metrics(
                [held[index]["lead_ms"] for index in join],
                [predicted[index] for index in join],
            ),
        },
        "matched_baselines": score(train, held),
        "limitation": (
            "Positive natural-RETURN reports only; saved report prefixes "
            "are not proven online token delivery. Does not measure false "
            "starts, non-final rounds, live latency, or physical H2D."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-root", type=Path, action="append", required=True)
    parser.add_argument("--heldout-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.train_root, args.heldout_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "heldout_count": result["heldout_count"],
        "join_last_count": result["join_last_count"],
        "selected_text_model": result["selected_text_model"],
        "token_prefix": result["token_prefix"],
        "matched_baselines": result["matched_baselines"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
