#!/usr/bin/env python3
"""Compare short prefills without treating clipped worker time as forward time."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
from statistics import mean, median


def distribution(values):
    return {
        "count": len(values),
        "mean_ms": mean(values) if values else None,
        "p50_ms": median(values) if values else None,
        "sum_ms": sum(values),
    }


def analyze(metadata):
    begin = metadata["monitor_window"]["begin_ts_ms"]
    end = metadata["monitor_window"]["end_ts_ms"]
    path = Path(metadata["source"])
    limit = metadata["read_prefix_bytes"]
    digest, seen = hashlib.sha256(), 0
    rows = []
    with path.open("rb") as stream:
        while seen < limit:
            line = stream.readline(limit - seen)
            if not line.endswith(b"\n"):
                raise ValueError("incomplete sealed audit line")
            seen += len(line)
            digest.update(line)
            if b'"gpu_service_sample"' not in line:
                continue
            row = json.loads(line)
            if row.get("phase") in ("prefill", "decode"):
                rows.append(row)
    if digest.hexdigest() != metadata["read_prefix_sha256"]:
        raise ValueError("sealed audit prefix changed")
    short, unknown = [], Counter()
    for index, row in enumerate(rows):
        if not begin <= row["complete_ts_ms"] < end or row["phase"] != "prefill":
            continue
        samples = row["request_samples"]
        if len(samples) != row["batch_size"] or not all(
            item.get("prefill_geometry_source") in ("native_batch", "native_request_range")
            for item in samples
        ):
            unknown["prefill_geometry"] += 1
            continue
        tokens = sum(item["token_delta"] for item in samples)
        if row["batch_size"] != 1 or tokens >= 512:
            continue
        launch, start, complete = (
            row.get(key) for key in (
                "launch_monotonic_ms", "service_start_monotonic_ms",
                "complete_monotonic_ms",
            )
        )
        if any(type(value) not in (float, int) for value in (launch, start, complete)):
            unknown["short_prefill_raw_timestamps"] += 1
            continue
        if not launch <= start <= complete:
            raise ValueError("nonmonotonic worker interval")
        graph = row.get("cuda_graph_replayed")
        previous = rows[index - 1] if index else None
        following = rows[index + 1] if index + 1 < len(rows) else None
        short.append({
            "sample_id": row["sample_id"], "complete_ts_ms": row["complete_ts_ms"],
            "request_id": samples[0]["request_id"], "extend_tokens": tokens,
            "prefix_tokens": samples[0]["sequence_tokens_before"],
            "graph": str(graph).lower() if type(graph) is bool else "unknown",
            "worker_interval_ms": row["service_elapsed_ms"],
            "launch_to_processed_completion_ms": complete - launch,
            "clipped_launch_overlap_ms": start - launch,
            "previous_phase": previous["phase"] if previous else "unknown",
            "previous_batch_size": previous["batch_size"] if previous else None,
            "next_phase": following["phase"] if following else "unknown",
            "next_batch_size": following["batch_size"] if following else None,
            "next_launch_minus_complete_ms": (
                following["launch_monotonic_ms"] - complete
                if following and following.get("monotonic_clock_domain")
                == row.get("monotonic_clock_domain")
                and type(following.get("launch_monotonic_ms")) in (float, int)
                else None
            ),
        })
    groups = defaultdict(list)
    for row in short:
        prefix = row["prefix_tokens"] // 2048 * 2048
        groups[f"prefix/{prefix}"].append(row)
        groups[f"prefix/{prefix}/graph/{row['graph']}"].append(row)
        groups[
            f"prefix/{prefix}/graph/{row['graph']}"
            f"/previous/{row['previous_phase']}/{row['previous_batch_size']}"
            f"/next/{row['next_phase']}/{row['next_batch_size']}"
        ].append(row)
    metrics = (
        "worker_interval_ms", "launch_to_processed_completion_ms",
        "clipped_launch_overlap_ms", "next_launch_minus_complete_ms",
    )
    return {
        "input": metadata, "short_prefill_count": len(short),
        "unknown_counts": dict(unknown), "graph_counts": dict(Counter(
            row["graph"] for row in short
        )),
        "distributions": {
            key: distribution([row[key] for row in short if row[key] is not None])
            for key in metrics
        },
        "cohorts": {
            group: {
                key: distribution([row[key] for row in members if row[key] is not None])
                for key in metrics
            }
            for group, members in sorted(groups.items())
        },
        "samples": short,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--busy-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("preserve existing short-prefill evidence")
    source = args.busy_report.read_bytes()
    report = json.loads(source)
    result = {
        "schema_version": 1,
        "analysis_started_at_local": datetime.now().astimezone().isoformat(),
        "busy_report": str(args.busy_report.resolve()),
        "busy_report_sha256": hashlib.sha256(source).hexdigest(),
        "arms": {name: analyze(metadata) for name, metadata in report["arms"].items()},
        "semantics": [
            "Read only exact hashed complete-line prefixes from the prior busy-window report.",
            "Short prefill means one request and fewer than 512 actual native extend tokens.",
            "Graph flags are recorded batch outcomes; unknown values remain unknown.",
            "Launch-to-processed-completion includes overlap and result processing, not pure forward.",
            "Clipped launch overlap is omitted by the published worker interval.",
            "Adjacent observed batches are not proven adjacent physical GPU work.",
            "Matched geometry is not identical demand; no causal gain or JCT saving is inferred.",
        ],
        "analyzer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    result["analysis_finished_at_local"] = datetime.now().astimezone().isoformat()
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        side: {key: data[key] for key in (
            "short_prefill_count", "graph_counts", "distributions",
        )}
        for side, data in result["arms"].items()
    }, indent=2))


if __name__ == "__main__":
    main()
