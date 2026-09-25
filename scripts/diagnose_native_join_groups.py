#!/usr/bin/env python3
"""Diagnose observed JOIN timing on a project-disjoint evaluation split."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.join_group_diagnostic import diagnose_join_groups  # noqa: E402
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
        raise SystemExit("model has no training-project provenance")
    rows, _ = load_evaluation_rows(
        args.dataset_dir, split=args.split, allow_formal_local=args.split == "calibration"
    )
    overlap = fit_projects & {str(row.get("project") or "") for row in rows}
    if overlap:
        raise SystemExit(f"evaluation overlaps fitting projects: {sorted(overlap)}")
    reentries = []
    for root in args.dataset_dir:
        with (root / "reentries.jsonl").open(encoding="utf-8") as stream:
            reentries.extend(json.loads(line) for line in stream if line.strip())
    result = diagnose_join_groups(FrontierBeliefModel.from_dict(raw_model), rows, reentries)
    result.update({
        "split": args.split,
        "note": "calibration split was already used to calibrate the model"
        if args.split == "calibration" else "independent evaluation split",
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    temp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
