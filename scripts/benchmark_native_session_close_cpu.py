#!/usr/bin/env python3
"""Measure explicit-close CPU cost with real cache fixtures, not GPU/JCT gains."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import time
from types import SimpleNamespace

from scripts.audit_native_system_costs import distribution


def benchmark(engine: Path, iterations: int) -> dict:
    import sglang.srt.mem_cache.unified_radix_cache as module

    engine = engine.resolve()
    if not Path(module.__file__).resolve().is_relative_to(engine):
        raise ValueError(f"wrong engine imported: {module.__file__}")
    sys.path.insert(0, str(engine / "test/srt"))
    from test_beliefkv_terminal_capacity import cache as cache_fixture
    from test_beliefkv_terminal_capacity import insert_session, match_length

    values = {name: {"cpu_ms": [], "wall_ms": []} for name in ("native", "close_gc")}
    released = {name: 0 for name in values}
    rng = random.Random(21)
    previous = os.environ.get("SGLANG_NATIVE_SESSION_CLOSE_RECLAIM")
    try:
        for iteration in range(iterations + 16):
            order = list(values)
            rng.shuffle(order)
            for name in order:
                os.environ["SGLANG_NATIVE_SESSION_CLOSE_RECLAIM"] = (
                    "1" if name == "close_gc" else "0"
                )
                cache = cache_fixture.__wrapped__(SimpleNamespace(param=512))
                parent, child = list(range(32)), list(range(16)) + list(range(100, 132))
                insert_session(cache, parent, "parent")
                insert_session(cache, child, "child")
                before = cache.token_to_kv_pool_allocator.available_size()
                cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
                cache.release_radix_session("child")
                wall = (time.perf_counter_ns() - wall) / 1_000_000
                cpu = (time.thread_time_ns() - cpu) / 1_000_000
                freed = cache.token_to_kv_pool_allocator.available_size() - before
                if freed != (32 if name == "close_gc" else 0):
                    raise ValueError("unexpected physical FULL release")
                if match_length(cache, parent) != len(parent):
                    raise ValueError("live parent prefix was lost")
                if cache.native_session_close_reclaim_stats()["pending_sessions"]:
                    raise ValueError("unblocked close left pending work")
                if iteration >= 16:
                    values[name]["cpu_ms"].append(cpu)
                    values[name]["wall_ms"].append(wall)
                    released[name] += freed
    finally:
        if previous is None:
            os.environ.pop("SGLANG_NATIVE_SESSION_CLOSE_RECLAIM", None)
        else:
            os.environ["SGLANG_NATIVE_SESSION_CLOSE_RECLAIM"] = previous
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "Randomized CPU-only close calls; construction/insertion excluded. "
            "Close-GC includes physical private-page release; native retains it. "
            "Not identical-work latency, GPU DMA, isolated control saving or JCT."
        ),
        "engine_module": str(Path(module.__file__).resolve()),
        "sources_sha256": {
            str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__), Path(module.__file__),
                engine / "test/srt/test_beliefkv_terminal_capacity.py",
            )
        },
        "iterations_per_mode": iterations,
        "warmup_per_mode": 16,
        "private_full_tokens_per_close": 32,
        "results": {
            name: {
                **{metric: distribution(samples) for metric, samples in metrics.items()},
                "released_full_tokens": released[name],
            } for name, metrics in values.items()
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-checkout", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations <= 0:
        parser.error("iterations must be positive")
    report = benchmark(args.engine_checkout, args.iterations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report["results"], indent=2))


if __name__ == "__main__":
    main()
