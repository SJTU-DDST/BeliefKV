#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from beliefkv.experiments.oracle_gpu_replay import OracleGPUReplay
from beliefkv.oracle.contracts import PerfectFutureOracleArm
from beliefkv.runtime.event_channel import UnixDatagramRuntimeEventSink


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay frozen agent demand through SGLang for Oracle O0/O3."
    )
    parser.add_argument("--arm", choices=("o0_current", "o3_joint"), required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--physical-sidecar", type=Path, required=True)
    parser.add_argument("--event-socket", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--replay-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--vocab-size", type=int, default=151_936)
    parser.add_argument("--request-timeout-seconds", type=float, default=3600.0)
    parser.add_argument("--proactive-min-wait-ms", type=float, default=5_000.0)
    parser.add_argument("--prefetch-lead-ms", type=float, default=2_000.0)
    return parser.parse_args()


def main() -> int:
    args = _args()
    output_dir = args.output_dir.expanduser().resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    sink = UnixDatagramRuntimeEventSink(args.event_socket)
    try:
        replay = OracleGPUReplay.from_paths(
            truth_path=args.truth.expanduser().resolve(),
            sidecar_path=args.physical_sidecar.expanduser().resolve(),
            arm=PerfectFutureOracleArm(args.arm),
            replay_id=args.replay_id,
            base_url=args.base_url,
            event_sink=sink,
            output_dir=output_dir,
            vocab_size=args.vocab_size,
            request_timeout_s=args.request_timeout_seconds,
            proactive_min_wait_ms=args.proactive_min_wait_ms,
            prefetch_lead_ms=args.prefetch_lead_ms,
        )
        result = asyncio.run(replay.run())
    finally:
        sink.close()
    summary_path = output_dir / "replay_summary.json"
    summary_path.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0 if result.failed_workflows == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
