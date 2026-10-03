#!/usr/bin/env python3
"""Export real reconciled native H2D ACKs; not an action or benefit model."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.summarize_semantic_h2d_ab import records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.arm / "server/transfer_telemetry.jsonl"
    acks = {
        r["command_id"]: r
        for r in records(args.arm / "server/physical_action_ack.jsonl")
        if r["action"] == "PREFETCH_GPU"
    }
    samples = []
    for row in records(source):
        if row.get("direction") != "h2d" or row.get("status") != "completed":
            continue
        children = row.get("tagged_child_commits") or []
        if not children or not all(child["command_id"] in acks for child in children):
            continue
        if sum(child["num_bytes"] for child in children) != row["actual_bytes"]:
            continue
        samples.append({
            "command_id": row["command_id"],
            "actual_bytes": row["actual_bytes"],
            "submit_to_ack_ms": row["submit_to_ack_ms"],
        })
    artifact = {
        "schema_version": 1, "kind": "native_h2d_ack_seed",
        "model": "Qwen3.5-35B-A3B",
        "pool_bytes_per_unit": {"kv": 20480, "mamba": 64389120},
        "timing_boundary": "native_submit_to_synchronized_ack",
        "source": str(source.resolve()),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "scope": "development cold-start timing evidence; no transfer authorization or benefit labels",
        "samples": samples,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2) + "\n")
    print(json.dumps({"samples": len(samples), "path": str(args.output)}))


if __name__ == "__main__":
    main()
