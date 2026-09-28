#!/usr/bin/env python3
"""Request-matched, development-only EOS/content first-trigger comparison."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pilot_child_stream_content import collect, evaluate as content_evaluate

THRESHOLDS = ("top20", "0.0001", "0.001", "0.01", "0.05", "0.1", "0.25")


def first_eos_events(row: dict, events: list[dict], tool_chunk_ts: float) -> dict[str, float]:
    first_content_ts = row["snapshots"][0]["ts_ms"]
    found: dict[str, float] = {}
    for event in events:
        attrs = event.get("attributes") or {}
        if (
            event.get("kind") != "structured_action"
            or event.get("invocation_id") != row["invocation_id"]
            or event.get("context_id") != row["context_id"]
            or event.get("context_epoch") != row["context_epoch"]
            or attrs.get("request_id") != row["rid"]
        ):
            continue
        ts = float(event["ts_ms"])
        if not first_content_ts <= ts < min(row["result_ts"], tool_chunk_ts):
            continue
        if attrs.get("beliefkv_child_eos_first_top_hit_shadow"):
            threshold = "top20"
        elif attrs.get("beliefkv_child_eos_shadow") and type(
            attrs.get("eos_top_probability_threshold")
        ) in (int, float):
            threshold = str(attrs["eos_top_probability_threshold"])
        else:
            continue
        if threshold in THRESHOLDS:
            found[threshold] = min(ts, found.get(threshold, ts))
    return found


def score(rows: list[dict], threshold: str) -> dict:
    positives = [row for row in rows if row["label"] == "return"]
    tools = [row for row in rows if row["label"] == "tool"]
    triggered = [row for row in positives if threshold in row["eos"]]
    lead = [
        row["return_ts"] - row["eos"][threshold]
        for row in triggered
    ]
    return {
        "return_rounds": len(positives),
        "tool_rounds_with_content": len(tools),
        "first_triggered_return": len(triggered),
        "first_triggered_tool": sum(threshold in row["eos"] for row in tools),
        "return_trigger_early_over_2000ms": sum(t > 2000 for t in lead),
        "return_trigger_500_to_2000ms": sum(500 <= t <= 2000 for t in lead),
        "return_trigger_late_under_500ms": sum(t < 500 for t in lead),
        "join_last_rounds": sum(row["join_last"] for row in positives),
        "join_last_500_to_2000ms": sum(
            row["join_last"]
            and threshold in row["eos"]
            and 500 <= row["return_ts"] - row["eos"][threshold] <= 2000
            for row in positives
        ),
        "window_hits_by_workflow": dict(sorted(Counter(
            row["task"] for row in triggered
            if 500 <= row["return_ts"] - row["eos"][threshold] <= 2000
        ).items())),
    }


def evaluate(workflows: list[Path], heldout_project: str) -> dict:
    rows, counts = collect(workflows, min_snapshot_chars=1)
    by_task: dict[str, list[dict]] = {}
    eos_manifests = []
    for root in workflows:
        manifest_path = root.parent / "manifest.json"
        if manifest_path.is_file():
            config = json.loads(manifest_path.read_text()).get("config") or {}
            eos_manifests.append({
                "workflows": str(root),
                "collected": bool(config.get("child_eos_shadow")),
                "top_hit": bool(config.get("child_eos_top_hit_shadow")),
            })
        for path in root.glob("*/runtime_events.deepagents.jsonl"):
            by_task[path.parent.name] = [
                json.loads(line) for line in path.read_text().splitlines()
            ]
    if not eos_manifests or not all(item["collected"] for item in eos_manifests):
        raise ValueError("all workflow roots must have EOS collection enabled")

    content_files = {
        path.parent.name: path
        for root in workflows for path in root.glob("*/child_stream_content.jsonl")
    }
    tool_chunk_by_task: dict[str, dict[str, float]] = {}
    for task, path in content_files.items():
        first_tool: dict[str, float] = {}
        for line in path.read_text().splitlines():
            observation = json.loads(line)
            if observation["event"] == "child_stream_content" and observation["tool_chunk"]:
                rid = observation["request_id"]
                first_tool[rid] = min(
                    float(observation["ts_ms"]),
                    first_tool.get(rid, float("inf")),
                )
        tool_chunk_by_task[task] = first_tool
    for row in rows:
        row["eos"] = first_eos_events(
            row, by_task[row["task"]],
            tool_chunk_by_task[row["task"]].get(row["rid"], float("inf")),
        )

    train = [row for row in rows if row["project"] != heldout_project]
    held = [row for row in rows if row["project"] == heldout_project]
    if not train or not held or not any(row["eos"] for row in rows):
        raise ValueError("not enough content/EOS rounds for project holdout")
    train_scores = {threshold: score(train, threshold) for threshold in THRESHOLDS}
    held_scores = {threshold: score(held, threshold) for threshold in THRESHOLDS}
    # Fixed training-only gate. Fail closed when no threshold meets both risks.
    admissible = [
        threshold for threshold, result in train_scores.items()
        if result["tool_rounds_with_content"] >= 20
        and result["return_rounds"] >= 10
        and result["first_triggered_tool"] / result["tool_rounds_with_content"] <= 0.05
        and result["return_trigger_early_over_2000ms"]
        / result["return_rounds"] <= 0.1
    ]
    selected = max(
        admissible, key=lambda threshold: (
            train_scores[threshold]["return_trigger_500_to_2000ms"],
            -train_scores[threshold]["first_triggered_tool"],
        ),
    ) if admissible else None
    baselines = content_evaluate(workflows, heldout_project, min_snapshot_chars=1)
    keys = (
        "near_return_2000ms_size_only",
        "near_return_2000ms_progress_only",
        "near_return_2000ms_progress_conditioned_content",
        "near_return_2000ms_progress_conditioned_phase",
    )
    return {
        "scope": "development only: no physical transfer; top-k absence is unknown, not P(EOS)=0",
        "heldout_project": heldout_project,
        "train_projects": sorted({row["project"] for row in train}),
        "collected_manifests": eos_manifests,
        "counts": dict(counts),
        "return_with_first_content": sum(row["label"] == "return" for row in rows),
        "tool_with_first_content": sum(row["label"] == "tool" for row in rows),
        "train_eos_threshold_scores": train_scores,
        "heldout_eos_threshold_scores": held_scores,
        "selected_threshold_from_train": selected,
        "selected_heldout_score": held_scores[selected] if selected else None,
        "same_rows_length_progress_baselines": {
            key: baselines["results"][key] for key in keys
        } if baselines["status"].startswith("diagnostic_") else baselines,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, action="append", required=True)
    parser.add_argument("--heldout-project", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args.workflows, args.heldout_project)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "heldout_project": args.heldout_project,
        "threshold": report["selected_threshold_from_train"],
        "heldout_score": report["selected_heldout_score"],
    }, indent=2))


if __name__ == "__main__":
    main()
