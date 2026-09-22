#!/usr/bin/env python3
"""Write a static SGLang v0.5.20 model migration audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from beliefkv.experiments.model_migration import inspect_migration


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-config", type=Path, required=True, help="Local config.json or model directory")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-profile", type=Path, help="SGLang v0.5.20 target runtime profile JSON")
    args = parser.parse_args(argv)
    report = inspect_migration(args.model_config, target_profile_path=args.target_profile)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "model_migration.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)
    return 0 if report["target_profile"]["profile_compatible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
