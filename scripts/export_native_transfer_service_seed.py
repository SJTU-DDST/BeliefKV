#!/usr/bin/env python3
"""Export reconciled controller payload timing, not agent/action reward labels."""

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beliefkv.runtime.native_transfer_service import NativeServiceSample, pool_shape
from scripts.summarize_semantic_h2d_ab import records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.arm / "server/transfer_telemetry.jsonl"
    capacity = json.loads((args.arm / "server/native_capacity_census.json").read_text())["capacity"]
    sizes = {
        "kv": capacity["host_full_bytes"] // capacity["host_full_tokens"],
        "mamba": capacity["host_mamba_bytes"] // capacity["host_mamba_slots"],
    }
    grouped = defaultdict(list)
    seen = set()
    for row in records(source):
        direction = row.get("direction")
        units = row.get("num_tokens_by_pool") or {}
        if (
            row.get("telemetry_origin") != "native_hicache_ack_v0520"
            or row.get("status") != "completed" or direction not in ("h2d", "d2h")
            or set(units) - set(sizes) or not row.get("node_ids")
        ):
            continue
        total = sum(amount * sizes[name] for name, amount in units.items())
        if total <= 0 or total != row.get("actual_bytes") or row["command_id"] in seen:
            continue
        try:
            sample = NativeServiceSample(
                total, row["submit_to_ack_ms"], direction,
                pool_shape(units.get("kv", 0), units.get("mamba", 0)),
                row.get("enqueue_to_submit_ms"),
            )
        except (ValueError, TypeError, KeyError):
            continue
        seen.add(row["command_id"])
        grouped[(direction, sample.shape)].append(sample.__dict__)
    # Preserve size classes and both directions instead of a D2H-dominated tail.
    samples = []
    for group in grouped.values():
        stride = max(1, len(group) // 48)
        samples.extend(group[::stride][-48:])
    result = {
        "schema_version": 1, "kind": "native_transfer_service_seed",
        "model": "Qwen3.5-35B-A3B", "pool_bytes_per_unit": sizes,
        "scope": "controller_completed_payload_timing_only",
        "source": str(source.resolve()),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "action_reward_or_online_eligibility_authorized": False,
        "samples": samples,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "selected": len(samples),
        "available": {str(key): len(group) for key, group in grouped.items()},
        "output": str(args.output),
    }, indent=2))


if __name__ == "__main__":
    main()
