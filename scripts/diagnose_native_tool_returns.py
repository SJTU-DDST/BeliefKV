#!/usr/bin/env python3
"""Evaluate tool-return timing by completed episode and forecast horizon."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.tool_return_diagnostic import diagnose_tool_returns  # noqa: E402
from beliefkv.predictor.structured_frontier import (  # noqa: E402
    FrontierBeliefModel,
    load_evaluation_rows,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--dataset-dir", type=Path, action="append", required=True)
    parser.add_argument("--split", choices=("calibration", "test_id", "test_ood"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw_model = json.loads(args.model.read_text(encoding="utf-8"))
    fit_projects = set(raw_model.get("metadata", {}).get("fit_projects", ()))
    if not fit_projects:
        raise SystemExit("missing training-project provenance")
    rows, _ = load_evaluation_rows(
        args.dataset_dir, split=args.split, allow_formal_local=args.split == "calibration"
    )
    if fit_projects & {str(row.get("project") or "") for row in rows}:
        raise SystemExit("evaluation projects overlap fitting projects")
    waits = []
    for root in args.dataset_dir:
        with (root / "external_waits.jsonl").open(encoding="utf-8") as stream:
            waits.extend(json.loads(line) for line in stream if line.strip())
    result = diagnose_tool_returns(
        FrontierBeliefModel.from_dict(raw_model), rows, waits
    )
    result["split"] = args.split
    result["note"] = (
        "diagnostic on calibration data already used to calibrate this model"
        if args.split == "calibration" else "independent evaluation split"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    temp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
