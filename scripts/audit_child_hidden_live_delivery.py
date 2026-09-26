#!/usr/bin/env python3
"""Audit received child hidden samples against completed requests and returns."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import numpy as np

try:
    from scripts.audit_child_hidden_trace import (
        blocked_child_invocations, index_workflow,
    )
except ModuleNotFoundError:
    from audit_child_hidden_trace import blocked_child_invocations, index_workflow


def _quantiles(values: list[float]) -> dict | None:
    if not values:
        return None
    return {
        "count": len(values),
        "p50": float(np.percentile(values, 50)),
        "p95": float(np.percentile(values, 95)),
        "max": float(max(values)),
    }


def audit(delivery_path: Path, trace_dir: Path, workflows: Path) -> dict:
    delivery = defaultdict(dict)
    ages = []
    duplicates = 0
    with delivery_path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            key = row["request_sha256"]
            tokens = int(row["token_count"])
            if tokens in delivery[key]:
                duplicates += 1
                continue
            delivery[key][tokens] = row
            ages.append(float(row["transport_age_ms"]))

    terminal = {}
    join_last = set()
    child_requests = set()
    for path in workflows.glob("*/runtime_events.deepagents.jsonl"):
        with path.open(encoding="utf-8") as stream:
            events = [json.loads(line) for line in stream]
        children = {
            event["target_invocation_id"]
            for event in events
            if event["kind"] == "spawn" and event.get("target_invocation_id")
        }
        child_requests.update(
            hashlib.sha256(attrs["request_id"].encode()).hexdigest()
            for event in events
            if event["kind"] in ("llm_submit", "llm_result")
            and event.get("invocation_id") in children
            if (attrs := event.get("attributes") or {}).get("request_id")
        )
        by_request, last_children = index_workflow(
            events, blocked_invocations=blocked_child_invocations(path.parent),
        )
        terminal.update(by_request)
        join_last.update(last_children)

    matched = 0
    matched_requests = 0
    explicit_receipt = 0
    estimated_receipt = 0
    missing_retained = 0
    matched_terminal = set()
    terminal_first_leads = []
    terminal_last_leads = []
    join_first_leads = []
    join_last_leads = []
    late_receipts = 0
    for path in trace_dir.glob("*.npz"):
        if path.stem not in child_requests:
            continue
        with np.load(path, allow_pickle=False) as trace:
            rid = str(trace["rid"].item())
            key = hashlib.sha256(rid.encode()).hexdigest()
            if path.stem != key:
                raise ValueError(f"mismatched request identity: {path}")
            samples = dict(zip(
                trace["token_counts"].tolist(),
                trace["arrival_ns"].tolist(),
                strict=True,
            ))
            finish_ns = int(trace["finish_ns"].item())
        rows = delivery.get(key, {})
        receipts = []
        for tokens, sent_ns in samples.items():
            row = rows.get(tokens)
            if row is None:
                missing_retained += 1
                continue
            age_ns = round(float(row["transport_age_ms"]) * 1e6)
            if "received_monotonic_ns" in row:
                received_ns = int(row["received_monotonic_ns"])
                explicit_receipt += 1
                if abs(received_ns - sent_ns - age_ns) > 2_000:
                    raise ValueError(f"inconsistent receiver timestamp: {path}")
            else:
                received_ns = sent_ns + age_ns
                estimated_receipt += 1
            receipts.append(received_ns)
            matched += 1
            if received_ns >= finish_ns:
                late_receipts += 1
        if receipts:
            matched_requests += 1
        label = terminal.get(rid)
        if not label or not receipts:
            continue
        child_id, returned_ms = label
        matched_terminal.add(rid)
        first_ms = (returned_ms * 1e6 - min(receipts)) / 1e6
        last_ms = (returned_ms * 1e6 - max(receipts)) / 1e6
        terminal_first_leads.append(first_ms)
        terminal_last_leads.append(last_ms)
        if child_id in join_last:
            join_first_leads.append(first_ms)
            join_last_leads.append(last_ms)
    all_received = sum(len(rows) for rows in delivery.values())
    return {
        "scope": "single-pilot read-only delivery, not calibrated ETA or H2D",
        "received_samples": all_received,
        "duplicate_samples": duplicates,
        "received_request_count": len(delivery),
        "transport_age_ms": _quantiles(ages),
        "matched_retained_samples": matched,
        "retained_samples_missing_delivery": missing_retained,
        "matched_request_count": matched_requests,
        "workflow_child_request_count": len(child_requests),
        "receipts_with_explicit_timestamp": explicit_receipt,
        "receipts_derived_from_npz_sampling_clock": estimated_receipt,
        "received_after_request_finish": late_receipts,
        "unmatched_received_samples": all_received - matched,
        "natural_terminal_request_count": len(terminal),
        "terminal_requests_with_matched_delivery": len(matched_terminal),
        "child_return_first_delivery_lead_ms": _quantiles(terminal_first_leads),
        "child_return_last_delivery_lead_ms": _quantiles(terminal_last_leads),
        "join_last_child_first_delivery_lead_ms": _quantiles(join_first_leads),
        "join_last_child_last_delivery_lead_ms": _quantiles(join_last_leads),
        "limitations": (
            "NPZ retains only first 16 and last 64 samples per completed "
            "request; unmatched live samples are not proven drops. Child RETURN "
            "is paired only with strict natural terminal labels; JOIN is the "
            "last member of a complete satisfied group. Old receiver rows use "
            "the matched NPZ sampling clock plus recorded transport age."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery", type=Path, required=True)
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.delivery, args.traces, args.workflows)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
