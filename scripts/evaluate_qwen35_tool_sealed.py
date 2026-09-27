#!/usr/bin/env python3
"""Score frozen unseen-project tool windows and timing with training-only heads."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.evaluate_qwen35_terminal_join_sealed import validate_sealed_run
from scripts.evaluate_tool_balanced_clocks import evaluate


def score_sealed_tool(
    frozen_test: Path,
    provenance: Path,
    train_workflows: list[Path],
    heldout_workflows: Path,
) -> dict:
    identity = validate_sealed_run(
        frozen_test, provenance, heldout_workflows,
    )
    result = evaluate(train_workflows, heldout_workflows)
    if (
        result.get("heldout_workflows") != identity["test_workflows"]
        or result["heldout"]["distinct_tasks"] > identity["test_workflows"]
    ):
        raise ValueError("tool score does not match frozen test identities")
    result.update({
        "status": "project_disjoint_sealed_tool_shadow_not_action_eligible",
        "sealed_identity": identity,
        "scope": (
            "All fitted classifiers, regression heads, the 0.8 window "
            "threshold, and the task-balanced long-call baseline come from "
            "training projects only. The same unseen-project first-input "
            "calls are scored for every head. Long-call timing error is "
            "conditional on a completed tool call lasting at least 600 ms; "
            "report classifier coverage, false windows, and workflow-clustered "
            "uncertainty separately. No physical migration was measured."
        ),
    })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-test", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument(
        "--train-workflows", type=Path, action="append", required=True,
    )
    parser.add_argument("--heldout-workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = score_sealed_tool(
        args.frozen_test, args.provenance,
        args.train_workflows, args.heldout_workflows,
    )
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
