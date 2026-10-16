#!/usr/bin/env python3
"""Compare sealed restore windows with observed context geometry and pressure."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analyze_busy_context_control import decode_geometry
from scripts.audit_native_system_costs import distribution, finite_number
from scripts.audit_sealed_wait_prefetch import SealedRecords


def sampled_pressure(
    rows, begin, end, *, clock_field, offset=0.0, fields, event_kind=None,
):
    samples, coverage = [], None
    before, after = None, None
    for row in rows:
        if event_kind is not None and row.get("event") != event_kind:
            continue
        raw = row.get(clock_field)
        if not finite_number(raw):
            raise ValueError(f"invalid pressure clock: {clock_field}")
        timestamp = raw + offset
        coverage = max(timestamp, coverage) if coverage is not None else timestamp
        if timestamp <= begin:
            before = row
        if timestamp <= end:
            after = row
        if begin <= timestamp < end:
            samples.append(row)
    if coverage is None or coverage < end or before is None:
        raise ValueError("pressure window is not fully observed")
    result = {}
    for name, getter in fields.items():
        known = [getter(row) for row in samples]
        valid = [value for value in known if finite_number(value)]
        result[name] = {
            "values": distribution(valid),
            "unknown_samples": len(known) - len(valid),
        }
    return {
        "samples": len(samples),
        "observed_through_ts_ms": coverage,
        "fields": result,
        "boundary_rows": [before, after],
        "semantics": (
            "Distributions of existing samples, not time-weighted occupancy, unique "
            "working-set size, exposed GPU stalls or removable workflow time."
        ),
    }


def counter_delta(before, after):
    if not isinstance(before, dict) or not isinstance(after, dict):
        return {"available": False, "reason": "counter_boundaries_unobserved"}
    if before.get("enabled") is not True or after.get("enabled") is not True:
        return {"available": False, "reason": "native_close_not_enabled"}
    lower, upper = before.get("counts"), after.get("counts")
    if not isinstance(lower, dict) or not isinstance(upper, dict):
        raise ValueError("native-close counters missing")
    if lower.keys() != upper.keys():
        raise ValueError("native-close counter schema changed")
    delta = {}
    for name, initial in lower.items():
        final = upper[name]
        if type(initial) is not int or type(final) is not int or final < initial:
            raise ValueError("native-close counter reset or invalid value")
        delta[name] = final - initial
    return {
        "available": True,
        "counts": delta,
        "semantics": (
            "Differences between preceding sampled snapshots; they do not identify "
            "which physical pages were later restored or prove reclamation causality."
        ),
    }


def matched_cohorts(left, right, minimum=32):
    result = {}
    for name in sorted(left.keys() & right.keys()):
        first, second = left[name], right[name]
        if min(first["batches"], second["batches"]) < minimum:
            continue
        if second["mean_ms"] <= 0:
            raise ValueError("nonpositive reference worker interval")
        result[name] = {
            "candidate": first,
            "reference": second,
            "candidate_mean_ratio": first["mean_ms"] / second["mean_ms"],
        }
    return result


def analyze_arm(run, start, end):
    windows = [
        row for row in run["windows"]
        if row["start_seconds"] == start and row["end_seconds"] == end
    ]
    if len(windows) != 1 or windows[0].get("service_window_observed_complete") is not True:
        raise ValueError("requested complete service window is absent")
    alignment = run["clock_alignment"]
    if alignment.get("native_client_clock_validated") is not True:
        raise ValueError("native/client clock alignment is unverified")
    offset = alignment.get("native_wall_minus_monotonic_ms")
    if not finite_number(offset):
        raise ValueError("native/client clock offset is missing")
    begin = alignment["anchor_unix_ms"] + start * 1000.0
    finish = alignment["anchor_unix_ms"] + end * 1000.0
    arm = Path(run["run"])
    clients = list(arm.glob("client_*/sglang_metrics.jsonl"))
    if len(clients) != 1:
        raise ValueError(f"expected one client metrics stream: {arm}")
    sealed = SealedRecords()
    metrics = sampled_pressure(
        sealed(clients[0]), begin, finish,
        clock_field="monotonic_ts_ms", offset=offset,
        fields={
            name: lambda row, key=name: row.get(key)
            for name in (
                "num_queue_reqs", "num_running_reqs", "num_used_tokens",
                "resident_pressure", "token_usage",
            )
        },
    )
    metrics.pop("boundary_rows")
    pools = sampled_pressure(
        sealed(arm / "server/host_pool_telemetry.jsonl"), begin, finish,
        clock_field="ts_ms", event_kind="host_pool_usage",
        fields={
            name: lambda row, key=name: (
                row.get("pools", {}).get(key, {}).get("used_fraction")
            )
            for name in ("full", "mamba")
        },
    )
    lower, upper = pools.pop("boundary_rows")
    close = counter_delta(
        lower.get("native_session_close_reclaim"),
        upper.get("native_session_close_reclaim"),
    )
    close["boundary_ts_ms"] = [lower["ts_ms"], upper["ts_ms"]]
    geometry = decode_geometry(arm, begin, finish)
    return {
        "run": str(arm),
        "window_ts_ms": [begin, finish],
        "service_window": windows[0],
        "geometry": geometry,
        "client_pressure": metrics,
        "host_pressure": pools,
        "native_close": close,
        "input_jsonl_prefixes": sealed.prefixes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-report", type=Path, required=True)
    parser.add_argument("--arm", action="append", required=True)
    parser.add_argument("--reference", default="native")
    parser.add_argument("--start-seconds", type=float, required=True)
    parser.add_argument("--end-seconds", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output exists; choose a fresh report ID")
    if not (
        finite_number(args.start_seconds) and finite_number(args.end_seconds)
        and 0 <= args.start_seconds < args.end_seconds
    ):
        parser.error("invalid window")
    raw = args.window_report.read_bytes()
    basis = json.loads(raw)
    names = args.arm
    if len(names) != len(set(names)) or args.reference not in names:
        parser.error("unique arms must include the reference")
    arms = {
        name: analyze_arm(basis["runs"][name], args.start_seconds, args.end_seconds)
        for name in names
    }
    reference = arms[args.reference]["geometry"]
    comparisons = {}
    for name, result in arms.items():
        if name == args.reference:
            continue
        geometry = result["geometry"]
        comparisons[name] = {
            "decode": matched_cohorts(geometry["cohorts"], reference["cohorts"]),
            "decode_graph": matched_cohorts(
                geometry["cuda_graph_cohorts"], reference["cuda_graph_cohorts"],
            ),
            "prefill": matched_cohorts(
                geometry["prefill_geometry"]["cohorts"],
                reference["prefill_geometry"]["cohorts"],
            ),
        }
    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "window_source": {
            "path": str(args.window_report), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "start_seconds": args.start_seconds, "end_seconds": args.end_seconds,
        "reference": args.reference, "arms": arms, "matched_cohorts": comparisons,
        "auditor_sources": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__), ROOT / "scripts/analyze_busy_context_control.py",
                ROOT / "scripts/audit_sealed_wait_prefetch.py",
                ROOT / "scripts/audit_native_system_costs.py",
            )
        },
        "semantics": [
            "Completed-window historical development comparison, not a matched causal trial.",
            "Decode bins match batch size and 2048-token mean context; graph bins additionally retain the actual per-batch replay flag, including unknown.",
            "Prefill bins require native work for every row and match batch size, 512-token total work and 2048-token mean prefix; they are not identical requests or kernel timings.",
            "Pressure and reclaim counters use existing sampled telemetry; missing values remain unknown and are not zero.",
            "Host occupancy includes host_pool_usage records only; eviction records in the same stream are not occupancy samples. Client num_used_tokens/resident_pressure are reported activity gauges, not all allocated or evictable HBM pages.",
            "The geometry reader hashes the exact complete-line prefix it consumed through verified window coverage. Other JSONL streams use stat-bounded complete-line prefixes. Cross-file reads are not atomic.",
            "The service basis supplies transfer receipts. Physical transfer time, worker interval, queue time and reclamation counts do not by themselves establish removable JCT or mechanism causality.",
        ],
    }
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "arms": {
            name: {
                "mean_context": result["geometry"]["sequence_tokens_per_request_mean"],
                "decode_worker_mean_ms": result["geometry"]["worker_decode_duration_mean_ms"],
                "client_pressure": result["client_pressure"],
                "host_pressure": result["host_pressure"],
                "native_close": result["native_close"],
            }
            for name, result in arms.items()
        },
    }, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
