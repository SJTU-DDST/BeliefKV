#!/usr/bin/env python3
"""Account for measured wall intervals before the exact successor run entry."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path

from scripts.analyze_prefill_turnover import distribution, key, number, validate_phase


GAP_PHASES = ("war_barrier_and_queue_publish", "post_launch_physical")
METRICS = (
    "gap_ms", "measured_wall_union_ms", "unmeasured_wall_ms",
    "before_previous_observer_entry_ms", "after_previous_observer_entry_ms",
    "late_measured_wall_union_ms", "late_unmeasured_wall_ms",
)


def partition(begin, end, phases):
    """Clip measured intervals; overlapping labels never double-count time."""
    clipped = {}
    for name, phase in phases.items():
        left = max(begin, phase["start_monotonic_ms"])
        right = min(end, phase["end_monotonic_ms"])
        if right > left:
            clipped[name] = (left, right)
    boundaries = sorted({begin, end, *(point for span in clipped.values() for point in span)})
    segments = [
        {
            "begin_monotonic_ms": left, "end_monotonic_ms": right,
            "wall_ms": right - left,
            "phases": [
                name for name, (start, finish) in clipped.items()
                if start <= left and right <= finish
            ],
        }
        for left, right in zip(boundaries, boundaries[1:]) if right > left
    ]
    measured = sum(segment["wall_ms"] for segment in segments if segment["phases"])
    return {
        "wall_ms": end - begin, "measured_wall_union_ms": measured,
        "unmeasured_wall_ms": max(0., end - begin - measured),
        "observed_phases": sorted(phases),
        "phase_wall_ms": {name: right - left for name, (left, right) in clipped.items()},
        "whole_phase_thread_cpu_ms": {
            name: phase["thread_cpu_ms"] for name, phase in phases.items()
            if begin <= phase["start_monotonic_ms"] <= phase["end_monotonic_ms"] <= end
        },
        "segments": segments,
    }


def summarize(rows):
    return {
        "sample_count": len(rows),
        "previous_observer_relations": dict(Counter(row["previous_observer_relation"] for row in rows)),
        "metrics": {
            name: distribution([row["metrics"][name] for row in rows if name in row["metrics"]])
            for name in METRICS
        },
        "phase_observation_coverage": {
            name: sum(name in row["partition"]["observed_phases"] for row in rows)
            for name in GAP_PHASES
        },
        "phase_wall_ms": {
            name: distribution([
                row["partition"]["phase_wall_ms"].get(name, 0.) for row in rows
                if name in row["partition"]["observed_phases"]
            ])
            for name in GAP_PHASES
        },
        "whole_phase_thread_cpu_ms": {
            name: distribution([
                row["partition"]["whole_phase_thread_cpu_ms"][name] for row in rows
                if name in row["partition"]["whole_phase_thread_cpu_ms"]
            ]) for name in GAP_PHASES
        },
    }


def analyze(samples, neighbors):
    indexed = defaultdict(list)
    for sample in samples:
        if (identity := key(sample)) is not None:
            indexed[identity].append(sample)
    neighbor_counts = Counter(key(row) for row in neighbors if key(row) is not None)
    rows, rejected = [], Counter()
    for neighbor in neighbors:
        identity = key(neighbor)
        if identity is None or neighbor_counts[identity] != 1 or len(indexed[identity]) != 1:
            rejected["missing_or_ambiguous_identity"] += 1
            continue
        sample = indexed[identity][0]
        if any(sample.get(field) != neighbor.get(field) for field in (
            "request_ids", "batch_size", "extend_tokens", "prefix_tokens", "loop", "graph",
        )):
            rejected["sample_identity_or_geometry_disagreement"] += 1
            continue
        previous, following = neighbor["previous"], neighbor["following"]
        if (
            sample["batch_size"] != 1 or sample["extend_tokens"] >= 512
            or previous.get("phase") != "decode" or following.get("phase") != "decode"
            or previous.get("status") != "observed" or following.get("status") != "observed"
            or neighbor.get("next_launch_relation") != "next_launch_inside_delivery_gap"
        ):
            rejected["outside_decode_short_prefill_decode_cohort"] += 1
            continue
        phases = sample.get("phases", {})
        if any(not validate_phase(phases.get(name)) for name in ("run_batch", "result_processing")):
            rejected["invalid_or_missing_gap_boundary"] += 1
            continue
        begin = phases["run_batch"]["end_monotonic_ms"]
        end = following.get("launch_monotonic_ms")
        if not number(end) or not begin <= end <= phases["result_processing"]["start_monotonic_ms"]:
            rejected["invalid_successor_chronology"] += 1
            continue
        measured = {name: phases[name] for name in GAP_PHASES if name in phases}
        if any(not validate_phase(phase) for phase in measured.values()):
            rejected["invalid_measured_phase"] += 1
            continue
        accounting = partition(begin, end, measured)
        metrics = {
            "gap_ms": accounting["wall_ms"],
            "measured_wall_union_ms": accounting["measured_wall_union_ms"],
            "unmeasured_wall_ms": accounting["unmeasured_wall_ms"],
        }
        observer = previous.get("complete_monotonic_ms")
        late = None
        relation = "missing"
        if number(observer):
            relation = "inside" if begin <= observer <= end else "outside"
            if relation == "inside":
                late = partition(observer, end, measured)
                metrics.update({
                    "before_previous_observer_entry_ms": observer - begin,
                    "after_previous_observer_entry_ms": end - observer,
                    "late_measured_wall_union_ms": late["measured_wall_union_ms"],
                    "late_unmeasured_wall_ms": late["unmeasured_wall_ms"],
                })
        rows.append({
            "monotonic_clock_domain": identity[0], "forward_iter": identity[1],
            "request_ids": sample["request_ids"],
            "extend_tokens": sample["extend_tokens"], "prefix_tokens": sample["prefix_tokens"],
            "loop": sample["loop"], "graph": sample["graph"],
            "previous_batch_size": previous["batch_size"],
            "next_batch_size": following["batch_size"],
            "previous_observer_relation": relation,
            "previous_observer_entry_monotonic_ms": observer if number(observer) else None,
            "partition": accounting, "late_partition": late, "metrics": metrics,
        })
    matched_slots = [
        row for row in rows if row["previous_batch_size"] == 47 and row["next_batch_size"] == 48
    ]
    return {
        "rejected": dict(rejected), "decode_short_prefill_decode": summarize(rows),
        "decode47_prefill1_decode48": summarize(matched_slots), "samples": rows,
    }


def load_window(path):
    raw = path.read_bytes()
    neighbors = json.loads(raw)
    phase_path = Path(neighbors["phase_report"])
    phase_raw = phase_path.read_bytes()
    if hashlib.sha256(phase_raw).hexdigest() != neighbors["phase_report_sha256"]:
        raise ValueError("sealed phase report hash changed")
    phases = json.loads(phase_raw)
    busy_path = Path(phases["busy_report"])
    busy_raw = busy_path.read_bytes()
    digest = hashlib.sha256(busy_raw).hexdigest()
    if digest != phases["busy_report_sha256"] or digest != neighbors["busy_report_sha256"]:
        raise ValueError("sealed busy report hash changed")
    if Path(neighbors["busy_report"]).resolve() != busy_path.resolve():
        raise ValueError("neighbor and phase busy-report identities disagree")
    if phases["same_version"] != neighbors["same_version"] or set(phases["arms"]) != set(neighbors["arms"]):
        raise ValueError("neighbor and phase comparison identities disagree")
    busy = json.loads(busy_raw)
    return {
        "neighbor_report": str(path.resolve()),
        "neighbor_report_sha256": hashlib.sha256(raw).hexdigest(),
        "phase_report": str(phase_path.resolve()),
        "phase_report_sha256": hashlib.sha256(phase_raw).hexdigest(),
        "busy_report": str(busy_path.resolve()), "busy_report_sha256": digest,
        "same_version": phases["same_version"],
        "sealed_audit_sources": {
            name: {field: arm[field] for field in (
                "source", "read_prefix_bytes", "read_prefix_sha256", "monitor_window",
            )} for name, arm in busy["arms"].items()
        },
        "arms": {
            name: analyze(phases["arms"][name]["samples"], arm["samples"])
            for name, arm in neighbors["arms"].items()
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--neighbor-report", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("preserve existing transition evidence")
    if len({path.resolve() for path in args.neighbor_report}) != len(args.neighbor_report):
        raise ValueError("do not count a neighbor report twice")
    report = {
        "schema_version": 1, "observed_at_local": datetime.now().astimezone().isoformat(),
        "windows": [load_window(path) for path in args.neighbor_report],
        "analyzer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "semantics": [
            "Consumes exact hashed phase and adjacent-forward reports; does not reread or revalidate the original raw audit bytes.",
            "Adjacent identities inherit the neighbor auditor's same-domain forward_iter minus/plus one proof.",
            "Only decode -> single-request short prefill (<512 extend tokens) -> decode is included.",
            "The successor boundary is scheduler.run_batch entry, not a CUDA launch or kernel boundary.",
            "The previous complete timestamp is entry to the native telemetry observer, not the end of result processing.",
            "Clip intervals per sample and take their union; measured phases are subsets of the gap, never extra additive time.",
            "Unmeasured wall includes telemetry tail, sampling, ingest, next selection, waits and unsampled work; it is not thread CPU or removable overhead.",
            "Thread CPU is reported only for a fully contained measured phase; no proportional CPU allocation to clipped phases.",
            "Missing observer boundaries and missing phases remain explicit; unmeasured time is not assumed idle.",
            "Means and sums can be decomposed within one cohort; marginal quantiles cannot be added or subtracted.",
            "Distinct runs/versions/trajectories prevent a causal performance or JCT-saving claim.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([{
        "neighbor_report": window["neighbor_report"],
        "arms": {
            name: {
                cohort: {
                    "sample_count": arm[cohort]["sample_count"],
                    "phase_observation_coverage": arm[cohort]["phase_observation_coverage"],
                    "mean_ms": {
                        metric: summary["mean_ms"]
                        for metric, summary in arm[cohort]["metrics"].items()
                    },
                } for cohort in ("decode_short_prefill_decode", "decode47_prefill1_decode48")
            } for name, arm in window["arms"].items()
        },
    } for window in report["windows"]], indent=2))


if __name__ == "__main__":
    main()
