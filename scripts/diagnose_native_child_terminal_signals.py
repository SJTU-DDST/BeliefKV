#!/usr/bin/env python3
"""Audit candidate natural/structured child completion signals before JOIN."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.child_terminal_signal import (  # noqa: E402
    summarize_child_terminal_signals,
)


def _jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--workflow-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    event_paths = sorted(args.workflow_root.glob("*/runtime_events.deepagents.jsonl"))
    if not event_paths:
        parser.error("no runtime event traces found")
    events = [event for path in event_paths for event in _jsonl(path)]
    summary = summarize_child_terminal_signals(
        _jsonl(args.dataset_dir / "reentries.jsonl"), events
    )
    summary["trace_file_count"] = len(event_paths)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    temp.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(args.output)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
