#!/usr/bin/env python3
"""Compare waiting-time safe input caps with later native Host-hit intervals."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
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


def native_lifetimes(native_events, service_events):
    submitted, completed, first = {}, {}, {}
    counts, domains = Counter(), set()
    outside, open_launches, previous_sequence = {}, [], 0
    for event in native_events:
        attrs = event.get("attributes") or {}
        rid = attrs.get("request_id")
        if event.get("kind") == "llm_submit":
            if rid is None or rid in submitted:
                raise ValueError("missing or duplicate native registration")
            submitted[rid] = event
        elif event.get("kind") == "llm_result":
            if rid is None or rid in completed:
                raise ValueError("missing or duplicate native completion")
            completed[rid] = event
    for event in service_events:
        if event.get("event") != "gpu_service_sample":
            continue
        sample_id = event.get("sample_id", "")
        if not sample_id.startswith("native-service-"):
            raise ValueError("unknown service sequence identity")
        sequence = int(sample_id.removeprefix("native-service-"))
        if sequence <= previous_sequence:
            raise ValueError("duplicate or reversed service sequence")
        counts["missing_service_sequence_numbers"] += sequence - previous_sequence - 1
        previous_sequence = sequence
        launch, end, wall = (event.get(key) for key in (
            "launch_monotonic_ms", "complete_monotonic_ms", "complete_ts_ms",
        ))
        if any(type(value) not in (int, float) or not math.isfinite(value)
               for value in (launch, end, wall)) or launch > end:
            raise ValueError("invalid service clock")
        domain = event.get("monotonic_clock_domain")
        if not isinstance(domain, str) or not domain:
            raise ValueError("missing service clock domain")
        domains.add(domain)
        for sample in event.get("request_samples") or ():
            rid = sample["request_id"]
            native = submitted.get(rid)
            if native is None:
                counts["service_requests_outside_native_prefix_records"] += 1
                outside[rid] = min(outside.get(rid, launch), launch)
                continue
            if any(sample.get(key) != native.get(key) for key in (
                "workflow_id", "invocation_id", "context_id", "context_epoch",
            )):
                raise ValueError("service request identity mismatch")
            if rid not in first or launch < first[rid]["launch"]:
                first[rid] = {"launch": launch, "offset": wall - end, "domain": domain}
    if len(domains) != 1:
        raise ValueError("one native service clock domain required")
    result = {}
    for rid, native in submitted.items():
        terminal, start = completed.get(rid), first.get(rid)
        if terminal is None or start is None:
            counts["requests_without_closed_observed_lifetime"] += 1
            if start is not None:
                open_launches.append(start["launch"])
            continue
        if any(terminal.get(key) != native.get(key) for key in (
            "workflow_id", "invocation_id", "context_id", "context_epoch",
        )):
            raise ValueError("completion request identity mismatch")
        attrs = terminal["attributes"]
        finish = attrs.get("complete_monotonic_ms")
        queue = native["ts_ms"] - start["offset"]
        launch = start["launch"]
        if (
            attrs.get("monotonic_clock_domain") != start["domain"]
            or type(finish) not in (int, float) or not math.isfinite(finish)
            or not queue <= launch <= finish
        ):
            raise ValueError("inconsistent native request lifetime")
        result[rid] = {"queue": queue, "launch": launch, "finish": finish}
    counts["closed_observed_request_lifetimes"] = len(result)
    counts["native_requests"] = len(submitted)
    return result, {
        **dict(counts),
        "service_requests_outside_native_prefix_unique": len(outside),
        "first_unbound_service_launch_monotonic_ms": min(outside.values(), default=None),
        "first_unclosed_observed_launch_monotonic_ms": min(open_launches, default=None),
    }


def queue_lifetime_exposure(lifetimes, targets, *, request_limit):
    if type(request_limit) is not int or request_limit < 1:
        raise ValueError("positive descriptive request limit required")
    changes, expected, selected = defaultdict(Counter), Counter(), Counter()
    for rid, row in lifetimes.items():
        queue, launch, finish = (row[key] for key in ("queue", "launch", "finish"))
        if (
            any(type(value) not in (int, float) or not math.isfinite(value)
                for value in (queue, launch, finish))
            or not queue <= launch <= finish
        ):
            raise ValueError("invalid lifetime ordering")
        changes[launch][None] += 1
        changes[finish][None] -= 1
        for name, ids in targets.items():
            if rid in ids:
                changes[queue][name] += 1
                changes[launch][name] -= 1
                expected[name] += launch - queue
                selected[name] += 1
    active, maximum, previous = 0, 0, None
    pending, histograms = Counter(), defaultdict(Counter)
    for when, updates in sorted(changes.items()):
        if previous is not None:
            elapsed = when - previous
            for name, count in pending.items():
                if count:
                    histograms[name][active] += count * elapsed
        active += updates.get(None, 0)
        for name, count in updates.items():
            if name is not None:
                pending[name] += count
        if active < 0 or any(count < 0 for count in pending.values()):
            raise ValueError("negative native lifetime count")
        maximum, previous = max(maximum, active), when
    if active or any(pending.values()):
        raise ValueError("unclosed native lifetime sweep")
    groups = {}
    for name, ids in targets.items():
        histogram = histograms[name]
        total = sum(histogram.values())
        if not math.isclose(total, expected[name], rel_tol=1e-9, abs_tol=1e-4):
            raise ValueError("queue-wait exposure does not conserve time")
        groups[name] = {
            "target_requests": len(ids), "observed_closed_targets": selected[name],
            "unknown_lifetime_targets": len(ids - lifetimes.keys()),
            "queue_to_first_launch_request_ms": total,
            "queue_to_first_launch_ms": distribution(
                lifetimes[rid]["launch"] - lifetimes[rid]["queue"]
                for rid in ids & lifetimes.keys()
            ),
            "last_target_launch_monotonic_ms": max(
                (lifetimes[rid]["launch"] for rid in ids & lifetimes.keys()), default=None,
            ),
            "request_ms_by_launched_unfinished": dict(sorted(histogram.items())),
            "wait_fraction_at_or_above_limit_minus_one": (
                sum(value for size, value in histogram.items() if size >= request_limit - 1)
                / total if total else None
            ),
            "wait_fraction_at_or_above_limit": (
                sum(value for size, value in histogram.items() if size >= request_limit)
                / total if total else None
            ),
        }
    return {
        "descriptive_request_limit": request_limit,
        "maximum_observed_launched_unfinished": maximum, "groups": groups,
        "scope": (
            "Waiter-time weighted overlap with closed observed request lifetimes "
            "from first scheduler launch to native completion. Missing or open "
            "lifetimes are excluded and may lower occupancy. These are not actual "
            "running-batch/allocator slot counts, CUDA busy time, or a counterfactual "
            "gain from preemption, priority or earlier transfer."
        ),
    }


def wait_occupancy_targets(rows):
    groups = defaultdict(set)
    for row in rows:
        rid = row["next_request_id"]
        if rid is None:
            continue
        groups["all_observed_next_requests"].add(rid)
        host = (row.get("host_interval") or {}).get("host_hit_tokens_at_service", 0)
        reason = row.get("last_pre_completion_no_step_reason")
        source = row["source"]
        if host > 0 and (source, reason) in (
            ("tool_wait", "no_reusable_input_restore_step"),
            ("tool_wait", "no_host_backed_missing_copy"),
            ("join_ticket", "prefetch_capacity_budget"),
        ):
            groups[f"{source}:{reason}:later_host_hit"].add(rid)
        if (row.get("early_started_and_reused_full_bytes") or 0) > 0:
            groups[f"{source}:early_reused_full"].add(rid)
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sealed-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--queue-occupancy", action="store_true")
    parser.add_argument("--request-limit", type=int, default=48)
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
    native_events = (
        list(sealed(native_path)) if args.queue_occupancy else sealed(native_path)
    )
    report = audit_safe_prefix(
        source["wait_event_attribution"]["rows"],
        sealed(plans_path), native_events,
    )
    input_paths = [plans_path, native_path]
    if args.queue_occupancy:
        service_path = arm / "server/runtime_audit.jsonl"
        if str(service_path) not in sealed.prefixes:
            raise ValueError(f"input missing from original sealed report: {service_path}")
        lifetimes, counts = native_lifetimes(native_events, sealed(service_path))
        report["queue_occupancy"] = {
            **queue_lifetime_exposure(
                lifetimes, wait_occupancy_targets(report["rows"]),
                request_limit=args.request_limit,
            ),
            "coverage": counts,
        }
        input_paths.append(service_path)
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
            str(path): sealed.prefixes[str(path)] for path in input_paths
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "output": str(args.output),
        **({"queue_occupancy": report["queue_occupancy"]} if args.queue_occupancy
           else {"by_source": report["by_source"]}),
    }, indent=2))


if __name__ == "__main__":
    main()
