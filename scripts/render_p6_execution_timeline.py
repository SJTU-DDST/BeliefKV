#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from beliefkv.metrics.execution_timeline import (
    load_execution_timeline,
    render_execution_timeline,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render one BeliefKV arm as an execution/transfer pipeline timeline."
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--arm", required=True)
    parser.add_argument("--output-html", type=Path, required=True)
    parser.add_argument("--title")
    parser.add_argument(
        "--wait-for-pid",
        type=int,
        help="Wait for the experiment driver to exit before reading final telemetry.",
    )
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--gpu-busy-threshold", type=float, default=10.0)
    parser.add_argument(
        "--end-offset-ms",
        type=float,
        help="Truncate the timeline relative to runtime initialization.",
    )
    args = parser.parse_args()
    if args.wait_for_pid is not None:
        if args.wait_for_pid <= 0 or args.poll_seconds <= 0:
            parser.error("wait-for-pid and poll-seconds must be positive")
        identity = _process_identity(args.wait_for_pid)
        print(f"Waiting for experiment driver PID {args.wait_for_pid}", flush=True)
        while identity is not None and _process_identity(args.wait_for_pid) == identity:
            time.sleep(args.poll_seconds)
    timeline = load_execution_timeline(
        args.run_dir,
        arm=args.arm,
        gpu_busy_threshold=args.gpu_busy_threshold,
        end_offset_ms=args.end_offset_ms,
    )
    html_path, data_path = render_execution_timeline(
        timeline,
        args.output_html,
        title=args.title,
    )
    print(
        json.dumps(
            {
                "html": str(html_path),
                "data": str(data_path),
                "summary": timeline.summary,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _process_identity(pid: int) -> str | None:
    try:
        # comm may contain spaces; fields after its final ')' start at state.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] != "Z" else None
    except FileNotFoundError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
