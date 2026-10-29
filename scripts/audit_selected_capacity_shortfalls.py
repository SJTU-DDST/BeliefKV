#!/usr/bin/env python3
"""Count observed consumer shortfalls without claiming mandatory new writeback."""

from collections import Counter
from datetime import datetime, timezone
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.arrival_mean120_20261029.queue_pair import complete_work
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def summarize(records):
    shortfalls, geometry, submitted, refusals = [], [], [], []
    attempts, stages, seen = set(), Counter(), Counter()
    for row in records:
        event = row.get("event")
        if event == "selected_consumer_capacity_shortfall":
            names = (
                "device_free_tokens", "missing_full_tokens", "input_tokens",
                "growth_reserve_tokens", "required_device_tokens", "deficit_tokens",
            )
            if any(type(row.get(name)) is not int or row[name] < 0 for name in names):
                raise ValueError("consumer shortfall requires nonnegative observed token counts")
            if (
                row["deficit_tokens"] <= 0
                or row["required_device_tokens"] != sum(row[name] for name in (
                    "missing_full_tokens", "input_tokens", "growth_reserve_tokens",
                ))
                or row["deficit_tokens"] != row["required_device_tokens"] - row["device_free_tokens"]
            ):
                raise ValueError("consumer shortfall does not reconcile with actual input and growth")
            key = tuple(row.get(name) for name in (
                "request_id", "workflow_id", "invocation_id", "context_id", "context_epoch", "attempt_id",
            ))
            if (
                any(type(value) is not str or not value for value in key[:4])
                or any(type(value) is not int or value < 0 for value in key[4:])
            ):
                raise ValueError("consumer shortfall identity is incomplete")
            attempts.add(key)
            seen[key] += 1
            stages[row["dispatch_stage"]] += 1
            shortfalls.append(row)
        elif event == "layer_kv_handoff_geometry_shortfall":
            donor, needed = row["private_unbacked_donor_tokens"], row["minimum_donor_tokens"]
            if (
                type(donor) is not int or type(needed) is not int
                or not 0 <= donor < needed
                or row["required_reserve_tokens"] != row["input_tokens"] + row["growth_reserve_tokens"]
            ):
                raise ValueError("private donor shortfall geometry is inconsistent")
            available, restore = row["device_free_tokens"], row["restore_tokens"]
            expected = (
                restore if available is None else max(
                    1, restore - max(0, available - row["required_reserve_tokens"]),
                )
            )
            if needed != expected:
                raise ValueError("private donor requirement lost actual free extent or reserve")
            geometry.append(row)
        elif event == "layer_kv_handoff_submitted":
            submitted.append(row)
        elif event == "layer_kv_handoff_refused":
            refusals.append(row)
    return {
        "capacity_shortfall_observations": len(shortfalls),
        "distinct_request_attempts": len(attempts),
        "distinct_request_ids": len({key[0] for key in attempts}),
        "repeated_observations_of_same_attempt": sum(value - 1 for value in seen.values()),
        "observations_by_dispatch_stage": dict(stages),
        "observed_deficit_tokens": distribution([row["deficit_tokens"] for row in shortfalls]),
        "missing_prefix_tokens": distribution([row["missing_full_tokens"] for row in shortfalls]),
        "handoff_private_geometry_refusals": len(geometry),
        "handoff_private_geometry_distinct_consumers": len({
            row["beneficiary_request_id"] for row in geometry
        }),
        "private_donor_coverage_of_required_recycled_tokens": distribution([
            row["private_unbacked_donor_tokens"] / row["minimum_donor_tokens"] for row in geometry
        ]),
        "handoff_native_refusal_observations": len(refusals),
        "submitted_handoffs": len(submitted),
        "submitted_consumer_request_ids": sorted({
            row["beneficiary_request_id"] for row in submitted
        }),
        "shortfall_observations": shortfalls,
        "private_geometry_refusals": geometry,
        "native_refusal_observations": refusals,
        "semantics": (
            "Observed selected-consumer deficits need space release or waiting at that instant. "
            "They do not establish that a new D2H is mandatory: settled Host-backed release, "
            "completion, another donor or a different scheduling choice may suffice. "
            "Private-path geometry refusals do not enumerate every safe donor."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve earlier complete shortfall audits")
    if complete_work(args.registration) is None:
        raise ValueError("require complete work, fresh drain and actual owned exit")
    plan = causal.load(args.registration)
    arm = Path(plan["run_root"]) / plan["order"][0]
    reader = SealedRecords()
    result = summarize(reader(arm / "opportunities/admission_opportunities.jsonl"))
    runtime = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    if result["capacity_shortfall_observations"] != runtime["counts"].get(
        "tool_offload_capacity_deficit_observations", 0,
    ):
        raise ValueError("complete shortfall journal and observed runtime count differ")
    for path, seal in reader.prefixes.items():
        if (
            Path(path).stat().st_size != seal["complete_prefix_bytes"]
            or sha256_file(Path(path)) != seal["complete_prefix_sha256"]
        ):
            raise ValueError("complete shortfall source changed during audit")
    causal.reservation.write_json(args.output, {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(args.registration.resolve()),
        "registration_sha256": sha256_file(args.registration),
        "summary": result, "input_jsonl_prefixes": reader.prefixes,
        "operator_sha256": sha256_file(Path(__file__)), "performance_accepted": False,
    })
    print({key: value for key, value in result.items() if not isinstance(value, list)})


if __name__ == "__main__":
    main()
