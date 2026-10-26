#!/usr/bin/env python3
"""Review completed native work and measured recovery before a Full probe."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent
from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_restore_wait_cohorts import audit as restore_waits
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workload_opportunities import analyze as blocking_opportunities


def recovery_observations(rows, *, bytes_per_token):
    requests = set()
    groups = Counter()
    for row in rows:
        if row.get("kind") != "llm_submit":
            continue
        attrs = row.get("attributes") or {}
        request = attrs.get("request_id")
        if type(request) is not str or not request or request in requests:
            raise ValueError("native first-service requests must have unique identities")
        requests.add(request)
        prompt, device, host = (
            attrs.get("prompt_tokens"), attrs.get("cached_tokens_device"),
            attrs.get("cached_tokens_host"),
        )
        if any(type(value) is not int or value < 0 for value in (prompt, device, host)):
            raise ValueError("missing native prompt or cache counters")
        if device + host > prompt:
            raise ValueError("native cache counters exceed prompt")
        groups["served_requests"] += 1
        groups["prompt_tokens"] += prompt
        groups["host_prefix_tokens"] += host
        groups["requests_with_host_prefix"] += bool(host)
        groups["device_prefix_tokens"] += device
    return {
        **dict(groups),
        "host_prefix_bytes": groups["host_prefix_tokens"] * bytes_per_token,
        "initial_prefill_included_in_prompt_total": True,
        "host_prefix_bytes_are_request_accounting_not_exclusive_pages": True,
    }


def audit(arm, *, expected_workflows):
    arm = Path(arm).resolve()
    if type(expected_workflows) is not int or expected_workflows <= 0:
        raise ValueError("expected workflow population must be positive")
    summaries = list(arm.glob("client_*/summary.json"))
    if len(summaries) != 1:
        raise ValueError("completed native source requires exactly one client summary")
    summary_path = summaries[0]
    summary = json.loads(summary_path.read_bytes())
    workflows = summary.get("workflows", ())
    if (
        summary.get("workflow_count") != expected_workflows
        or summary.get("completed_workflows") != expected_workflows
        or len(workflows) != expected_workflows
        or len({row.get("workflow_id") for row in workflows}) != expected_workflows
        or any(row.get("native_agent_jct_eligible") is not True for row in workflows)
    ):
        raise ValueError("source must retain the complete eligible workflow population")
    paths = sorted((summary_path.parent / "workflows").glob(
        "*/runtime_events.deepagents.jsonl",
    ))
    if len(paths) != expected_workflows:
        raise ValueError("source is missing workflow event files")
    sealed = SealedRecords()
    events = [
        RuntimeEvent.from_dict(row) for path in paths for row in sealed(path)
    ]
    blocking = blocking_opportunities(events)
    if (
        blocking["workflow_count"] != expected_workflows
        or not blocking["complete_event_pairing"]
        or {row["workflow_id"] for row in blocking["agents"]}
        != {row["workflow_id"] for row in workflows}
    ):
        raise ValueError("source blocking intervals or workflow lifecycles are incomplete")
    native_path = arm / "server/native_telemetry_status.json"
    native = json.loads(native_path.read_bytes())
    if (
        native.get("collection_mode") != "native_reactive"
        or native.get("host_tier_enabled") is not True
        or native.get("writer_error")
        or any(native.get(name) != 0 for name in (
            "dropped_records", "failed_records", "pending_request_count", "pending_batch_count",
        ))
    ):
        raise ValueError("native source telemetry is incomplete or not drained")
    capacity_path = arm / "server/native_capacity_census.json"
    capacity = json.loads(capacity_path.read_bytes())["capacity"]
    geometry = (native.get("host_pool_evidence") or {}).get("full") or {}
    units, size = capacity.get("host_full_tokens"), capacity.get("host_full_bytes")
    bytes_per_token = geometry.get("bytes_per_unit")
    if (
        type(units) is not int or units <= 0 or type(size) is not int or size <= 0
        or type(bytes_per_token) is not int or bytes_per_token <= 0
        or geometry.get("capacity_units") != units or bytes_per_token * units != size
    ):
        raise ValueError("source lacks consistent observed Host FULL geometry")
    # Device allocations can include sentinel/padding rows outside token capacity.
    recovery = recovery_observations(
        sealed(arm / "server/runtime_events.sglang.jsonl"),
        bytes_per_token=bytes_per_token,
    )
    if not recovery.get("served_requests"):
        raise ValueError("source has no native served request observations")
    waits = restore_waits(arm)
    expected_waits = (native.get("gpu_restore_wait_probe") or {}).get(
        "counts", {},
    ).get("completed_samples")
    if (
        type(expected_waits) is not int
        or expected_waits != waits["summary"]["sampled_batches"]
    ):
        raise ValueError("native restore-wait sample coverage is incomplete")
    wait_total = waits["summary"]["total_wait_ms"].get("sum", 0.)
    if not math.isfinite(wait_total):
        raise ValueError("native sampled restore wait is not finite")
    prefix_evidence = native.get("context_prefix_reuse_evidence") or {}
    prefix_counts = prefix_evidence.get("counts") or {}
    prefix_loss = prefix_counts.get("previously_served_prefix_recompute_proxy_tokens", 0)
    if type(prefix_loss) is not int or prefix_loss < 0:
        raise ValueError("invalid native old-input prefix-loss accounting")
    opportunity = recovery.get("requests_with_host_prefix", 0) > 0 or prefix_loss > 0
    return {
        "schema_version": 1,
        "arm": str(arm),
        "workflow_count": expected_workflows,
        "all_normal_tails_retained": True,
        "blocking": blocking,
        "native_recovery": recovery,
        "full_pool_geometry": {
            "host_bytes_per_token": bytes_per_token,
            "host_capacity_tokens": units, "host_allocated_bytes": size,
            "device_capacity_tokens": capacity.get("device_full_tokens"),
            "device_allocated_bytes": capacity.get("device_full_bytes"),
            "host_geometry_derived_from_device_capacity": False,
        },
        "native_sampled_restore_waits": waits,
        "context_prefix_reuse_evidence": prefix_evidence,
        "host_pool_evidence": native.get("host_pool_evidence"),
        "accepted_for_full_probe": opportunity,
        "review_reasons": [] if opportunity else [
            "no_native_host_recovery_or_previous_input_prefix_loss_observed",
        ],
        "cold_exclusive_page_availability": "requires_actual_full_native_donor_checks",
        "sampled_waits_prove_removable_jct": False,
        "sampled_waits_expanded_to_all_batches": False,
        "policy_performance_used_for_source_selection": False,
        "tool_test_pass_required": False,
        "input_jsonl_prefixes": {**sealed.prefixes, **waits["input_jsonl_prefixes"]},
        "input_sha256": {
            str(path): sha256_file(path)
            for path in (summary_path, native_path, capacity_path)
        },
        "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--expected-workflows", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve existing source review")
    result = audit(args.arm, expected_workflows=args.expected_workflows)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        name: result[name] for name in (
            "workflow_count", "native_recovery", "accepted_for_full_probe", "review_reasons",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
