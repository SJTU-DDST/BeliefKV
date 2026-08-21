#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify the deterministic Host drop/recompute GPU gate."
    )
    parser.add_argument("--runtime-audit", type=Path, required=True)
    parser.add_argument("--runtime-summary", type=Path, required=True)
    parser.add_argument("--gate-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _records(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def main() -> int:
    args = _args()
    rows = _records(args.runtime_audit)
    summary = json.loads(args.runtime_summary.read_text(encoding="utf-8"))
    state = (
        summary.get("joint_control", {}).get("host_recompute_micro_gate", {})
    )
    gate_rows = [
        item
        for item in rows
        if item.get("event") == "host_recompute_micro_gate_state"
        and item.get("gate_id") == args.gate_id
    ]
    offload_command_id = state.get("offload_command_id")
    host_drop_command_id = state.get("host_drop_command_id")
    offload_acks = [
        item
        for item in rows
        if item.get("event") == "transfer_acknowledged"
        and item.get("command_id") == offload_command_id
        and item.get("status") == "completed"
        and int(item.get("actual_bytes", 0) or 0) > 0
    ]
    host_drop_rows = [
        item
        for item in rows
        if item.get("event") == "host_cleanup_terminal"
        and item.get("command_id") == host_drop_command_id
        and item.get("status") == "completed"
        and item.get("mode") == "cpu_only_recompute"
        and bool(item.get("recompute_required"))
        and int(item.get("actual_bytes", 0) or 0) > 0
    ]
    service_rows = [
        item
        for item in rows
        if item.get("event") == "context_recompute_service_started"
        and item.get("context_id") == state.get("context_id")
        and int(item.get("uncached_prompt_tokens", 0) or 0) > 0
    ]
    transactions = summary.get("transactions", {})
    correctness = summary.get("correctness_gates", {})
    checks = {
        "gate_reached_completed_state": state.get("stage") == "completed",
        "state_machine_audited": {
            "offload_queued",
            "cpu_only_ready",
            "host_dropped_recompute_required",
            "completed",
        }.issubset({str(item.get("stage")) for item in gate_rows}),
        "nonzero_d2h_completed": bool(offload_acks),
        "generation_safe_host_drop_completed": bool(host_drop_rows),
        "native_recompute_service_observed": bool(service_rows),
        "no_pending_transactions": bool(
            correctness.get("no_pending_transactions")
        ),
        "shutdown_acknowledged": summary.get("shutdown_state") == "acknowledged",
        "no_active_restore_obligation": not transactions.get(
            "active_restore_obligation_ids"
        ),
        "no_inflight_command": not transactions.get("inflight_command_ids"),
    }
    result = {
        "schema_version": 1,
        "gate_id": args.gate_id,
        "passed": all(checks.values()),
        "checks": checks,
        "evidence": {
            "context_id": state.get("context_id"),
            "offload_command_id": offload_command_id,
            "explicit_d2h_bytes": state.get("explicit_d2h_bytes", 0),
            "host_drop_command_id": host_drop_command_id,
            "host_drop_bytes": state.get("host_drop_bytes", 0),
            "recompute_request_id": state.get("recompute_request_id"),
            "recompute_uncached_prompt_tokens": state.get(
                "recompute_uncached_prompt_tokens", 0
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
