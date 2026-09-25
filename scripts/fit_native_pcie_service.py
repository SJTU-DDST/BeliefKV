#!/usr/bin/env python3
"""Fit split-isolated native PCIe service and evaluate queue when observable."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_pcie_service import fit_native_pcie_service


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-dir", type=Path, required=True)
    parser.add_argument("--calibration-dir", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    inputs = [args.train_dir / "pcie_operations.jsonl",
              args.calibration_dir / "pcie_operations.jsonl",
              args.train_dir / "dataset_manifest.json",
              args.calibration_dir / "dataset_manifest.json", args.split_manifest]
    if args.output.resolve() in {item.resolve() for item in inputs}:
        parser.error("output must not overwrite input evidence")
    artifact = fit_native_pcie_service(
        args.train_dir, args.calibration_dir, split_manifest=args.split_manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"output": str(args.output.resolve()),
                      "hardware_key": artifact["hardware_key"],
                      "acceptance": artifact["acceptance"],
                      "train": artifact["train"]["counts"],
                      "calibration": artifact["calibration"]["counts"]}, indent=2))
    return 0 if artifact["acceptance"]["joint_queue_and_service_eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
