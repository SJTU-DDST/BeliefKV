#!/usr/bin/env python3
"""Preserve a bounded scheduler profile with execution and process identities."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.summarize_semantic_h2d_ab import verify_frozen_execution_source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--execution-root", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--parent", type=int, required=True)
    parser.add_argument("--start-ticks", required=True)
    parser.add_argument("--samples", type=int, required=True)
    parser.add_argument("--errors", type=int, required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--rate", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    compressed_path = args.output.with_suffix(".raw.gz")
    if args.output.exists() or compressed_path.exists():
        raise ValueError("preserve existing profile")
    plan = json.loads((args.run_root / "ab_plan.json").read_bytes())
    verification = verify_frozen_execution_source(args.execution_root, plan["code_commit"])
    stat = Path(f"/proc/{args.pid}/stat").read_text()
    identity = stat[stat.rfind(")") + 2:].split()
    if identity[0] == "Z" or int(identity[1]) != args.parent or identity[19] != args.start_ticks:
        raise ValueError("scheduler process identity changed")
    raw = args.raw.read_bytes()
    leaves, inclusive, routes = Counter(), Counter(), Counter()
    for row in raw.decode(errors="backslashreplace").splitlines():
        if not row or row.startswith("#"):
            continue
        stack, amount = row.rsplit(" ", 1)
        count = int(amount)
        frames = [re.sub(r":\d+\)$", ")", frame) for frame in stack.split(";")]
        leaves[frames[-1]] += count
        inclusive.update({frame: count for frame in set(frames)})
        route = next((label for needle, label in (
            ("process_batch_result", "result"), ("run_batch (", "launch"),
            ("get_next_batch", "selection"), ("_write (", "writer"),
        ) if any(needle in frame for frame in frames)), "other")
        routes[route] += count
    assert sum(leaves.values()) == args.samples
    compressed = gzip.compress(raw, mtime=0)
    assert gzip.decompress(compressed) == raw
    compressed_path.write_bytes(compressed)
    report = {
        "schema_version": 1, "created_at": datetime.now().astimezone().isoformat(),
        "run_root": str(args.run_root), "collection_commit": plan["code_commit"],
        "execution_verification": verification,
        "scheduler_identity": {"pid": args.pid, "parent": args.parent,
                               "start_ticks": args.start_ticks},
        "raw_mtime_local": datetime.fromtimestamp(args.raw.stat().st_mtime).astimezone().isoformat(),
        "requested_duration_seconds": args.duration, "requested_rate_hz": args.rate,
        "method": "py-spy record --gil --threads --full-filenames --nonblocking --format raw",
        "recorded_samples": args.samples, "sampling_errors": args.errors,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "compressed_profile": str(compressed_path),
        "compressed_sha256": hashlib.sha256(compressed).hexdigest(),
        "route_samples": dict(routes),
        "leaf_functions": leaves.most_common(),
        "inclusive_functions": inclusive.most_common(),
        "limitations": [
            "GIL stack samples are not CPU/wall accounting or kernel duration.",
            "All sampling errors are retained; the sample is not a full-run profile.",
            "Inclusive functions overlap; their counts cannot be summed as exclusive cost.",
            "Main execution source remains frozen during collection.",
            "The bounded read-only profiler overlaps the live development run.",
            "No native-relative causal gain or JCT saving is inferred.",
        ],
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "samples": args.samples,
                      "errors": args.errors, "routes": routes}))


if __name__ == "__main__":
    main()
