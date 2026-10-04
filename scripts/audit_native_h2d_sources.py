#!/usr/bin/env python3
"""Separate predictive commands from native controller H2D payload batches."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.summarize_semantic_h2d_ab import records


def audit(arm: Path) -> dict:
    predictive = {
        row["command_id"] for row in records(arm / "server/physical_action_ack.jsonl")
        if row.get("action") == "PREFETCH_GPU"
    }
    counts = Counter()
    native_units, predictive_units = Counter(), Counter()
    for row in records(arm / "server/transfer_telemetry.jsonl"):
        if row.get("direction") != "h2d":
            continue
        counts["controller_batches"] += 1
        size = row.get("actual_bytes")
        if type(size) is not int:
            counts["unknown_payload_batches"] += 1
            continue
        children = [
            child for child in row.get("tagged_child_commits", [])
            if child["command_id"] in predictive
        ]
        tagged = sum(child["num_bytes"] for child in children)
        counts["total_bytes"] += size
        counts["predictive_bytes"] += tagged
        counts["native_bytes"] += size - tagged
        counts["predictive_batches"] += bool(children)
        counts["native_only_batches"] += not children
        counts["mixed_batches"] += bool(children) and size > tagged
        pools = Counter(row.get("num_tokens_by_pool") or {})
        for child in children:
            for pool, units in child["num_tokens_by_pool"].items():
                predictive_units[pool] += units
                pools[pool] -= units
        native_units.update(pools)
    return {
        "scope": "controller payload batches; no invented request-level byte attribution",
        "counts": dict(counts), "predictive_commands": len(predictive),
        "native_pool_units": dict(native_units),
        "predictive_pool_units": dict(predictive_units),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.arm)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
