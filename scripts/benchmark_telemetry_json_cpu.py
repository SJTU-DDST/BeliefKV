#!/usr/bin/env python3
"""Compare lossless telemetry encoding on fixed prefixes of real log streams."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
import ujson

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.record_json import encode_record


def collect(path, *, max_lines, per_event):
    limit = path.stat().st_size
    values, counts, digest, consumed = [], Counter(), hashlib.sha256(), 0
    with path.open("rb") as stream:
        for _ in range(max_lines):
            line = stream.readline(limit - consumed)
            if not line or not line.endswith(b"\n"):
                break
            digest.update(line)
            consumed += len(line)
            row = json.loads(line)
            event = row.get("event", row.get("kind", "unknown"))
            if counts[event] < per_event:
                values.append((event, row))
                counts[event] += 1
    return values, {
        "read_complete_prefix_bytes": consumed, "sha256": digest.hexdigest(),
        "sampled_by_event": dict(counts),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-lines", type=int, default=60000)
    parser.add_argument("--per-event", type=int, default=8)
    parser.add_argument("--trials", type=int, default=60)
    args = parser.parse_args()
    rows, seals = [], {}
    for path in args.source:
        values, seal = collect(
            path.resolve(), max_lines=args.max_lines, per_event=args.per_event,
        )
        rows.extend(values)
        seals[str(path.resolve())] = seal
    encoders = {
        "baseline": lambda row: json.dumps(row, separators=(",", ":"), allow_nan=False),
        "optimized": encode_record,
    }
    for _, row in rows:
        expected = json.loads(encoders["baseline"](row))
        assert json.loads(encoders["optimized"](row)) == expected
    categories = sorted({event for event, _ in rows})
    results = {}
    for category in categories + ["all"]:
        values = [row for event, row in rows if category == "all" or event == category]
        times = {name: {"wall_ms": [], "thread_cpu_ms": []} for name in encoders}
        for trial in range(args.trials + 5):
            order = list(encoders)
            if trial % 2:
                order.reverse()
            for name in order:
                wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                for row in values:
                    encoders[name](row)
                thread_ms = (time.thread_time_ns() - cpu) / 1e6
                wall_ms = (time.perf_counter_ns() - wall) / 1e6
                if trial >= 5:
                    times[name]["wall_ms"].append(wall_ms)
                    times[name]["thread_cpu_ms"].append(thread_ms)
        results[category] = {
            "record_count": len(values),
            "costs": {
                name: {
                    **samples, "mean_wall_ms": float(np.mean(samples["wall_ms"])),
                    "p50_wall_ms": float(np.median(samples["wall_ms"])),
                    "mean_thread_cpu_ms": float(np.mean(samples["thread_cpu_ms"])),
                } for name, samples in times.items()
            },
        }
    report = {
        "scope": (
            "CPU encoding only on identical real log records, with parsed values equal. "
            "No queue waiting, filesystem I/O, GIL attribution, GPU/JCT or total-run savings."
        ),
        "stdlib_python": sys.version, "ujson_version": ujson.__version__,
        "source_prefix_seals": seals, "sample_count": len(rows),
        "parsed_records_equal": True, "results": results,
    }
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "sample_count": len(rows), "parsed_records_equal": True,
        "all": results["all"],
    }, indent=2))


if __name__ == "__main__":
    main()
