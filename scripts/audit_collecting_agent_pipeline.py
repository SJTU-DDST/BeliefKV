#!/usr/bin/env python3
"""Observe sealed prefixes of a live agent run without claiming final performance."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_agent_budget_composition import event_changes, inspect
from scripts.audit_decode_batch_efficiency import accumulate, summarize
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.run_causal_comparison import comparison, load, receipt_work, reservation


def blocked_composition(states, *, begin, end, maximum_age_ms=1500.):
    if (
        not all(type(value) in (int, float) and math.isfinite(value)
                for value in (begin, end, maximum_age_ms))
        or not 0 <= begin <= end or maximum_age_ms <= 0
    ):
        raise ValueError("invalid diagnostic observation interval")
    times = [row["ts_ms"] for row in states]
    if any(not math.isfinite(when) for when in times) or any(
        left >= right for left, right in zip(times, times[1:])
    ):
        raise ValueError("diagnostic snapshot clock is not increasing")
    tokens, counts = Counter(), Counter()
    for index, state in enumerate(states):
        right = min(end, state["ts_ms"] + maximum_age_ms,
                    times[index + 1] if index + 1 < len(times) else end)
        duration = max(0., right - max(begin, state["ts_ms"]))
        if not duration:
            continue
        admission = state.get("agent_admission") or {}
        budgets = admission.get("active_agent_budgets")
        if type(budgets) is not list:
            counts["unknown_ms"] += duration
            continue
        active = admission.get("active_invocation_ids")
        if (
            type(active) is not list or len(set(active)) != len(active)
            or [row["invocation_id"] for row in budgets] != active
            or any(
                type(row["tokens"]) is not int or row["tokens"] <= 0
                or type(row["buffer_tokens"]) is not int or row["buffer_tokens"] < 0
                or type(row["pending"]) is not bool
                or row["blocked"] is not None and type(row["blocked"]) is not str
                for row in budgets
            )
            or type(admission.get("pending_requests")) is not int
        ):
            raise ValueError("invalid same-snapshot logical budget")
        total = sum(row["tokens"] + row["buffer_tokens"] for row in budgets)
        if total != admission.get("logical_active_tokens"):
            raise ValueError("same-snapshot logical budget did not reconcile")
        inactive = admission["pending_requests"] - sum(row["pending"] for row in budgets)
        if inactive < 0:
            raise ValueError("active pending agents exceed registered requests")
        counts["observed_ms"] += duration
        counts["logical_budget_token_ms"] += total * duration
        counts["ms_with_inactive_registered_requests"] += duration * bool(inactive)
        for row in budgets:
            if row["pending"] or not row["blocked"]:
                continue
            amount = (row["tokens"] + row["buffer_tokens"]) * duration
            tokens[row["blocked"]] += amount
            if inactive:
                counts["blocked_budget_token_ms_with_inactive_requests"] += amount
    denominator = counts["logical_budget_token_ms"]
    return {
        "observation_ms": dict(counts),
        "active_budget_fraction_by_block_reason": {
            reason: amount / denominator if denominator else None
            for reason, amount in sorted(tokens.items())
        },
        "active_budget_fraction_blocked_with_inactive_requests": (
            counts["blocked_budget_token_ms_with_inactive_requests"] / denominator
            if denominator else None
        ),
        "semantics": (
            "Logical active budget token-time only. Inactive registered requests "
            "are submitted model demand, not proof of a physical capacity deficit. "
            "Blocked budgets are not physical HBM occupancy, reclaimable bytes, "
            "an exclusive delay cause or removable JCT."
        ),
    }


def validate_receipts(rows, expected):
    fields = ("prompt_tokens", "output_tokens", "prompt_sha256",
              "output_sha256", "native_finish_reason")
    seen = set()
    for row in rows:
        identity = row["request_id"]
        if (
            identity in seen or identity not in expected
            or row.get("exact_tokens_verified") is not True
            or row.get("native_receipt", {}).get("sampling_path") != "position"
            or tuple(row.get(name) for name in fields) != expected[identity]
        ):
            raise ValueError("live replay receipt changed source work or stopping")
        seen.add(identity)
    return len(seen)


def audit(registration, label, expected_path):
    plan = load(registration)
    comparison.verify_sources(plan)
    state = load(registration.with_suffix(".state.json"))
    if (
        label not in plan["order"] or state.get("current_arm") != label
        or state.get("status") != "running_" + label
        or not all(reservation.alive(state.get(name)) for name in ("watcher", "server", "driver"))
    ):
        raise ValueError("collecting observation requires actual live owned processes")
    arm = Path(plan["run_root"]) / label
    reader = SealedRecords()
    expected = receipt_work(expected_path, sampling_mode="position")
    if len(expected) != plan["source_plan"]["requests"]:
        raise ValueError("expected receipt population differs from the registered work")
    exact = validate_receipts(
        reader(arm / "replay/native_requests.jsonl"), expected,
    )
    snapshots = [
        row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == "admission_runtime_state"
    ]
    if not snapshots or any(row.get("physical_disabled") is not False for row in snapshots):
        raise ValueError("missing healthy runtime observations")
    events = list(reader(arm / "server/runtime_events.sglang.jsonl"))
    changes = event_changes(events)
    if not changes:
        raise ValueError("no observed native requests")
    budgets = (
        inspect(snapshots, events) if snapshots[-1].get("agent_admission") is not None else None
    )
    reasons = (
        blocked_composition(snapshots, begin=changes[0][0], end=changes[-1][0])
        if budgets is not None else None
    )
    info = load(arm / "server/server_info.json")
    if info.get("tp_size") != 1 or info.get("dp_size") != 1 or info.get("speculative_algorithm") is not None:
        raise ValueError("unsupported native decode topology")
    decode, phases = Counter(), Counter()
    seen = set()
    last_start = -math.inf
    domains = set()
    for row in reader(arm / "server/runtime_audit.jsonl"):
        if row.get("event") != "gpu_service_sample":
            continue
        start, elapsed = row["service_start_ts_ms"], row["service_elapsed_ms"]
        if (
            row["forward_iter"] in seen or row["phase"] not in ("prefill", "decode")
            or not all(type(value) in (int, float) and math.isfinite(value)
                       for value in (start, elapsed))
            or start < last_start or elapsed < 0
        ):
            raise ValueError("duplicate or unsupported forward observation")
        seen.add(row["forward_iter"])
        last_start = start
        domains.add(row["monotonic_clock_domain"])
        phases[row["phase"]] += row["service_elapsed_ms"]
        if row["phase"] == "decode":
            accumulate(
                decode, row, info["cuda_graph_config"]["decode"]["bs"],
                disable_padding=info["disable_cuda_graph_padding"],
            )
    status_path = arm / "server/native_telemetry_status.json"
    status_bytes = status_path.read_bytes()
    status = json.loads(status_bytes)
    if len(domains) != 1 or status.get("writer_error") is not None or any(
        status.get(name) != 0 for name in ("failed_records", "dropped_records")
    ):
        raise ValueError("native telemetry reports an error")
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "sealed_collecting_prefix_not_complete_performance",
        "registration": str(registration.resolve()), "registration_sha256": sha256_file(registration),
        "collection_code_commit": plan["code_commit"], "arm": str(arm),
        "actual_live_processes": {name: reservation.process(state[name]["pid"])
                                 for name in ("watcher", "server", "driver")},
        "expected_requests": len(expected), "exact_completed_receipts": exact,
        "expected_receipts": {"path": str(expected_path.resolve()), "sha256": sha256_file(expected_path)},
        "sampled_logical_budgets": budgets, "sampled_block_reasons": reasons,
        "decode_prefix": summarize(decode),
        "worker_interval_sum_ms_by_phase": dict(phases),
        "latest_runtime_snapshot": snapshots[-1],
        "native_status_observation": status,
        "native_status_sha256": hashlib.sha256(status_bytes).hexdigest(),
        "input_jsonl_prefixes": reader.prefixes,
        "analysis_source_sha256": {name: sha256_file(ROOT / name) for name in (
            "scripts/audit_collecting_agent_pipeline.py",
            "scripts/audit_agent_budget_composition.py",
            "scripts/audit_decode_batch_efficiency.py",
            "scripts/audit_sealed_wait_prefetch.py",
            "scripts/run_causal_comparison.py",
        )},
        "limitations": [
            "Independent first-read complete JSONL prefixes are not an atomic cross-file snapshot.",
            "Logical budget, decode and receipt prefixes can cover different end times.",
            "Decode worker intervals include runtime and waiting; they are not pure GPU kernel durations.",
            "No completed-only subset JCT or whole-run throughput is calculated.",
            "This observation does not establish final correctness, performance or prediction quality.",
        ],
        "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--arm", required=True)
    parser.add_argument("--expected-receipts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous collecting evidence")
    result = audit(args.registration, args.arm, args.expected_receipts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        key: result[key] for key in (
            "scope", "exact_completed_receipts", "expected_requests",
            "sampled_logical_budgets", "sampled_block_reasons", "decode_prefix",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
