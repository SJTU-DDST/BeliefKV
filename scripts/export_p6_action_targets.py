#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from beliefkv.predictor.action_targets import (  # noqa: E402
    OperationalActionTargetContract,
    build_action_target_rows,
)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export schema-v4 operational action targets from frozen P6 data."
    )
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    contract = OperationalActionTargetContract.load(args.contract)
    rows, report = build_action_target_rows(
        _read_jsonl(args.dataset_dir / "frontier_decision_points.jsonl"),
        _read_jsonl(args.dataset_dir / "external_waits.jsonl"),
        contract,
    )
    for path, payload, jsonl in (
        (args.output, rows, True),
        (args.report, report, False),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        if jsonl:
            temporary.write_text(
                "".join(json.dumps(item, sort_keys=True) + "\n" for item in payload),
                encoding="utf-8",
            )
        else:
            temporary.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        temporary.replace(path)
    print(json.dumps({"output": str(args.output), "report": report}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
