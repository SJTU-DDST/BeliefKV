#!/usr/bin/env python3
"""Freeze one common Poisson arrival table for a complete causal workload."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.replay_arrivals import poisson_arrival_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--rate-per-second", type=float, required=True)
    parser.add_argument("--seed", type=int, default=21)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    registration = json.loads(args.registration.read_bytes())
    plan = poisson_arrival_plan(
        registration["workflow_ids"], rate_per_second=args.rate_per_second, seed=args.seed,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(plan, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "output": str(args.output.resolve()), "workflows": len(plan["arrival_schedule"]),
        "rate_per_second": plan["rate_per_second"],
        "last_arrival_seconds": plan["arrival_schedule"][-1]["offset_seconds"],
        "arrival_schedule_sha256": plan["arrival_schedule_sha256"],
    }))


if __name__ == "__main__":
    main()
