#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from beliefkv.predictor.action_targets import load_action_target_rows
from beliefkv.predictor.structured_frontier import (
    FrontierBeliefModel,
    evaluate_frontier_model,
    load_evaluation_rows,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate action-aligned P6 predictions on a held-out split."
    )
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--dataset-dir", type=Path, action="append", required=True)
    parser.add_argument("--action-target", type=Path, action="append", required=True)
    parser.add_argument(
        "--split", choices=("calibration", "test_id", "test_ood"), required=True
    )
    parser.add_argument("--allow-formal-local", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows, _ = load_evaluation_rows(
        args.dataset_dir,
        split=args.split,
        allow_formal_local=args.allow_formal_local,
    )
    targets = load_action_target_rows(args.action_target)
    metrics = evaluate_frontier_model(
        FrontierBeliefModel.load(args.model), rows, targets
    )
    payload = {
        "schema_version": 1,
        "model": str(args.model.resolve()),
        "split": args.split,
        "decision_point_count": metrics["decision_point_count"],
        "workflow_count": metrics["workflow_count"],
        "action_timing": metrics["wait_slack"],
        "action_head_availability": metrics["action_head_availability"],
        "ood_fallback_rate": metrics["ood_fallback_rate"],
        "boundary": metrics["classification"]["boundary"],
        "tool_terminal": metrics["classification"]["tool_terminal"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
