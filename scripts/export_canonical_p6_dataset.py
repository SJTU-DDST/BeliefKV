#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from beliefkv.experiments.p6_canonical import (
    characterize_canonical_p6_dataset,
    export_canonical_p6_training_dataset,
)
from beliefkv.experiments.p6_dataset import _write_json_atomic


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export canonical replacement-aware P6 training evidence."
    )
    parser.add_argument("--selection-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--coverage-output", type=Path)
    parser.add_argument(
        "--coverage-only",
        action="store_true",
        help="Recompute coverage for an existing canonical dataset without exporting.",
    )
    args = parser.parse_args()
    if args.coverage_only:
        manifest_path = args.output_dir / "dataset_manifest.json"
        if not manifest_path.is_file():
            raise SystemExit(f"canonical dataset manifest is missing: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = export_canonical_p6_training_dataset(
            args.selection_manifest,
            args.output_dir,
            repository_root=REPOSITORY_ROOT,
        )
    coverage = characterize_canonical_p6_dataset(args.output_dir)
    coverage_output = args.coverage_output or args.output_dir / "coverage_report.json"
    _write_json_atomic(coverage_output, coverage)
    print(
        json.dumps(
            {
                "dataset_manifest": manifest,
                "coverage_report": coverage,
                "coverage_output": str(coverage_output.resolve()),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if coverage["coverage_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
