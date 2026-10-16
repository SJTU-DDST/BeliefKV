#!/usr/bin/env python3
"""Associate V1109 barrier samples with exact first-service request cache records."""

from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
from statistics import median


BASE = Path("/home/longhao/experiment")
MAIN = BASE / "BeliefKV"
DEV = BASE / ".worktrees/beliefkv-v25-followup-20261011"
SOURCE = MAIN / "experiments/reports/v1109_mamba_cow_restore_frontier_20261016.json"
NAME = "v1110_mamba_barrier_batch_coupling_20261016.json"


def analyze(arm):
    needed = {rid for sample in arm["samples"] for rid in sample["request_ids"]}
    receipt = arm["receipts"]["events"]
    attrs, consumed, digest = {}, 0, hashlib.sha256()
    with Path(receipt["path"]).open("rb") as stream:
        while consumed < receipt["read_prefix_bytes"]:
            line = stream.readline(receipt["read_prefix_bytes"] - consumed)
            if not line.endswith(b"\n"):
                raise ValueError("sealed first-service prefix changed")
            consumed += len(line)
            digest.update(line)
            if (b'"kind":"llm_submit"' not in line
                    and b'"kind": "llm_submit"' not in line):
                continue
            row = json.loads(line)
            rid = row["attributes"]["request_id"]
            if rid in needed:
                attrs[rid] = {
                    key: row["attributes"][key] for key in (
                        "request_id", "cached_tokens_device",
                        "cached_tokens_host", "mamba_host_hit_slots",
                    )
                }
    if digest.hexdigest() != receipt["read_prefix_sha256"]:
        raise ValueError("sealed first-service prefix digest changed")
    rows = []
    for sample in arm["samples"]:
        matched = [attrs.get(rid) for rid in sample["request_ids"]]
        known = all(item is not None for item in matched)
        rows.append({
            **sample, "request_cache": matched,
            "first_service_cache_complete": known,
            "requests_with_mamba_host_hit": (
                sum(item["mamba_host_hit_slots"] > 0 for item in matched) if known else None
            ),
            "requests_without_any_host_hit": (
                sum(item["cached_tokens_host"] == 0 and item["mamba_host_hit_slots"] == 0
                    for item in matched) if known else None
            ),
        })
    groups = {}
    for route in arm["routes"]:
        selected = [row for row in rows if row["route"] == route]
        known = [row for row in selected if row["first_service_cache_complete"]]
        groups[route] = {
            "sample_count": len(selected),
            "batch_size_counts": dict(Counter(row["batch_size"] for row in selected)),
            "multi_request_batches": sum(row["batch_size"] > 1 for row in selected),
            "all_dependency_wait_sum_ms": sum(row["total_wait_ms"] for row in selected),
            "first_dependency_wait_p50_ms": (
                median(row["first_wait_ms"] for row in selected) if selected else None
            ),
            "first_service_cache_complete_batches": len(known),
            "mixed_first_service_host_and_no_host_requests": sum(
                row["requests_with_mamba_host_hit"] > 0
                and row["requests_without_any_host_hit"] > 0 for row in known
            ),
            "mamba_host_hit_request_count_histogram": dict(Counter(
                row["requests_with_mamba_host_hit"] for row in known
            )),
        }
    return {
        "verified_events_prefix": receipt,
        "groups": groups,
        "pre_forward_barrier_rows": [
            row for row in rows if row["route"] == "pre_forward_last_mamba_barrier"
        ],
    }


def main():
    outputs = [root / "experiments/reports" / NAME for root in (MAIN, DEV)]
    if any(path.exists() for path in outputs):
        raise FileExistsError("preserve prior analysis")
    payload = SOURCE.read_bytes()
    original = json.loads(payload)
    result = {
        "status": "exact_request_cache_association_not_COW_source_allocation_proof",
        "at": datetime.now().astimezone().isoformat(),
        "input": {
            "path": str(SOURCE), "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
        "arms": {label: analyze(arm) for label, arm in original["arms"].items()},
        "limitations": [
            "First-service request counters identify requests, not each later prefill chunk.",
            "One request's COW at batch entry can gate the whole forward stream on "
            "the shared batch layer38 completion event.",
            "Mixed request composition and a coarse wait identify a coupling candidate, "
            "not proof that the particular COW source is absent from the in-flight load.",
            "General trace records do not contain every source/destination COW slot "
            "identity; retain native safety checks until exact dependence is proven.",
            "No all-run timing extrapolation, native-relative causality or JCT saving.",
        ],
        "goal_complete": False,
    }
    encoded = json.dumps(result, indent=2, allow_nan=False) + "\n"
    for path in outputs:
        with path.open("x") as stream:
            stream.write(encoded)
    print(json.dumps({
        "output": str(outputs[0]),
        "arms": {
            label: arm["groups"] for label, arm in result["arms"].items()
        },
    }, indent=2))


if __name__ == "__main__":
    main()
