#!/usr/bin/env python3
"""Training-only project-LOO replay of frozen causal tool-survival adaptation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_cold_tool_project_loo import require_complete_batch
from scripts.evaluate_cold_tool_structure_holdout import cold_calls
from scripts.evaluate_cold_tool_survival_landmarks import evaluate


def project_leave_one_out(rows: list[dict]) -> dict:
    projects = sorted({row["project"] for row in rows})
    if len(projects) < 3:
        raise ValueError("at least three projects are required")
    return {
        "status": "train_only_project_loo_online_adaptation_not_action_eligible",
        "projects": projects,
        "folds": {
            project: evaluate(
                [row for row in rows if row["project"] != project],
                [row for row in rows if row["project"] == project],
                online_project_history=True,
            )
            for project in projects
        },
        "limitation": (
            "Model priors fit only other training projects; the online arm "
            "uses completed calls of its own evaluated project strictly before "
            "each landmark. This is causal test-time adaptation, not zero-shot "
            "project generalization. Scoring conditions on successful completed "
            "calls and excludes censored/error/intervened calls; no action, "
            "safe-point or physical transfer is validated."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ids, errors = require_complete_batch(args.workflows)
    rows, censor = cold_calls(args.workflows)
    result = project_leave_one_out(rows)
    result["frozen_workflow_count"] = len(ids)
    result["runner_error_workflows"] = errors
    result["censor"] = censor
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": result["status"],
        "projects": result["projects"],
        "frozen_workflow_count": len(ids),
    }, indent=2))


if __name__ == "__main__":
    main()
