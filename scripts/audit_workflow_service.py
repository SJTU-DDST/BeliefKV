#!/usr/bin/env python3
"""Audit complete workflow first service and submitted-ready service gaps."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.audit_causal_jct_paths import first_launches
from scripts.audit_sealed_wait_prefetch import SealedRecords


def distribution(values):
    values = sorted(values)
    if not values:
        return {"count": 0}

    def quantile(fraction):
        pos = (len(values) - 1) * fraction
        low, high = int(pos), min(int(pos) + 1, len(values) - 1)
        return values[low] + (values[high] - values[low]) * (pos - low)

    return {
        "count": len(values), "mean": statistics.fmean(values),
        "p50": quantile(.5), "p90": quantile(.9), "p99": quantile(.99),
        "max": values[-1],
    }


def merge(intervals):
    result = []
    for start, stop in sorted(intervals):
        if not math.isfinite(start) or not math.isfinite(stop) or stop < start:
            raise ValueError("invalid workflow service interval")
        if start == stop:
            continue
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], stop)
        else:
            result.append([start, stop])
    return result


def subtract(ready, services):
    result, index = [], 0
    for start, stop in ready:
        cursor = start
        while index < len(services) and services[index][1] <= cursor:
            index += 1
        position = index
        while position < len(services) and services[position][0] < stop:
            left, right = services[position]
            if left > cursor:
                result.append([cursor, min(left, stop)])
            cursor = max(cursor, right)
            if cursor >= stop:
                break
            position += 1
        if cursor < stop:
            result.append([cursor, stop])
    return result


def service_metrics(ready, services, *, arrival, terminal, first_launch):
    ready, served = merge(ready), merge(services)
    if not ready or not served or not arrival <= ready[0][0] <= first_launch <= terminal:
        raise ValueError("a completed workflow requires ready and native service evidence")
    gaps = subtract(ready, served)
    post_start = [
        (max(start, served[0][1]), stop)
        for start, stop in gaps if stop > served[0][1]
    ]
    ready_ms = sum(stop - start for start, stop in ready)
    stalled_ms = sum(stop - start for start, stop in gaps)
    return {
        "arrival_to_first_launch_s": (first_launch - arrival) / 1000.,
        "first_submit_to_first_launch_s": (first_launch - ready[0][0]) / 1000.,
        "max_post_start_ready_service_gap_s": max(
            (stop - start for start, stop in post_start), default=0.,
        ) / 1000.,
        "post_start_ready_gap_s": distribution((b - a) / 1000. for a, b in post_start),
        "submitted_ready_s": ready_ms / 1000.,
        "submitted_ready_stall_fraction": stalled_ms / ready_ms,
        "jct_s": (terminal - arrival) / 1000.,
    }


def audit_arm(arm):
    reader = SealedRecords()
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    request_rows = list(reader(arm / "replay/native_requests.jsonl"))
    requests = {row["request_id"]: row for row in request_rows}
    submissions = {
        row["attributes"]["request_id"]: row
        for row in events if row["kind"] == "llm_submit"
    }
    arrivals = {
        row["workflow_id"]: row["ts_ms"] for row in events if row["kind"] == "workflow_start"
    }
    terminals = {
        row["workflow_id"]: row["ts_ms"] for row in events if row["kind"] == "workflow_end"
    }
    if (
        len(requests) != len(request_rows) or not arrivals
        or requests.keys() != submissions.keys() or arrivals.keys() != terminals.keys()
    ):
        raise ValueError("incomplete workflow/request population")
    status_path = arm / "server/native_telemetry_status.json"
    raw_status = status_path.read_bytes()
    status = json.loads(raw_status)
    if any(status.get(name) != 0 for name in (
        "dropped_records", "failed_records", "writer_queue_depth", "pending_batch_count",
    )) or status.get("writer_error") is not None:
        raise ValueError("native telemetry did not drain")
    expected = status["background_writer_timing"]["categories"][
        "audit:gpu_service_sample"
    ]["records_seen"]
    pending, services, grouped = defaultdict(list), defaultdict(list), defaultdict(list)

    def batches():
        for row in reader(arm / "server/runtime_audit.jsonl"):
            if row.get("event") != "gpu_service_sample":
                continue
            workflows = set()
            for sample in row["request_samples"]:
                rid = sample["request_id"]
                if rid in requests:
                    workflow = submissions[rid]["workflow_id"]
                    if workflow != sample["workflow_id"]:
                        raise ValueError("native service changed workflow identity")
                    workflows.add(workflow)
            start, stop = row["service_start_monotonic_ms"], row["complete_monotonic_ms"]
            if not 0 <= start <= stop or not math.isfinite(stop):
                raise ValueError("invalid native worker interval")
            for workflow in workflows:
                services[workflow].append((start, stop))
            yield row

    launches = first_launches(batches(), requests, expected_records=expected)
    for rid, request in requests.items():
        workflow = submissions[rid]["workflow_id"]
        start, stop = request["submitted_monotonic_ms"], request["native_complete_monotonic_ms"]
        if not arrivals[workflow] <= start <= launches[rid] <= stop <= terminals[workflow]:
            raise ValueError("request outside workflow lifetime")
        pending[workflow].append((start, stop))
        grouped[workflow].append(rid)
    records = [
        {
            "workflow_id": workflow, "requests": len(grouped[workflow]),
            "output_tokens": sum(requests[rid]["output_tokens"] for rid in grouped[workflow]),
            **service_metrics(
                pending[workflow], services[workflow], arrival=arrivals[workflow],
                terminal=terminals[workflow],
                first_launch=min(launches[rid] for rid in grouped[workflow]),
            ),
        }
        for workflow in sorted(arrivals)
    ]
    return {
        "arm": str(arm.resolve()), "records": records,
        "workflow_equal_weight_summary": {
            field: distribution(row[field] for row in records) for field in (
                "arrival_to_first_launch_s", "first_submit_to_first_launch_s",
                "max_post_start_ready_service_gap_s", "submitted_ready_stall_fraction",
                "jct_s",
            )
        },
        "native_records_checked": expected, "input_jsonl_prefixes": reader.prefixes,
        "telemetry_status_sha256": hashlib.sha256(raw_status).hexdigest(),
        "metric_contract": {
            "identity": "root and descendants with overlapping service merged",
            "first_service": "actual worker launch, not user token TTFT",
            "ready_gap": "submitted incomplete model work without observed worker service",
            "blocked_only_time_excluded": True, "normal_tails_retained": True,
            "worker_waits_are_inside_service_intervals": True,
            "proves_equal_work_or_exclusive_gpu_fair_share": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous workflow service evidence")
    report = audit_arm(args.arm)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(report["workflow_equal_weight_summary"], indent=2))


if __name__ == "__main__":
    main()
