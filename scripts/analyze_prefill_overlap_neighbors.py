#!/usr/bin/env python3
"""Split prefill result-delivery gaps using exact adjacent native forwards."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path

from scripts.analyze_prefill_turnover import (
    distribution, key, number, request_ids, validate_phase,
)


METRICS = (
    "run_return_to_result_start_ms",
    "run_return_to_next_launch_ms",
    "next_launch_to_result_start_ms",
    "current_post_launch_physical_overlap_ms",
    "current_war_barrier_overlap_ms",
)


def read_neighbors(metadata, wanted):
    path, limit = Path(metadata["source"]), metadata["read_prefix_bytes"]
    digest, consumed = hashlib.sha256(), 0
    found = defaultdict(list)
    with path.open("rb") as stream:
        while consumed < limit:
            line = stream.readline(limit - consumed)
            if not line.endswith(b"\n"):
                raise ValueError("sealed audit prefix ends in an incomplete line")
            digest.update(line)
            consumed += len(line)
            if b'"gpu_service_sample"' not in line:
                continue
            row = json.loads(line)
            if row.get("event") == "gpu_service_sample" and key(row) in wanted:
                found[key(row)].append(row)
    if digest.hexdigest() != metadata["read_prefix_sha256"]:
        raise ValueError("sealed audit prefix hash changed")
    return found


def neighbor(index, identity):
    matches = index.get(identity, ())
    if len(matches) != 1:
        return {"status": "missing" if not matches else "ambiguous"}
    row = matches[0]
    launch, complete = row.get("launch_monotonic_ms"), row.get("complete_monotonic_ms")
    ids = request_ids(row, service=True)
    if (
        row.get("phase") not in ("prefill", "decode") or ids is None
        or not number(launch) or not number(complete) or complete < launch
    ):
        return {"status": "invalid"}
    return {
        "status": "observed", "phase": row["phase"], "batch_size": row["batch_size"],
        "launch_monotonic_ms": launch, "complete_monotonic_ms": complete,
        "request_ids": ids,
    }


def overlap(begin, end, phase):
    if phase is None or not validate_phase(phase):
        return None
    return max(
        0., min(end, phase["end_monotonic_ms"]) - max(begin, phase["start_monotonic_ms"]),
    )


def summarize(rows):
    return {
        "sample_count": len(rows),
        "next_launch_relations": dict(Counter(row["next_launch_relation"] for row in rows)),
        "metrics": {
            name: distribution([
                row["metrics"][name] for row in rows if name in row["metrics"]
            ]) for name in METRICS
        },
    }


def analyze(samples, index):
    rows, rejected = [], Counter()
    for sample in samples:
        identity = key(sample)
        if identity is None:
            rejected["invalid_sample_identity"] += 1
            continue
        current = neighbor(index, identity)
        if (
            current["status"] != "observed" or current["phase"] != "prefill"
            or current["request_ids"] != tuple(sample["request_ids"])
            or current["batch_size"] != sample["batch_size"]
        ):
            rejected[f"current_service/{current['status']}"] += 1
            continue
        run = sample["phases"].get("run_batch")
        result = sample["phases"].get("result_processing")
        if run is None or result is None or not validate_phase(run) or not validate_phase(result):
            rejected["invalid_or_missing_current_phases"] += 1
            continue
        begin, end = run["end_monotonic_ms"], result["start_monotonic_ms"]
        if end < begin:
            rejected["result_precedes_run_return"] += 1
            continue
        previous = neighbor(index, (identity[0], identity[1] - 1))
        following = neighbor(index, (identity[0], identity[1] + 1))
        metrics = {"run_return_to_result_start_ms": end - begin}
        for name, phase in (
            ("current_post_launch_physical_overlap_ms", "post_launch_physical"),
            ("current_war_barrier_overlap_ms", "war_barrier_and_queue_publish"),
        ):
            duration = overlap(begin, end, sample["phases"].get(phase))
            if duration is not None:
                metrics[name] = duration
        relation = following["status"]
        if following["status"] == "observed":
            launch = following["launch_monotonic_ms"]
            if launch < current["launch_monotonic_ms"] or launch < begin:
                relation = "invalid_next_launch_chronology"
            elif launch > end:
                relation = "result_started_before_next_launch"
            else:
                relation = "next_launch_inside_delivery_gap"
                metrics["run_return_to_next_launch_ms"] = launch - begin
                metrics["next_launch_to_result_start_ms"] = end - launch
        rows.append({
            "forward_iter": identity[1], "monotonic_clock_domain": identity[0],
            "request_ids": sample["request_ids"],
            "batch_size": sample["batch_size"], "extend_tokens": sample["extend_tokens"],
            "prefix_tokens": sample["prefix_tokens"], "loop": sample["loop"],
            "graph": sample["graph"], "previous": previous, "following": following,
            "next_launch_relation": relation, "metrics": metrics,
        })
    short = [row for row in rows if row["batch_size"] == 1 and row["extend_tokens"] < 512]
    groups = defaultdict(list)
    for row in short:
        previous = row["previous"].get("phase", row["previous"]["status"])
        following = row["following"].get("phase", row["following"]["status"])
        groups[f"previous/{previous}/next/{following}/relation/{row['next_launch_relation']}"].append(row)
    return {
        "rejected": dict(rejected), "all_paired": summarize(rows),
        "short_single_request": summarize(short),
        "short_neighbor_cohorts": {name: summarize(values) for name, values in sorted(groups.items())},
        "samples": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("preserve existing neighbor evidence")
    raw = args.phase_report.read_bytes()
    phases = json.loads(raw)
    busy_path = Path(phases["busy_report"])
    busy_raw = busy_path.read_bytes()
    if hashlib.sha256(busy_raw).hexdigest() != phases["busy_report_sha256"]:
        raise ValueError("the phase report references a changed sealed busy report")
    busy = json.loads(busy_raw)
    arms = {}
    for name, original in phases["arms"].items():
        wanted = {
            (domain, forward + offset)
            for row in original["samples"]
            if (identity := key(row)) is not None
            for domain, forward in (identity,) for offset in (-1, 0, 1)
        }
        arms[name] = analyze(original["samples"], read_neighbors(busy["arms"][name], wanted))
    report = {
        "schema_version": 1, "observed_at_local": datetime.now().astimezone().isoformat(),
        "phase_report": str(args.phase_report.resolve()),
        "phase_report_sha256": hashlib.sha256(raw).hexdigest(),
        "busy_report": str(busy_path), "busy_report_sha256": hashlib.sha256(busy_raw).hexdigest(),
        "same_version": phases["same_version"], "arms": arms,
        "analyzer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "semantics": [
            "Only exact same-domain forward_iter minus/plus one is used; no nearest sample or cross-clock fallback.",
            "All source bytes are checked against the original sealed complete-line prefix; live append-only growth is excluded.",
            "Only the current and requested neighboring service rows are retained, not every request of the complete trace.",
            "A next launch inside the gap splits observed intervals; it does not isolate next-forward CPU or kernel cost.",
            "Known current physical/barrier overlaps are subsets of the original delivery gap, not additional additive time.",
            "Missing, duplicate or invalid neighbors remain explicit. Different live trajectories and historical versions prevent causal speedup claims.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        name: {"rejected": arm["rejected"], **arm["short_single_request"]}
        for name, arm in arms.items()
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
