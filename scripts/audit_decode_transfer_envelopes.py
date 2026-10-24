#!/usr/bin/env python3
"""Compare recorded decode intervals against conservative native copy bounds."""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from statistics import mean, median
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.run_causal_comparison import receipt_work


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class EnvelopeIndex:
    def __init__(self, spans):
        merged = []
        for begin, end in sorted(spans):
            if not finite(begin) or not finite(end) or not 0 <= begin <= end:
                raise ValueError("invalid transfer envelope")
            if begin == end:
                continue
            if merged and begin <= merged[-1][1]:
                merged[-1] = merged[-1][0], max(merged[-1][1], end)
            else:
                merged.append((begin, end))
        self.spans = merged
        self.starts = [begin for begin, _ in merged]
        self.ends = [end for _, end in merged]
        self.prefix_ms = [0.0]
        for begin, end in merged:
            self.prefix_ms.append(self.prefix_ms[-1] + (end - begin))

    def overlap_ms(self, begin, end):
        if not finite(begin) or not finite(end) or not 0 <= begin <= end:
            raise ValueError("invalid service interval")
        left, right = bisect_right(self.ends, begin), bisect_left(self.starts, end)
        if left >= right:
            return 0.0
        value = self.prefix_ms[right] - self.prefix_ms[left]
        value -= max(0.0, begin - self.starts[left])
        value -= max(0.0, self.ends[right - 1] - end)
        return max(0.0, value)


def summary(rows):
    values = sorted(row["worker_ms"] for row in rows)
    return {
        "batches": len(rows),
        "output_tokens": sum(row["output_tokens"] for row in rows),
        "worker_interval_sum_ms": sum(values),
        "worker_interval_mean_ms": mean(values) if values else None,
        "worker_interval_p50_ms": median(values) if values else None,
        "worker_interval_p90_ms": values[math.ceil(.9 * len(values)) - 1] if values else None,
        "sum_transfer_envelope_intersections_ms": sum(row["copy_bound_ms"] for row in rows),
    }


def steady_decode(row, index):
    return all(
        neighbor is not None and neighbor["phase"] == "decode"
        and neighbor["requests"] == row["requests"]
        for neighbor in (index.get(row["forward"] - 1), index.get(row["forward"] + 1))
    )


def transfer_bounds(rows):
    spans, counts = defaultdict(list), Counter()
    for row in rows:
        if row.get("status") != "completed" or row.get("direction") not in ("h2d", "d2h"):
            raise ValueError("transfer capture is incomplete or has an unknown direction")
        size = row.get("actual_bytes")
        if type(size) is not int or size < 0:
            raise ValueError("missing physical transfer size")
        counts["operations"] += 1
        if size == 0:
            counts["zero_byte_operations"] += 1
            continue
        begin, end = row.get("submit_ts_ms"), row.get("complete_ts_ms")
        if not finite(begin) or not finite(end) or not 0 <= begin <= end:
            counts["unbounded_positive_byte_operations"] += 1
            continue
        spans[row["direction"]].append((begin, end))
    return spans, counts


def collect(arm):
    reader = SealedRecords()
    status_path = arm / "server/native_telemetry_status.json"
    status = json.loads(status_path.read_bytes())
    if status.get("writer_error") is not None or any(
        status.get(key) != 0 for key in (
            "pending_request_count", "pending_batch_count", "writer_queue_depth",
            "dropped_records", "failed_records",
        )
    ):
        raise ValueError("native capture is not complete and healthy")
    result_path = arm / "replay/summary.json"
    result = json.loads(result_path.read_bytes())
    if any(result.get(key) is not True for key in (
        "causal_execution_completed", "exact_tokens_verified", "original_native_stopping_verified",
    )):
        raise ValueError("comparison requires complete exact-work replay")
    receipts = arm / "replay/native_requests.jsonl"
    if sha256_file(receipts) != result["native_requests_sha256"]:
        raise ValueError("request receipts changed")
    work = receipt_work(receipts)
    if len(work) != result["completed_requests"]:
        raise ValueError("request population changed")
    spans, transfer_counts = transfer_bounds(reader(arm / "server/transfer_telemetry.jsonl"))
    if transfer_counts["operations"] != status["record_counts"]["transfer"]:
        raise ValueError("transfer capture is not settled")
    envelope = EnvelopeIndex([span for group in spans.values() for span in group])
    index, samples, domains, offsets = {}, set(), set(), []
    for row in reader(arm / "server/runtime_audit.jsonl"):
        if row.get("event") != "gpu_service_sample":
            continue
        forward, sample_id = row["forward_iter"], row["sample_id"]
        if forward in index or sample_id in samples:
            raise ValueError("duplicate native forward")
        samples.add(sample_id)
        domains.add(row["monotonic_clock_domain"])
        begin, end, elapsed = (
            row["service_start_ts_ms"], row["complete_ts_ms"], row["service_elapsed_ms"],
        )
        if (
            not all(finite(value) for value in (begin, end, elapsed))
            or begin > end or elapsed < 0 or not math.isclose(end - begin, elapsed, abs_tol=.01)
        ):
            raise ValueError("invalid native service chronology")
        offsets.append(end - row["complete_monotonic_ms"])
        requests = row["request_samples"]
        request_ids = tuple(sys.intern(item["request_id"]) for item in requests)
        if len(request_ids) != row["batch_size"] or len(set(request_ids)) != len(request_ids):
            raise ValueError("invalid native request geometry")
        phase = row["phase"]
        if phase not in ("prefill", "decode"):
            raise ValueError("unknown native phase")
        values = {}
        if phase == "decode":
            if any(
                item["token_delta_semantics"] != "observed_output_ids_delta"
                or any(type(item.get(name)) is not int or item[name] < 0 for name in (
                    "sequence_tokens_before", "output_tokens_before", "token_delta",
                ))
                for item in requests
            ):
                raise ValueError("invalid decode work")
            sequence = [item["sequence_tokens_before"] for item in requests]
            copy_ms = envelope.overlap_ms(begin, end)
            values = {
                "worker_ms": elapsed, "copy_bound_ms": copy_ms,
                "transfer_class": (
                    "unknown_bounds" if transfer_counts["unbounded_positive_byte_operations"]
                    else "intersects_envelope" if copy_ms > 0 else "outside_envelopes"
                ),
                "sequence_sum": sum(sequence), "sequence_max": max(sequence),
                "output_tokens": sum(item["token_delta"] for item in requests),
                "singleton_ordinal": requests[0]["output_tokens_before"] if len(requests) == 1 else None,
            }
        index[forward] = {
            "forward": forward, "phase": phase, "requests": request_ids,
            "batch_size": row["batch_size"], "graph": row["cuda_graph_replayed"], **values,
        }
    expected = status["background_writer_timing"]["categories"]["audit:gpu_service_sample"]["records_seen"]
    if len(samples) != expected or len(domains) != 1 or max(offsets) - min(offsets) > 5:
        raise ValueError("incomplete service capture or incompatible clocks")
    decode = [row for row in index.values() if row["phase"] == "decode"]
    for row in decode:
        row["steady"] = steady_decode(row, index)
        row["neighbors"] = tuple(
            (neighbor["phase"], neighbor["batch_size"]) if neighbor else ("missing", None)
            for neighbor in (index.get(row["forward"] - 1), index.get(row["forward"] + 1))
        )
    classes = {
        name: summary([row for row in decode if row["transfer_class"] == name])
        for name in ("outside_envelopes", "intersects_envelope", "unknown_bounds")
    }
    return {
        "arm": str(arm.resolve()), "decode": summary(decode),
        "by_transfer_envelope": classes,
        "steady_same_request_decode": summary([row for row in decode if row["steady"]]),
        "by_adjacent_phase": {
            str(pair): summary([row for row in decode if tuple(
                neighbor[0] for neighbor in row["neighbors"]
            ) == pair])
            for pair in sorted({tuple(neighbor[0] for neighbor in row["neighbors"]) for row in decode})
        },
        "transfer_bounds": {
            **transfer_counts, "merged_intervals": len(envelope.spans),
            "submit_to_ack_union_ms": envelope.prefix_ms[-1],
        },
        "source_sha256": {str(path.resolve()): sha256_file(path) for path in (status_path, result_path, receipts)},
        "input_jsonl_prefixes": reader.prefixes,
    }, decode, work


def compare(left, right, *, context_bin=65536, maximum_bin=16384, minimum=10, neighbors=False):
    if min(context_bin, maximum_bin, minimum) <= 0:
        raise ValueError("geometry bins and cohort minimum must be positive")
    result = {}
    for cohort in ("all", "steady", "steady_outside_envelopes"):
        grouped = []
        for rows in (left, right):
            groups = defaultdict(list)
            for row in rows:
                if cohort != "all" and not row["steady"]:
                    continue
                if cohort == "steady_outside_envelopes" and row["transfer_class"] != "outside_envelopes":
                    continue
                group = (
                    row["batch_size"], row["sequence_sum"] // context_bin,
                    row["sequence_max"] // maximum_bin, row["graph"],
                )
                if neighbors:
                    group += row["neighbors"]
                groups[group].append(row)
            grouped.append(groups)
        a, b = grouped
        common = sorted(a.keys() & b.keys(), key=str)
        matched = [key for key in common if min(len(a[key]), len(b[key])) >= minimum]
        weight = sum(min(len(a[key]), len(b[key])) for key in matched)
        averages = {
            name: sum(
                min(len(a[key]), len(b[key])) * mean(row["worker_ms"] for row in groups[key])
                for key in matched
            ) / weight if weight else None
            for name, groups in (("left_mean_ms", a), ("right_mean_ms", b))
        }
        result[cohort] = {
            "match_neighbor_phases_and_sizes": neighbors,
            "geometry_cohorts": len(matched), "common_weight": weight,
            "matched_batches": {
                "left": sum(len(a[key]) for key in matched),
                "right": sum(len(b[key]) for key in matched),
            },
            **averages,
            "ratio": (
                averages["left_mean_ms"] / averages["right_mean_ms"]
                if weight and averages["right_mean_ms"] > 0 else None
            ),
        }
    return result


def exact_singletons(left, right):
    groups = []
    for rows in (left, right):
        index = defaultdict(list)
        for row in rows:
            if row["batch_size"] == 1 and row["steady"] and row["transfer_class"] == "outside_envelopes":
                index[(row["requests"][0], row["singleton_ordinal"])].append(row)
        groups.append(index)
    a, b = groups
    common = a.keys() & b.keys()
    valid = [
        key for key in common
        if len(a[key]) == len(b[key]) == 1
        and a[key][0]["sequence_sum"] == b[key][0]["sequence_sum"]
        and a[key][0]["graph"] == b[key][0]["graph"]
        and a[key][0]["output_tokens"] == b[key][0]["output_tokens"]
    ]
    return {
        "matched_ordinals": len(valid),
        "matched_requests": len({key[0] for key in valid}),
        "ambiguous_common_ordinals": sum(len(a[key]) != 1 or len(b[key]) != 1 for key in common),
        "incompatible_common_ordinals": len(common) - len(valid) - sum(
            len(a[key]) != 1 or len(b[key]) != 1 for key in common
        ),
        "left": summary([a[key][0] for key in valid]),
        "right": summary([b[key][0] for key in valid]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True, help="NAME=ARM_PATH")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve existing diagnosis")
    arms, points, work = {}, {}, None
    for entry in args.arm:
        name, separator, raw = entry.partition("=")
        if not separator or not name or name in arms:
            parser.error("--arm requires a distinct NAME=PATH")
        arms[name], points[name], receipts = collect(Path(raw))
        if work is not None and work != receipts:
            raise ValueError("request tokens or stopping conditions differ")
        work = receipts
    if len(arms) != 2:
        parser.error("exactly two complete arms are required")
    left, right = points.values()
    report = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "arms": arms, "left_right_order": list(arms),
        "geometry_matched": compare(left, right),
        "neighbor_geometry_matched": compare(left, right, neighbors=True),
        "exact_single_request_outside_envelopes": exact_singletons(left, right),
        "same_request_tokens_and_stopping_verified": True,
        "analysis_source_sha256": {
            name: sha256_file(ROOT / name) for name in (
                "scripts/audit_decode_transfer_envelopes.py",
                "scripts/audit_sealed_wait_prefetch.py", "scripts/run_causal_comparison.py",
            )
        },
        "performance_accepted": False,
        "scope": [
            "All recorded decode service intervals and ordinary workflow tails are retained.",
            "Submit-to-ACK envelopes conservatively bound completed native H2D/D2H; intersection is possible overlap, not DMA duration or exposed waiting.",
            "Outside envelopes excludes these observed Host transfers, not device-to-device state copies, CPU control, layout/interleaving effects or unobserved transfers.",
            "Any unknown positive-byte transfer bound makes exclusion unknown. Independent complete-line input seals are not an atomic snapshot.",
            "Geometry matches batch size, sum/max context bins and graph. Neighbor matching additionally requires exact preceding/following forward phases and sizes. Steady means those adjacent forwards decode the same ordered requests.",
            "Exact singleton matches request and recorded output ordinal plus context, graph and token delta; these are observation-conditioned cohorts, not a causal ablation.",
            "Worker intervals include runtime/result processing and busy waiting. No pure GPU time, unique root cause, removable JCT or speedup is inferred.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key in (
        "left_right_order", "geometry_matched", "exact_single_request_outside_envelopes",
    )}, indent=2))


if __name__ == "__main__":
    main()
