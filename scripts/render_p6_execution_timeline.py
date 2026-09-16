#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

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
    parser.add_argument("--gpu-busy-threshold", type=float, default=10.0)
    args = parser.parse_args()
    timeline = load_execution_timeline(
        args.run_dir,
        arm=args.arm,
        gpu_busy_threshold=args.gpu_busy_threshold,
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


if __name__ == "__main__":
    raise SystemExit(main())
