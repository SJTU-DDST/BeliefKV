#!/usr/bin/env python3
"""Compare real CPU-cache victim order and simulated write-back batch counts."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


def benchmark(engine, seeds):
    import sglang.srt.mem_cache.unified_radix_cache as native

    engine = engine.resolve()
    if not Path(native.__file__).resolve().is_relative_to(engine):
        raise ValueError(f"wrong engine imported: {native.__file__}")
    sys.path.insert(0, str(engine / "test/srt"))
    from test_beliefkv_full_host_eviction import cache as cache_fixture
    from test_beliefkv_write_back_frontier import run_real_frontier

    rows, totals = [], defaultdict(lambda: {
        "fixtures": 0, "batches": 0, "nodes": 0, "freed_full_tokens": 0,
    })
    for seed in range(seeds):
        for requested in (6, 18):
            modes = {
                mode: run_real_frontier(
                    cache_fixture.__wrapped__(), mode=mode,
                    seed=seed, requested=requested,
                )
                for mode in ("single", "chain", "frontier")
            }
            baseline = modes["single"]
            for mode, result in modes.items():
                if any(result[key] != baseline[key] for key in ("evicted", "freed", "nodes")):
                    raise ValueError(f"victims/capacity changed: {seed}/{requested}/{mode}")
                totals[mode]["fixtures"] += 1
                totals[mode]["batches"] += result["batches"]
                totals[mode]["nodes"] += result["nodes"]
                totals[mode]["freed_full_tokens"] += result["freed"]
            if modes["frontier"]["batches"] > modes["chain"]["batches"]:
                raise ValueError("frontier increased submissions in this CPU fixture")
            rows.append({"seed": seed, "requested_full_tokens": requested, "modes": modes})
    return {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "engine": str(engine), "seeds": seeds, "totals": dict(totals), "rows": rows,
        "sources": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__), Path(native.__file__),
                engine / "python/sglang/srt/environ.py",
                engine / "python/sglang/srt/mem_cache/unified_cache/components/full.py",
                engine / "test/srt/test_beliefkv_full_host_eviction.py",
                engine / "test/srt/test_beliefkv_write_back_frontier.py",
            )
        },
        "semantics": [
            "Real CPU cache/tree/allocator and native eviction. DMA completes through simulated ACKs; native operation merging and index mapping are exercised.",
            "Same tree topology and priority seed in every mode; exact canonical prefix victim order, freed FULL tokens and node counts must match.",
            "Batch counts only, not latency. Construction, tiny one-layer storage and fake DMA cannot establish GPU launch saving, PCIe bandwidth, end-to-end throughput or task quality.",
            "Full-only fixture comparison complements the separate real hybrid FULL/Mamba mapping test. No GPU experiment or candidate selection is implied.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-checkout", type=Path, required=True)
    parser.add_argument("--seeds", type=int, default=16)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.seeds <= 0 or args.output.exists():
        parser.error("positive seeds and a fresh output path are required")
    report = benchmark(args.engine_checkout, args.seeds)
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"output": str(args.output), "totals": report["totals"]}, indent=2))


if __name__ == "__main__":
    main()
