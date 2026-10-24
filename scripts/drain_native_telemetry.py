#!/usr/bin/env python3
"""Drain a completed native capture before its owning shell stops the server."""

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import run_causal_comparison as comparison


class ServerPidProbe:
    def __init__(self, pid):
        self.pid = pid
        self.identity = comparison.reservation.process(pid)

    def poll(self):
        try:
            current = comparison.reservation.process(self.pid)
        except (OSError, ValueError):
            return 1
        return (
            None if current and self.identity
            and type(self.identity.get("start_ticks")) is str
            and self.identity["start_ticks"].isdigit()
            and current.get("start_ticks") == self.identity.get("start_ticks")
            and current.get("state") in ("R", "S", "D", "I") else 1
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--server-pid", type=int, required=True)
    args = parser.parse_args(argv)
    if args.server_pid <= 0:
        parser.error("server PID must be positive")
    pause_file = Path(os.environ.get(
        "BELIEFKV_EXPERIMENT_PAUSE_FILE", "/tmp/beliefkv-experiments.paused",
    ))

    def pulse():
        if pause_file.exists():
            raise ValueError("experiment paused while draining native telemetry")

    comparison.wait_for_telemetry_drain(
        args.arm, server=ServerPidProbe(args.server_pid), pulse=pulse,
    )


if __name__ == "__main__":
    main()
