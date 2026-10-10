#!/usr/bin/env python3
"""Measure sampled writer timing overhead on unchanged JSON processing."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beliefkv.runtime.writer_timing import SampledWriterTiming


def benchmark(iterations: int, groups: int) -> dict:
    records = {
        "semantic_frame": {
            "event": "semantic_frame", "content_tail": "report evidence " * 68,
            "request_id": "req", "context_epoch": 1,
        },
        "decode_batch_48": {
            "event": "gpu_service_sample", "batch_size": 48,
            "request_samples": [
                {
                    "request_id": f"req-{index}", "invocation_id": f"child-{index}",
                    "sequence_tokens_before": 32768, "output_tokens_before": 100,
                    "cached_tokens_host": 8192, "mamba_host_hit_length": 1,
                    "token_delta": 1,
                }
                for index in range(48)
            ],
        },
        "closure_64": {
            "event": "terminal_context_cache_sample",
            "nodes": [
                {
                    "node_id": index, "parent_id": index - 1 if index else None,
                    "full_device_tokens": 256, "full_host_tokens": 256,
                    "mamba_device_present": False, "mamba_host_present": False,
                    "full_device_locks": 0, "full_host_locks": 0,
                    "mamba_device_locks": 0, "mamba_host_locks": 0,
                    "full_session_refs": 2, "mamba_session_refs": 0,
                    "pending_write_id": None, "pending_load_id": None,
                }
                for index in range(64)
            ],
        },
    }
    results = {}
    for name, record in records.items():
        expected = json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n"
        timing = SampledWriterTiming()
        samples = {"baseline": [], "sampled": []}
        for group in range(groups + 2):
            variants = ("baseline", "sampled")
            if group % 2:
                variants = tuple(reversed(variants))
            for variant in variants:
                started = time.perf_counter_ns()
                for _ in range(iterations):
                    sample = timing.begin(name) if variant == "sampled" else None
                    encoded = json.dumps(
                        record, separators=(",", ":"), allow_nan=False,
                    ) + "\n"
                    if variant == "sampled":
                        timing.finish(sample)
                    if encoded != expected:
                        raise AssertionError("timing changed encoded evidence")
                elapsed = (time.perf_counter_ns() - started) / iterations
                if group >= 2:
                    samples[variant].append(elapsed)
        results[name] = {
            "encoded_bytes": len(expected.encode()),
            "evidence_equal": True,
            "mean_ns_per_record": {
                variant: mean(values) for variant, values in samples.items()
            },
            "median_ns_per_record": {
                variant: median(values) for variant, values in samples.items()
            },
            "mean_increment_ns_per_record": (
                mean(samples["sampled"]) - mean(samples["baseline"])
            ),
            "processing_timing": timing.snapshot(),
        }
    return {
        "scope": (
            "Synthetic alternating JSON-processing CPU benchmark. Includes exact "
            "encoded-evidence comparisons, excludes queue, disk, prefix processing "
            "and concurrent scheduler work; not a GPU or end-to-end speedup."
        ),
        "groups": groups, "iterations_per_group": iterations,
        "warmup_groups": 2,
        "sampler_sha256": hashlib.sha256(
            (ROOT / "beliefkv/runtime/writer_timing.py").read_bytes(),
        ).hexdigest(),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--groups", type=int, default=16)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.iterations, args.groups) < 1:
        parser.error("iterations and groups must be positive")
    if args.output.exists():
        raise FileExistsError(args.output)
    result = benchmark(args.iterations, args.groups)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        name: {key: row[key] for key in (
            "encoded_bytes", "evidence_equal", "mean_ns_per_record",
            "mean_increment_ns_per_record",
        )}
        for name, row in result["results"].items()
    }, indent=2))


if __name__ == "__main__":
    main()
