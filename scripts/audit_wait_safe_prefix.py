#!/usr/bin/env python3
"""Compare waiting-time safe input caps with later native Host-hit intervals."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_prefetch_lifecycle import distribution, wait_event_identity
from scripts.audit_sealed_wait_prefetch import SealedRecords


def nonnegative_int(value):
    return type(value) is int and value >= 0


def host_interval_at_safe_cap(native, cap):
    """Partition a later observed Host interval; this does not identify DMA."""
    attrs = native.get("attributes") or {}
    device, host = (attrs.get(key) for key in (
        "cached_tokens_device", "cached_tokens_host",
    ))
    if not all(nonnegative_int(value) for value in (device, host, cap)):
        return None
    inside = min(host, max(0, cap - device))
    return {
        "device_prefix_tokens_at_service": device,
        "host_hit_tokens_at_service": host,
        "host_hit_tokens_within_safe_input_cap": inside,
        "host_hit_tokens_beyond_safe_input_cap": host - inside,
    }


def audit_safe_prefix(waits, plans, native_requests):
    by_identity = {}
    for wait in waits:
        identity = wait_event_identity(wait)
        if identity is None or identity in by_identity:
            raise ValueError("missing or duplicate wait identity")
        by_identity[identity] = wait
    latest = {}
    for plan in plans:
        if plan.get("event") != "wait_prefetch_plan":
            continue
        identity = wait_event_identity(plan)
        wait = by_identity.get(identity)
        completion = wait.get("completion_ts_ms") if wait else None
        when = plan.get("ts_ms")
        if (
            completion is None or type(when) not in (int, float)
            or not when < completion
        ):
            continue
        if identity not in latest or when > latest[identity]["ts_ms"]:
            latest[identity] = plan
    native_by_id = {}
    for native in native_requests:
        if native.get("kind") != "llm_submit":
            continue
        rid = (native.get("attributes") or {}).get("request_id")
        if rid is None:
            continue
        if rid in native_by_id:
            raise ValueError("duplicate native request submission")
        native_by_id[rid] = native
    rows = []
    for identity, wait in by_identity.items():
        plan = latest.get(identity)
        native = native_by_id.get(wait.get("next_request_id"))
        if native is not None and any(
            native.get(key) != wait.get(key)
            for key in ("workflow_id", "context_id", "invocation_id")
        ):
            raise ValueError("next request identity mismatch")
        cap = plan.get("reusable_input_tokens") if plan else None
        interval = host_interval_at_safe_cap(native, cap) if native else None
        rows.append({
            **{key: wait.get(key) for key in (
                "source", "workflow_id", "context_id", "context_epoch", "join_id",
                "invocation_id", "next_request_id", "first_service_ts_ms",
                "native_arrival_ts_ms", "completion_ts_ms",
                "early_started_and_reused_full_bytes",
            )},
            "last_pre_completion_plan_ts_ms": plan["ts_ms"] if plan else None,
            "last_pre_completion_plan_reason": plan.get("reason") if plan else None,
            "last_pre_completion_no_step_reason": plan.get("no_step_reason") if plan else None,
            "safe_input_cap_tokens": cap,
            "latest_plan_expanded_closure": (
                nonnegative_int(plan.get("unbacked_full_nodes"))
                if plan else False
            ),
            "observed_host_only_full_tokens_in_closure": (
                plan.get("host_backed_full_missing_device_tokens") if plan else None
            ),
            "observed_host_only_mamba_nodes_in_closure": (
                plan.get("host_only_mamba_nodes_in_observed_closure") if plan else None
            ),
            "observed_unbacked_full_nodes_in_closure": (
                plan.get("unbacked_full_nodes") if plan else None
            ),
            "plan_to_first_service_ms": (
                wait["first_service_ts_ms"] - plan["ts_ms"]
                if plan and wait.get("first_service_ts_ms") is not None else None
            ),
            "plan_to_completion_ms": (
                wait["completion_ts_ms"] - plan["ts_ms"] if plan else None
            ),
            **{key: wait.get(key) for key in (
                "completion_to_client_submit_ms", "client_submit_to_native_arrival_ms",
                "submit_to_first_service_ms",
            )},
            "native_checkpoint_tokens_at_service": (
                (native.get("attributes") or {}).get("reentry_checkpoint_seqlen")
                if native else None
            ),
            "host_interval": interval,
        })
    sources = {}
    for source in ("join_ticket", "tool_wait"):
        selected = [row for row in rows if row["source"] == source]
        groups = defaultdict(list)
        for row in selected:
            reason = (
                row["last_pre_completion_no_step_reason"]
                or row["last_pre_completion_plan_reason"] or "no_pre_completion_plan"
            )
            groups[reason].append(row)
        sources[source] = {
            "observed_waits": len(selected),
            "by_last_pre_completion_reason": {
                reason: summarize_group(group)
                for reason, group in sorted(groups.items())
            },
        }
    return {"by_source": sources, "rows": rows}


def summarize_group(rows):
    known = [row for row in rows if row["host_interval"] is not None]
    positive = [row for row in known if row["host_interval"]["host_hit_tokens_at_service"] > 0]
    expanded = [row for row in rows if row["latest_plan_expanded_closure"]]
    return {
        "waits": len(rows),
        "unknown_safe_cap_or_native_hit_waits": len(rows) - len(known),
        "positive_later_native_host_hit_waits": len(positive),
        "positive_later_host_hit_within_safe_cap_waits": sum(
            row["host_interval"]["host_hit_tokens_within_safe_input_cap"] > 0 for row in positive
        ),
        "positive_later_host_hit_entirely_beyond_safe_cap_waits": sum(
            row["host_interval"]["host_hit_tokens_within_safe_input_cap"] == 0 for row in positive
        ),
        **{
            key: sum(row["host_interval"][key] for row in known)
            for key in (
                "host_hit_tokens_at_service", "host_hit_tokens_within_safe_input_cap",
                "host_hit_tokens_beyond_safe_input_cap",
            )
        },
        "expanded_closure_waits": len(expanded),
        "expanded_closure_without_host_only_full_waits": sum(
            row["observed_host_only_full_tokens_in_closure"] == 0 for row in expanded
        ),
        "expanded_closure_with_unbacked_full_waits": sum(
            nonnegative_int(row["observed_unbacked_full_nodes_in_closure"])
            and row["observed_unbacked_full_nodes_in_closure"] > 0 for row in expanded
        ),
        "positive_later_host_hit_plan_to_service_ms": distribution(
            row["plan_to_first_service_ms"] for row in positive
        ),
        "positive_later_host_hit_wait_components_ms": {
            key: distribution(row[key] for row in positive)
            for key in (
                "plan_to_completion_ms", "completion_to_client_submit_ms",
                "client_submit_to_native_arrival_ms", "submit_to_first_service_ms",
            )
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sealed-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output already exists")
    payload = gzip.decompress(args.sealed_report.read_bytes())
    source = json.loads(payload)
    sealed = SealedRecords()
    sealed.prefixes.update(source["input_jsonl_prefixes"])
    arm = Path(source["arm"])
    plans_path = arm / "opportunities/admission_opportunities.jsonl"
    native_path = arm / "server/runtime_events.sglang.jsonl"
    for path in (plans_path, native_path):
        if str(path) not in sealed.prefixes:
            raise ValueError(f"input missing from original sealed report: {path}")
    report = audit_safe_prefix(
        source["wait_event_attribution"]["rows"],
        sealed(plans_path), sealed(native_path),
    )
    report.update({
        "scope": (
            "Descriptive partition of the next request's native Host-hit interval "
            "observed at first service, using the last plan strictly before recorded "
            "wait completion. It is neither physical DMA attribution nor proof that "
            "those pages were Host-only or eligible at the earlier plan. Safe caps "
            "do not verify next-input token equality; unknowns remain unknown. "
            "Wait identities can share physical pages. No transfer bytes or oracle "
            "JCT benefit is inferred."
        ),
        "sealed_report": str(args.sealed_report),
        "sealed_report_sha256": hashlib.sha256(args.sealed_report.read_bytes()).hexdigest(),
        "detail_uncompressed_sha256": hashlib.sha256(payload).hexdigest(),
        "input_jsonl_prefixes": {
            str(path): sealed.prefixes[str(path)] for path in (plans_path, native_path)
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"output": str(args.output), "by_source": report["by_source"]}, indent=2))


if __name__ == "__main__":
    main()
