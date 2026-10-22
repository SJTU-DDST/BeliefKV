#!/usr/bin/env python3
"""Follow JOIN enqueue identity changes through observed client handoffs."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_sealed_wait_prefetch import SealedRecords


def identity(row):
    return tuple(row.get(name) for name in ("workflow_id", "join_id", "context_id"))


def analyze(events, acknowledgements):
    bindings, issues, plans = defaultdict(list), defaultdict(list), defaultdict(list)
    rejected = []
    for position, row in enumerate(events):
        kind = row.get("event")
        key = identity(row)
        if kind == "confirmed_join_client_bound":
            bindings[key].append((position, row))
        elif kind == "prefetch_native_issued" and row.get("source") == "join_ticket":
            issues[key].append((position, row))
        elif kind == "wait_prefetch_plan" and row.get("source") == "join_ticket":
            plans[key].append((position, row))
            if row.get("reason") == "enqueue_causal_changed":
                rejected.append((position, row))
    acks = {}
    for row in acknowledgements:
        if row.get("action") != "PREFETCH_GPU":
            continue
        command = row.get("command_id")
        if command in acks:
            raise ValueError("duplicate prefetch ACK identity")
        acks[command] = row
    result = []
    for position, failure in rejected:
        key = identity(failure)
        row = {
            **dict(zip(("workflow_id", "join_id", "context_id"), key)),
            "rejected_request_id": failure.get("request_id"),
            "rejected_context_epoch": failure.get("context_epoch"),
            "rejection_ts_ms": failure["ts_ms"],
            "status": "client_handoff_unproven",
        }
        prior = [item for index, item in bindings[key] if index < position]
        binding = prior[-1] if prior else None
        epoch = failure.get("context_epoch")
        if (
            not all(type(value) is str and value for value in key)
            or type(epoch) is not int or epoch < 0
            or binding is None or binding["ts_ms"] > failure["ts_ms"]
            or binding.get("context_epoch") != epoch
            or binding.get("target_context_epoch") != epoch + 1
            or type(binding.get("request_id")) is not str or not binding["request_id"]
            or binding["request_id"] == failure.get("request_id")
            or type(failure.get("session_id")) is not str or not failure["session_id"]
            or type(failure.get("session_generation")) is not int
        ):
            result.append(row)
            continue
        target = binding["request_id"]
        row.update(
            client_binding_ts_ms=binding["ts_ms"], target_request_id=target,
            target_context_epoch=epoch + 1,
            status="client_handoff_without_observed_retry",
        )
        # Preserve file order as well as clocks. A future binding or a different
        # session/request cannot supply proof for this invalidated selection.
        retries = [
            item for index, item in issues[key]
            if index > position and item["ts_ms"] >= failure["ts_ms"]
            and item.get("request_id") == target
            and item.get("context_epoch") == epoch + 1
            and item.get("checkpoint_context_epoch") == epoch
            and item.get("session_id") == failure["session_id"]
            and item.get("session_generation") == failure["session_generation"]
        ]
        if not retries:
            result.append(row)
            continue
        first = retries[0]
        batch = [item for item in retries if item["ts_ms"] == first["ts_ms"]]
        verified, full_bytes = [], 0
        for item in batch:
            ack = acks.get(item.get("command_id"))
            if (
                ack is not None and ack.get("context_id") == key[2]
                and ack.get("context_epoch") == epoch + 1
                and type((ack.get("pool_bytes") or {}).get("kv")) is int
                and ack["pool_bytes"]["kv"] >= 0
                and ack["ts_ms"] >= item["ts_ms"]
            ):
                verified.append(item["command_id"])
                full_bytes += ack["pool_bytes"]["kv"]
        row.update(
            status="client_handoff_retry_submitted",
            first_retry_submit_ts_ms=first["ts_ms"],
            rejection_to_first_retry_ms=first["ts_ms"] - failure["ts_ms"],
            first_retry_command_ids=[item["command_id"] for item in batch],
            first_retry_acked_full_command_ids=verified,
            first_retry_acked_full_bytes=full_bytes,
            following_target_plan_reasons=dict(Counter(
                item["reason"] for index, item in plans[key]
                if index > position and item.get("request_id") == target
                and item.get("context_epoch") == epoch + 1
                and item["ts_ms"] >= failure["ts_ms"]
            )),
        )
        result.append(row)
    return {
        "enqueue_causal_changed_records": len(result),
        "status_counts": dict(Counter(row["status"] for row in result)),
        "rows": result,
    }


def audit(arm):
    reader = SealedRecords()
    result = analyze(
        reader(arm / "opportunities/admission_opportunities.jsonl"),
        reader(arm / "server/physical_action_ack.jsonl"),
    )
    return {**result, "arm": str(arm.resolve()), "input_jsonl_prefixes": reader.prefixes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous evidence; use a fresh output")
    result = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        **audit(args.arm),
        "scope": (
            "Observed JOIN enqueue rejections followed through a prior explicit client "
            "binding and same checkpoint/session-generation retry. File order and "
            "scheduler wall clocks must agree. Submission and matching FULL ACK do "
            "not prove next-request reuse, complete restoration or a JCT saving. "
            "Absent retries and unproven bindings remain explicit, not failed workflows. "
            "Subsequent capacity observations do not prove an exclusive rejection cause."
        ),
        "analysis_source_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(__file__), ROOT / "scripts/audit_sealed_wait_prefetch.py")
        },
        "gpu_performance_accepted": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "output": str(args.output), "status_counts": result["status_counts"],
        "retry_delays_ms": [
            row["rejection_to_first_retry_ms"] for row in result["rows"]
            if "rejection_to_first_retry_ms" in row
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
