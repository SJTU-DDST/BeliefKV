#!/usr/bin/env python3
"""Fit the post-intent RETURN interval on train, then audit held-out projects."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from beliefkv.predictor.completion_lead import (
    CompletionLead,
    completion_signal_records,
    evaluate_completion_lead,
)


def _jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _records(root: Path, workflows: Path):
    paths = sorted(workflows.glob("*/runtime_events.deepagents.jsonl"))
    if not paths:
        raise ValueError(f"no runtime event traces: {workflows}")
    events = [row for path in paths for row in _jsonl(path)]
    return completion_signal_records(_jsonl(root / "reentries.jsonl"), events)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-dataset", type=Path, required=True)
    parser.add_argument("--train-workflows", type=Path, required=True)
    parser.add_argument("--calibration-dataset", type=Path, required=True)
    parser.add_argument("--calibration-workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    train, train_counts = _records(args.train_dataset, args.train_workflows)
    calibration, calibration_counts = _records(
        args.calibration_dataset, args.calibration_workflows
    )
    model = CompletionLead.fit(train)
    result = {
        "model": model.to_dict(),
        "train": train_counts,
        "calibration": {
            **calibration_counts,
            **evaluate_completion_lead(model, calibration),
        },
        "status": "offline_conditional_signal_diagnostic_only",
        "qualification": (
            "Uses completed JOIN cohort and older traces with unknown finish reason; "
            "not a long-horizon JOIN model or an online physical-action gate."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
