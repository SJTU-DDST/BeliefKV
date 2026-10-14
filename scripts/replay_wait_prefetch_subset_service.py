#!/usr/bin/env python3
"""Replay smaller-payload timing using causal, sealed V56 receipt histories."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.native_h2d_seed import load_h2d_seed
from beliefkv.runtime.native_transfer_service import load_native_service_seed
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_runtime import (
    NativeAdmissionRuntime, _WaitPrefetchServiceShape,
)
from scripts.audit_prefetch_lifecycle import wait_event_identity
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.evaluate_transfer_service_history import read_prefix, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sealed", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    details = args.output.with_suffix(".rows.json.gz")
    if args.output.exists() or details.exists():
        raise ValueError("preserve prior replay evidence")
    sealed_bytes = args.sealed.read_bytes()
    sealed = json.loads(gzip.decompress(sealed_bytes))
    arm = Path(sealed["arm"])
    plan_path = arm.parent / "ab_plan.json"
    plan_bytes = plan_path.read_bytes()
    plan = json.loads(plan_bytes)
    opportunity = arm / "opportunities/admission_opportunities.jsonl"
    transfers_path = arm / "server/transfer_telemetry.jsonl"
    prefixes = sealed["input_jsonl_prefixes"]
    selected, queries = {}, []
    for row in read_prefix(opportunity, prefixes[str(opportunity)]):
        if row.get("event") != "wait_prefetch_plan":
            continue
        identity = wait_event_identity(row)
        if row.get("reason") == "start_window" and identity is not None:
            selected[identity] = row
        if row.get("reason") not in ("service_unsupported", "service_size_unsupported"):
            continue
        if sum((row.get("planned_pool_bytes") or {}).values()) > 0:
            queries.append({"query": row, "selection": selected.get(identity)})
    receipts = []
    for row in read_prefix(transfers_path, prefixes[str(transfers_path)]):
        size, delay = row.get("actual_bytes"), row.get("submit_to_ack_ms")
        complete = row.get("complete_ts_ms")
        if (
            row.get("status") == "completed"
            and row.get("direction") in ("h2d", "d2h")
            and type(size) is int and size > 0
            and type(delay) in (int, float) and math.isfinite(delay)
            and 0 < delay <= 60_000
            and type(complete) in (int, float) and math.isfinite(complete)
        ):
            receipts.append(row)
    receipts.sort(key=lambda row: row["complete_ts_ms"])
    queries.sort(key=lambda row: row["query"]["ts_ms"])
    baseline, base_sources = baseline_runtime(plan["code_commit"])
    runtimes = {"baseline": baseline(), "candidate": NativeAdmissionRuntime()}
    units = plan["native_capacity_census"]["host"]["pool_bytes_per_unit"] if (
        "native_capacity_census" in plan
    ) else {"kv": 20480, "mamba": 64389120}
    if units != {"kv": 20480, "mamba": 64389120}:
        raise ValueError("unexpected physical pool geometry")
    entries = {name: NS(host_pool=NS(size_per_token=size)) for name, size in units.items()}
    rich = load_native_service_seed(plan["transfer_service_seed"], plan["transfer_service_seed_sha256"])
    legacy = load_h2d_seed(plan["h2d_seed_artifact"], plan["h2d_seed_sha256"])
    for runtime in runtimes.values():
        if runtime._native_service_samples or runtime._h2d_samples:
            raise ValueError("unexpected environment-provided service seed")
        runtime._native_service_samples.extend(rich)
        runtime._h2d_samples.extend(legacy)
        runtime.attach_native_cache(NS(cache_controller=NS(mem_pool_host=NS(entry_map=entries))))
    index, rows, counts = 0, [], Counter()
    recovered = set()
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic") as clock:
            for item in queries:
                query, selection = item["query"], item["selection"]
                timestamp = query["ts_ms"]
                while index < len(receipts) and receipts[index]["complete_ts_ms"] < timestamp:
                    receipt = receipts[index]
                    clock.return_value = receipt["complete_ts_ms"] / 1000.
                    commit = NS(
                        status=receipt["status"], direction=receipt["direction"],
                        node_ids=(), child_commits=(),
                        actual_bytes=receipt["actual_bytes"],
                        submit_to_ack_ms=receipt["submit_to_ack_ms"],
                        enqueue_to_submit_ms=receipt.get("enqueue_to_submit_ms"),
                        num_tokens_by_pool=tuple((receipt.get("num_tokens_by_pool") or {}).items()),
                    )
                    for runtime in runtimes.values():
                        runtime.on_native_transfer_commit(commit)
                    index += 1
                clock.return_value = timestamp / 1000.
                amounts = query["planned_pool_bytes"]
                if any(amounts.get(name, 0) % unit for name, unit in units.items()):
                    raise ValueError("recorded payload is not integral native pool units")
                key = PrefillCandidateKey(
                    query["request_id"], query["workflow_id"], query["invocation_id"],
                    query["context_id"], query["context_epoch"], 0,
                    query["session_id"], query["session_generation"],
                )
                # The log omits the leaf generation. This proxy exercises only
                # timing; native checkpoint eligibility is deliberately unknown.
                step = NS(key=key, leaf_node_id=0, leaf_creation_time=0)
                payload = NS(
                    step=step, required_full_tokens=amounts.get("kv", 0) // units["kv"],
                    required_mamba_slots=amounts.get("mamba", 0) // units["mamba"],
                )
                shape = None
                same_scope = selection is not None and all(
                    selection.get(name) == query.get(name)
                    for name in ("session_id", "session_generation", "request_id", "reusable_input_tokens")
                )
                if same_scope and 0 <= timestamp - selection["ts_ms"] <= 2000.:
                    shape = _WaitPrefetchServiceShape(
                        key, 0, 0,
                        selection["planned_full_tokens"], selection["planned_mamba_slots"],
                    )
                base = runtimes["baseline"]._h2d_start_window(payload)
                direct = runtimes["candidate"]._h2d_start_window(payload)
                if base != direct:
                    raise AssertionError("previous direct timing estimate changed")
                candidate = runtimes["candidate"]._h2d_start_window(payload, service_shape=shape)
                counts["queries"] += 1
                counts["recorded_selection_same_visible_scope_within_2s"] += shape is not None
                counts["direct_runtime_estimates_identical"] += 1
                counts["new_cost_supported_queries"] += base is None and candidate is not None
                if base is None and candidate is not None:
                    recovered.add(wait_event_identity(query))
                rows.append({
                    "source": query["source"], "workflow_id": query["workflow_id"],
                    "context_id": query["context_id"], "context_epoch": query["context_epoch"],
                    "join_id": query.get("join_id"), "timestamp_ms": timestamp,
                    "selected_payload_bytes": (
                        sum(selection["planned_pool_bytes"].values()) if shape is not None else None
                    ),
                    "selection_to_rejection_ms": (
                        timestamp - selection["ts_ms"] if selection is not None else None
                    ),
                    "planned_pool_bytes": amounts,
                    "fits_current_free_lists": query.get("fits_current_free_lists"),
                    "baseline_supported": base is not None,
                    "subset_cost_supported": candidate is not None,
                    "subset_service_ms": candidate.service_ms if candidate is not None else None,
                    "subset_enqueue_ms": candidate.enqueue_ms if candidate is not None else None,
                    "checkpoint_leaf_eligibility": "unknown_not_reconstructed",
                })
        if any(runtime.completed_physical_actions for runtime in runtimes.values()):
            raise AssertionError("timing replay produced a physical action")
        packed = gzip.compress(json.dumps(rows, separators=(",", ":"), allow_nan=False).encode(), mtime=0)
        details.write_bytes(packed)
        report = {
            "status": "causal_subset_cost_replay_not_native_identity_or_action_replay",
            "observed_at_local": datetime.now().astimezone().isoformat(),
            "collection_revision": plan["code_commit"], "baseline_sources": base_sources,
            "sealed_report": str(args.sealed.resolve()),
            "sealed_report_sha256": hashlib.sha256(sealed_bytes).hexdigest(),
            "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
            "source_prefixes": {str(path): prefixes[str(path)] for path in (opportunity, transfers_path)},
            "source_sha256": {
                name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in (
                    "beliefkv/runtime/sglang_v0520_runtime.py",
                    "beliefkv/runtime/native_transfer_policy.py",
                    "beliefkv/runtime/native_transfer_service.py",
                    "scripts/replay_wait_prefetch_subset_service.py",
                )
            },
            "counts": dict(counts), "receipts_fed_per_runtime": index,
            "new_cost_supported_independent_waits": len(recovered),
            "new_cost_supported_waits_by_source": dict(Counter(identity[0] for identity in recovered)),
            "new_cost_supported_queries_service_ms": summary(
                row["subset_service_ms"] for row in rows
                if not row["baseline_supported"] and row["subset_cost_supported"]
            ),
            "details": str(details.resolve()), "details_sha256": hashlib.sha256(packed).hexdigest(),
            "limitations": [
                "Only receipts completed strictly before each query feed the actual runtime receipt handler.",
                "Clock order approximates scheduler callback/cache order; future and in-flight outcomes are excluded.",
                "All original direct estimates stay identical; the estimator and bounded histories are unchanged.",
                "A <=2s same-visible-scope selection exercises cost fallback; missing native leaf generation stays unknown.",
                "Actual runtime still checks key, checkpoint generation, reduced per-pool payload, latest forecast and native capacity.",
                "A larger selected payload's measured cost is a planning allowance, not a guaranteed queue/DMA upper bound.",
                "Rejected smaller payloads have no executed-transfer labels; support is not timing accuracy or newly executed prefetch.",
                "No physical action, GPU experiment, online policy acceptance or JCT/throughput benefit is inferred.",
            ],
        }
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        print(json.dumps({
            "counts": dict(counts), "new_waits_by_source": report["new_cost_supported_waits_by_source"],
            "new_service_ms": report["new_cost_supported_queries_service_ms"],
        }), flush=True)
    finally:
        for runtime in runtimes.values():
            runtime.close()


if __name__ == "__main__":
    main()
