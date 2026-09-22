#!/usr/bin/env python3
"""Export train-only Qwen3.5 native reactive evidence with per-head gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from beliefkv.experiments.p6_dataset import export_native_reactive_p6_dataset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = export_native_reactive_p6_dataset(
        args.run_dir, args.output_dir, split_manifest=args.split_manifest
    )
    print(
        json.dumps(
            {
                "dataset_manifest": str(
                    (args.output_dir / "dataset_manifest.json").resolve()
                ),
                "formal_local_training_eligible": manifest[
                    "formal_local_training_eligible"
                ],
                "native_request_evidence": manifest["source"][
                    "native_request_evidence"
                ],
                "training_readiness": manifest["training_readiness"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if manifest["formal_local_training_eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
