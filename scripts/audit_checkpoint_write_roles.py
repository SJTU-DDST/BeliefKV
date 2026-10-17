#!/usr/bin/env python3
"""Associate write receipts with checkpoint observations available before submit."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_checkpoint_write_read_balance import copy_balance
from scripts.audit_native_system_costs import finite_number
from scripts.audit_sealed_wait_prefetch import SealedRecords


def write_roles(events, transfers, begin, end, mamba_unit_bytes):
    observations, seen_requests, ever_safe = [], set(), set()
    counts = Counter()
    for row in events:
        if row.get("kind") not in ("llm_submit", "llm_result"):
            continue
        timestamp = row.get("ts_ms")
        attributes = row.get("attributes") or {}
        context = (row.get("workflow_id"), row.get("context_id"))
        if not finite_number(timestamp) or not all(type(value) is str and value for value in context):
            counts["unassociable_request_events"] += 1
            continue
        request = attributes.get("request_id")
        if not request or (row["kind"], request) in seen_requests:
            raise ValueError("missing or duplicate request event identity")
        seen_requests.add((row["kind"], request))
        safe = {}
        snapshot = attributes.get("reentry_checkpoint") or {}
        if row["kind"] == "llm_result":
            counts[f"result_snapshot_{snapshot.get('status', 'missing')}"] += 1
            for state in snapshot.get("mamba_states", ()):
                if state.get("within_safe_input") is not True:
                    continue
                node, prefix = state.get("node_id"), state.get("prefix_tokens")
                if type(node) is not int or node < 0 or type(prefix) is not int or prefix <= 0:
                    raise ValueError("invalid safe checkpoint observation")
                if node in safe:
                    raise ValueError("duplicate checkpoint in one observation")
                safe[node] = prefix
                ever_safe.add(node)
        known = snapshot.get("status") == "observed" and bool(safe)
        observations.append((timestamp, context, safe, known))
    observations.sort(key=lambda row: row[0])
    current, uncertain = defaultdict(set), defaultdict(set)
    by_context, seen_safe, prefixes, conflicts = {}, set(), {}, set()
    cursor, roles, samples = 0, Counter(), defaultdict(list)
    completed_writes = [
        row for row in transfers
        if row.get("status") == "completed" and row.get("direction") == "d2h"
    ]
    for transfer in sorted(completed_writes, key=lambda row: row["submit_ts_ms"]):
        timestamp = transfer["submit_ts_ms"]
        while cursor < len(observations) and observations[cursor][0] < timestamp:
            _, context, safe, known = observations[cursor]
            previous = by_context.get(context, {})
            for node in previous:
                current[node].discard(context)
                uncertain[node].discard(context)
            if known:
                by_context[context] = safe
                for node, prefix in safe.items():
                    if node in prefixes and prefixes[node] != prefix:
                        conflicts.add(node)
                    prefixes[node] = prefix
                    current[node].add(context)
                    seen_safe.add(node)
            else:
                by_context[context] = previous
                for node in previous:
                    uncertain[node].add(context)
            cursor += 1
        if not begin <= timestamp < end:
            continue
        units = transfer["num_tokens_by_pool"].get("mamba", 0)
        if not units:
            continue
        counts["cohort_mamba_copy_units"] += units
        nodes = transfer.get("node_ids") or ()
        if len(nodes) != 1 or units != 1 or type(nodes[0]) is not int:
            counts["unassignable_mamba_copy_units"] += units
            continue
        node = nodes[0]
        counts["assignable_single_node_mamba_copies"] += 1
        counts["single_node_ever_observed_safe_including_future"] += node in ever_safe
        if node in conflicts:
            role = "observed_prefix_identity_conflict"
        elif current[node]:
            role = "last_observed_current_safe_anchor"
        elif uncertain[node]:
            role = "prior_safe_anchor_current_state_unknown"
        elif node in seen_safe:
            role = "prior_safe_anchor_superseded_in_observed_contexts"
        else:
            role = "not_previously_observed_as_safe_anchor"
        roles[role] += 1
        if len(samples[role]) < 16:
            samples[role].append({
                "command_id": transfer["command_id"], "submit_ts_ms": timestamp,
                "node_id": node, "observed_prefix_tokens": prefixes.get(node),
            })
    return {
        "counts": dict(counts),
        "roles": {
            role: {"copy_units": amount, "mamba_bytes": amount * mamba_unit_bytes}
            for role, amount in sorted(roles.items())
        },
        "samples_by_role": dict(samples),
        "observed_safe_node_count_including_future": len(ever_safe),
        "prefix_identity_conflicts_before_observed_transfers": len(conflicts),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--balance-report", type=Path, required=True)
    parser.add_argument("--arm", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or len(args.arm) != len(set(args.arm)):
        parser.error("fresh output and distinct arms are required")
    raw = args.balance_report.read_bytes()
    balance, arms = json.loads(raw), {}
    for name in args.arm:
        source = balance["arms"][name]
        sealed = SealedRecords()
        sealed.prefixes.update(source["input_jsonl_prefixes"])
        run = Path(source["run"])
        transfers = list(sealed(run / "server/transfer_telemetry.jsonl"))
        begin, end = source["cohort_submit_window_ms"]
        checked = copy_balance(transfers, begin, end, source["pool_bytes_per_unit"])
        if checked != {key: source[key] for key in checked}:
            raise ValueError("the physical receipt balance changed")
        result = write_roles(
            sealed(run / "server/runtime_events.sglang.jsonl"),
            transfers, begin, end, source["pool_bytes_per_unit"]["mamba"],
        )
        if result["counts"].get("cohort_mamba_copy_units", 0) != (
            source["totals"]["cohort_d2h"]["copy_units"].get("mamba", 0)
        ):
            raise ValueError("role coverage disagrees with completed physical writes")
        arms[name] = {**result, "run": str(run), "input_jsonl_prefixes": sealed.prefixes}
    report = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "balance_source": {
            "path": str(args.balance_report), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "arms": arms,
        "auditor_sources": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__), ROOT / "scripts/audit_checkpoint_write_read_balance.py",
                ROOT / "scripts/audit_native_system_costs.py",
                ROOT / "scripts/audit_sealed_wait_prefetch.py",
            )
        },
        "semantics": [
            "Roles use only checkpoint/request events strictly before each physical D2H submission. A new request or unknown snapshot makes the prior anchor's current status unknown.",
            "The separate ever-observed coverage count includes future snapshots; it is not an online input, policy label or evidence of physical use.",
            "Only single-node, one-Mamba-copy receipts are assigned. Aggregate batches without per-node pool identity remain unassignable.",
            "These are observations of logical node IDs and prefixes, not physical copy generations, per-node consumption, session-reference counts or safe discard permissions.",
            "Not previously observed does not mean unused, output-tail, invalid or safe to drop. Superseded in observed contexts does not exclude other shared or future users.",
            "Unknown/empty snapshots retain uncertainty. Prefix conflicts are reported rather than treated as stable identity. Receipt and request files are sealed independently.",
        ],
    }
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "arms": {name: {key: arm[key] for key in ("counts", "roles")} for name, arm in arms.items()},
    }, indent=2))


if __name__ == "__main__":
    main()
