#!/usr/bin/env python3
"""Partition observed ACK-to-service waits using independent JOIN/tool boundaries."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_tool_forecast_windows import distribution


PHASES = (
    "before_completion", "completion_to_submit", "submit_to_native", "after_native_arrival",
)
FIELDS = (
    "completion_ts_ms", "client_submit_ts_ms", "native_arrival_ts_ms",
    "first_service_ts_ms", "last_ack_to_first_service_ms",
)


def partition(row: dict) -> dict:
    result = {
        key: row.get(key) for key in (
            "source", "workflow_id", "invocation_id", "context_id", "context_epoch",
            "join_id", "next_request_id", "node_command_count",
            "early_started_and_reused_full_bytes", "remaining_native_full_host_hit_bytes",
            "sampled_batch_restore_dependency_wait_ms", "full_residency_expirations",
        )
    }
    values = [row.get(field) for field in FIELDS]
    if not all(type(value) in (int, float) and math.isfinite(value) for value in values):
        return {**result, "status": "missing_or_invalid_boundary"}
    completion, submit, arrival, service, gap = values
    if not completion <= submit <= arrival <= service:
        return {**result, "status": "reversed_completion_submission_service_chain"}
    if gap < 0:
        return {**result, "status": "service_observed_before_last_ack", "gap_ms": gap}
    ack = service - gap
    boundaries = (ack, completion, submit, arrival, service)
    phases = {
        name: max(0., min(right, service) - max(left, ack))
        for name, left, right in zip(PHASES, boundaries, boundaries[1:])
    }
    if not math.isclose(sum(phases.values()), gap, abs_tol=.01):
        raise ValueError("service phases do not partition the observed ACK interval")
    return {
        **result, "status": "partitioned", "gap_ms": gap, "phases_ms": phases,
        "last_ack_derived_ts_ms": ack,
        "native_arrival_to_first_service_ms": service - arrival,
        "dominant_phase": max(phases, key=phases.get),
    }


def cohort_summary(rows: list[dict]) -> dict:
    expirations = Counter()
    for row in rows:
        expirations.update(
            item["release_stage"] for item in row.get("full_residency_expirations") or ()
        )
    return {
        "events": len(rows), "gap_ms": distribution(row["gap_ms"] for row in rows),
        "phase_ms": {
            name: distribution(row["phases_ms"][name] for row in rows) for name in PHASES
        },
        "dominant_phases": dict(Counter(row["dominant_phase"] for row in rows)),
        "native_arrival_to_first_service_ms": distribution(
            row["native_arrival_to_first_service_ms"] for row in rows
        ),
        "known_zero_remaining_native_full_events": sum(
            row["remaining_native_full_host_hit_bytes"] == 0 for row in rows
        ),
        "positive_remaining_native_full_events": sum(
            (row["remaining_native_full_host_hit_bytes"] or 0) > 0 for row in rows
        ),
        "events_with_sampled_restore_dependencies": sum(
            bool(row.get("sampled_batch_restore_dependency_wait_ms")) for row in rows
        ),
        "full_expiration_commands_by_observed_stage": dict(expirations),
    }


def completion_windows(rows: list[dict]) -> dict:
    result = {}
    for name, left, right in (
        ("completion_to_client_submit", "completion_ts_ms", "client_submit_ts_ms"),
        ("completion_to_native_arrival", "completion_ts_ms", "native_arrival_ts_ms"),
        ("client_submit_to_native_arrival", "client_submit_ts_ms", "native_arrival_ts_ms"),
    ):
        values = []
        for row in rows:
            pair = row.get(left), row.get(right)
            if (
                all(type(value) in (int, float) and math.isfinite(value) for value in pair)
                and pair[0] <= pair[1]
            ):
                values.append(pair[1] - pair[0])
        result[name] = {
            **distribution(values), "missing_or_unordered_events": len(rows) - len(values),
        }
    return result


def evaluate(lifecycle: dict, *, long_gap_ms: float = 5000.) -> dict:
    if not math.isfinite(long_gap_ms) or long_gap_ms < 0:
        raise ValueError("long-gap threshold must be finite and nonnegative")
    groups, detail = {}, []
    for source in ("join_ticket", "tool_wait"):
        waits = [
            row for row in lifecycle["wait_event_attribution"]["rows"] if row["source"] == source
        ]
        rows = [partition(row) for row in waits if row["node_command_count"] > 0]
        valid = [row for row in rows if row["status"] == "partitioned"]
        detail.extend(rows)
        groups[source] = {
            "observed_waits": len(waits), "waits_with_node_commands": len(rows),
            "all_wait_completion_windows_ms": completion_windows(waits),
            "positive_native_full_restore_completion_windows_ms": completion_windows([
                row for row in waits
                if (row.get("remaining_native_full_host_hit_bytes") or 0) > 0
            ]),
            "partition_status": dict(Counter(row["status"] for row in rows)),
            "cohorts": {
                "all_partitioned": cohort_summary(valid),
                "early_verified_full": cohort_summary([
                    row for row in valid if row["early_started_and_reused_full_bytes"] > 0
                ]),
                "no_early_verified_full": cohort_summary([
                    row for row in valid if row["early_started_and_reused_full_bytes"] == 0
                ]),
                "long_gap": cohort_summary([
                    row for row in valid if row["gap_ms"] >= long_gap_ms
                ]),
            },
        }
    return {
        "scope": (
            "Observed independent waits with node actions; ACK time is derived from "
            "the lifecycle audit's last-ACK interval. Phase intersections partition "
            "that interval and do not add quantiles. Native service timestamps are "
            "observed scheduler/model-worker intervals, not CUDA launch or kernel time. "
            "Early verified reuse is an outcome-conditioned cohort, not a causal "
            "scheduling comparison. Native-arrival waiting includes admission, "
            "restoration and control work; it is not pure GPU queueing. No FULL Host "
            "hit does not establish complete Mamba readiness. Missing restore samples "
            "are unknown, and no exposed-stall or counterfactual saving is inferred."
            " Completion windows include independent waits without node commands; "
            "positive native FULL restore is an explicitly outcome-conditioned subset. "
            "These endpoint intervals include notification/planning work and are "
            "not guaranteed available DMA overlap."
        ),
        "long_gap_diagnostic_threshold_ms": long_gap_ms, "sources": groups, "rows": detail,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    payload = args.lifecycle_audit.read_bytes()
    result = evaluate(json.loads(payload))
    result["input"] = {
        "lifecycle_audit": str(args.lifecycle_audit.resolve()),
        "lifecycle_audit_sha256": hashlib.sha256(payload).hexdigest(),
        "audit_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}, indent=2))


if __name__ == "__main__":
    main()
