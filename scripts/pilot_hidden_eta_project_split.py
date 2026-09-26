#!/usr/bin/env python3
"""Read-only proxy probe for project-held-out decode-stage timing.

Prompts contain issue descriptions, not live child histories. No prompt, decoded
text, or hidden vector is written to disk; this cannot certify JOIN prefetch.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import time
from collections import defaultdict
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

import numpy as np


def select_tasks(path: Path, limit: int) -> dict[str, list[dict]]:
    tasks = defaultdict(list)
    for entry in json.loads(path.read_text())["workloads"]:
        project = entry["instance_id"].split("__", 1)[0]
        tasks[project].append(entry)
    selected = {}
    for project, group in sorted(tasks.items()):
        if len(group) < limit:
            continue
        group = sorted(group, key=lambda task: task["instance_id"])
        random.Random(17).shuffle(group)
        selected[project] = group[:limit]
    return selected


def collect(base_url: str, task: dict, max_tokens: int) -> list[tuple]:
    request_body = {
        "model": "Qwen3.5-35B-A3B",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a software maintenance subagent. Given an issue, "
                    "briefly describe a plausible diagnosis and a concrete "
                    "fix plan in 4-6 sentences. You cannot inspect the repo "
                    "or call tools; do not claim you ran any tests."
                ),
            },
            {
                "role": "user",
                "content": task["problem_statement"][:6000],
            },
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": True,
        "rid": uuid4().hex,
        "return_hidden_states": "last",
    }
    request = Request(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(request_body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    samples = []
    finish_reason = None
    finish_ms = None
    start = time.monotonic()
    with urlopen(request, timeout=180) as response:
        for line in response:
            if not line.startswith(b"data: "):
                continue
            payload = line[6:].strip()
            if payload == b"[DONE]":
                break
            now_ms = (time.monotonic() - start) * 1000
            event = json.loads(payload)
            if event.get("id") != request_body["rid"]:
                raise ValueError("hidden-state SSE changed request identity")
            for choice in event.get("choices") or []:
                if choice["index"] != 0:
                    raise ValueError("unexpected parallel completion")
                hidden = (choice.get("delta") or {}).get("hidden_states")
                if hidden is not None and choice.get("finish_reason") is None:
                    vector = np.asarray(hidden, dtype=np.float32)
                    if vector.shape != (2048,) or not np.isfinite(vector).all():
                        raise ValueError("invalid intermediate hidden state")
                    samples.append((now_ms, vector))
                if choice.get("finish_reason") is not None:
                    finish_reason = choice["finish_reason"]
                    finish_ms = now_ms
    if finish_reason != "stop" or finish_ms is None:
        return []
    return [
        (ordinal, timestamp, finish_ms - timestamp, vector)
        for ordinal, (timestamp, vector) in enumerate(samples, 1)
        if timestamp < finish_ms
    ]


def features(samples: list[tuple], include_hidden: bool) -> np.ndarray:
    columns = []
    for ordinal, timestamp, _, vector in samples:
        base = [
            math.log1p(ordinal * 32),
            math.log1p(timestamp),
            math.log1p(ordinal * 32000 / max(timestamp, 1)),
        ]
        columns.append(
            np.concatenate((base, vector / math.sqrt(2048)))
            if include_hidden else base
        )
    return np.asarray(columns, dtype=np.float64)


def fit_ridge(x: np.ndarray, labels: np.ndarray, penalty: float = 32) -> tuple:
    mean = x.mean(axis=0)
    scale = np.maximum(x.std(axis=0), 0.1)
    normalized = (x - mean) / scale
    target_mean = labels.mean()
    if x.shape[1] <= x.shape[0]:
        weights = np.linalg.solve(
            normalized.T @ normalized + penalty * np.eye(x.shape[1]),
            normalized.T @ (labels - target_mean),
        )
    else:
        weights = normalized.T @ np.linalg.solve(
            normalized @ normalized.T + penalty * np.eye(x.shape[0]),
            labels - target_mean,
        )
    return mean, scale, weights, target_mean


def predict(model: tuple, x: np.ndarray) -> np.ndarray:
    mean, scale, weights, target_mean = model
    return np.expm1(np.clip((x - mean) / scale @ weights + target_mean, 0, 16))


def evaluate(model: tuple, records: list[list[tuple]], hidden: bool) -> dict:
    errors = []
    first_triggers = []
    selected_errors = []
    for samples in records:
        predicted = predict(model, features(samples, hidden))
        actual = np.array([item[2] for item in samples])
        if np.any((actual >= 500) & (actual <= 5000)):
            mask = (actual >= 500) & (actual <= 5000)
            errors.append(float(np.median(np.abs(predicted[mask] - actual[mask]))))
        candidates = np.flatnonzero(predicted <= 1500)
        if len(candidates):
            idx = int(candidates[0])
            first_triggers.append(float(actual[idx]))
            selected_errors.append(float(abs(predicted[idx] - actual[idx])))
    return {
        "natural_stop_requests": len(records),
        "requests_with_500_to_5000ms_samples": len(errors),
        "per_request_median_error_p50_ms": (
            statistics.median(errors) if errors else None
        ),
        "first_triggered_requests": len(first_triggers),
        "first_trigger_500_to_3000ms": sum(
            500 <= lead <= 3000 for lead in first_triggers
        ),
        "first_trigger_early_over_3000ms": sum(
            lead > 3000 for lead in first_triggers
        ),
        "first_trigger_late_under_500ms": sum(
            lead < 500 for lead in first_triggers
        ),
        "first_trigger_abs_error_p50_ms": (
            statistics.median(selected_errors) if selected_errors else None
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18001")
    parser.add_argument("--train-manifest", type=Path, required=True)
    parser.add_argument("--heldout-manifest", type=Path, required=True)
    parser.add_argument("--train-per-project", type=int, default=4)
    parser.add_argument("--heldout-per-project", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=2048)
    args = parser.parse_args()
    if min(args.train_per_project, args.heldout_per_project, args.max_tokens) < 1:
        parser.error("sample limits and max tokens must be positive")
    train_tasks = select_tasks(args.train_manifest, args.train_per_project)
    heldout_tasks = select_tasks(args.heldout_manifest, args.heldout_per_project)
    if set(train_tasks) & set(heldout_tasks):
        parser.error("project split overlaps")
    records = {}
    for split, tasks in (("train", train_tasks), ("heldout", heldout_tasks)):
        records[split] = []
        for project, group in tasks.items():
            for task in group:
                samples = collect(args.base_url, task, args.max_tokens)
                if samples:
                    records[split].append(samples)
            print(
                f"{split} {project}: natural stops "
                f"{len(records[split])}/{sum(map(len, tasks.values()))}",
                flush=True,
            )
    if len(records["train"]) < 5 or len(records["heldout"]) < 5:
        raise RuntimeError("insufficient project-split natural completions")
    training = [sample for request in records["train"] for sample in request]
    labels = np.log1p(np.asarray([sample[2] for sample in training]))
    result = {
        "proxy_only": True,
        "train_projects": sorted(train_tasks),
        "heldout_projects": sorted(heldout_tasks),
        "train_natural_stop_requests": len(records["train"]),
        "heldout_natural_stop_requests": len(records["heldout"]),
        "train_early_snapshots": len(training),
    }
    for hidden in (False, True):
        model = fit_ridge(features(training, hidden), labels)
        result["hidden_plus_progress" if hidden else "progress_only"] = evaluate(
            model, records["heldout"], hidden
        )
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
