#!/usr/bin/env python3
"""Describe exact successor work after tool interruptions, not a new schedule."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.replay_token_capture import _wire
from scripts.audit_causal_jct_paths import finite, unique
from scripts.audit_join_reentry_service import identity
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_tool_reentry_service import tool_reentries


def validated_requests(rows):
    requests = unique(rows, "request_id")
    clock = None
    for rid, row in requests.items():
        receipt = row.get("native_receipt") or {}
        native_identity = receipt.get("identity")
        times = [row.get(name) for name in (
            "submitted_monotonic_ms", "native_complete_monotonic_ms",
            "http_complete_monotonic_ms",
        )]
        if (
            row.get("exact_tokens_verified") is not True
            or receipt.get("request_id") != rid
            or not isinstance(native_identity, list) or len(native_identity) != 4
            or identity(dict(zip(
                ("workflow_id", "invocation_id", "context_id", "context_epoch"),
                native_identity,
            ))) is None
            or not isinstance(receipt.get("clock_domain"), str)
            or not receipt["clock_domain"]
            or clock is not None and clock != receipt["clock_domain"]
            or not all(finite(value) and value >= 0 for value in times)
            or not times[0] <= times[1] <= times[2]
            or receipt.get("complete_monotonic_ms") != times[1]
        ):
            raise ValueError("request identity, exact work or native clock is invalid")
        clock = receipt["clock_domain"]
        output_ids = receipt.get("output_token_ids")
        if (
            type(row.get("prompt_tokens")) is not int or row["prompt_tokens"] < 0
            or receipt.get("prompt_tokens") != row["prompt_tokens"]
            or type(row.get("output_tokens")) is not int or row["output_tokens"] < 0
            or not isinstance(output_ids, list) or len(output_ids) != row["output_tokens"]
        ):
            raise ValueError("request token work differs from native receipt")
        try:
            output_hash = hashlib.sha256(_wire(output_ids)).hexdigest()
        except (TypeError, ValueError) as error:
            raise ValueError("invalid native output token IDs") from error
        for field in ("prompt_sha256", "output_sha256"):
            value = row.get(field)
            if (
                type(value) is not str or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
                or (output_hash if field == "output_sha256" else receipt.get(field)) != value
            ):
                raise ValueError("request token hash differs from native receipt")
        if (
            not isinstance(row.get("native_finish_reason"), dict)
            or not row["native_finish_reason"]
            or row["native_finish_reason"] != receipt.get("native_finish_reason")
        ):
            raise ValueError("request stopping differs from native receipt")
    if not requests:
        raise ValueError("no complete request work")
    return requests


def continuation_rows(episodes, requests, native_events, *, short_gap_ms):
    if not finite(short_gap_ms) or short_gap_ms <= 0:
        raise ValueError("positive finite short tool boundary required")
    native = {}
    for event in native_events:
        if event.get("kind") != "llm_submit":
            continue
        rid = (event.get("attributes") or {}).get("request_id")
        if rid in native:
            raise ValueError("duplicate native first-service request")
        native[rid] = event
    result, seen = [], set()
    for episode in episodes:
        rid = episode.get("request_id")
        if rid is None:
            continue
        if rid in seen:
            raise ValueError("duplicate tool successor")
        seen.add(rid)
        prior = requests.get(episode["origin_request_id"])
        following = requests.get(rid)
        event = native.get(rid)
        if prior is None or following is None or event is None:
            raise ValueError("tool successor lacks exact native work")
        expected = (
            episode["workflow_id"], episode["invocation_id"],
            episode["context_id"], episode["context_epoch"],
        )
        previous_identity = (
            episode["workflow_id"], episode["invocation_id"],
            episode["origin_context_id"], episode["origin_context_epoch"],
        )
        if (
            tuple(following["native_receipt"]["identity"]) != expected
            or tuple(prior["native_receipt"]["identity"]) != previous_identity
            or identity(event) != expected
        ):
            raise ValueError("tool episode changed native identity")
        gap = following["submitted_monotonic_ms"] - prior["native_complete_monotonic_ms"]
        duration = episode.get("tool_round_duration_ms")
        if gap < 0 or not finite(duration) or duration < 0:
            raise ValueError("invalid complete tool-round chronology")
        before, after = prior["native_receipt"], following["native_receipt"]
        same_context = (
            expected[2] == previous_identity[2]
            and expected[3] == previous_identity[3] + 1
            and type(before.get("session_id")) is str and bool(before["session_id"])
            and type(before.get("session_generation")) is int
            and before["session_generation"] >= 0
            and type(after.get("session_generation")) is int
            and after["session_generation"] >= 0
            and (before["session_id"], before["session_generation"])
            == (after.get("session_id"), after.get("session_generation"))
        )
        reasons = []
        if duration > short_gap_ms:
            reasons.append("long_tool_round")
        if gap > short_gap_ms:
            reasons.append("long_completion_to_submit_gap")
        if not same_context:
            reasons.append("context_or_session_changed_or_unknown")
        if episode.get("context_compactions_before_successor"):
            reasons.append("context_compaction")
        attrs = event.get("attributes") or {}
        observed = {}
        for field in (
            "cached_tokens_device", "cached_tokens_host",
            "mamba_host_hit_slots", "uncached_prompt_tokens",
        ):
            value = attrs.get(field)
            if type(value) is not int or value < 0:
                raise ValueError("native cache evidence is unavailable")
            observed[field] = value
        result.append({
            "workflow_id": episode["workflow_id"],
            "invocation_id": episode["invocation_id"],
            "origin_request_id": episode["origin_request_id"], "request_id": rid,
            "tool_round_duration_ms": duration,
            "native_completion_to_next_submit_ms": gap,
            "same_context_and_session": same_context,
            "short_return_identity_candidate": not reasons,
            "exclusion_reasons": reasons,
            "prior_output_tokens": prior["output_tokens"],
            "successor_output_tokens": following["output_tokens"],
            "successor_prompt_tokens": following["prompt_tokens"],
            "first_service_cache_evidence": observed,
        })
    return result


def summarize(rows):
    output = [row["successor_output_tokens"] for row in rows]
    return {
        "requests": len(rows),
        "successor_output_tokens": distribution(output),
        "prior_output_tokens": distribution(row["prior_output_tokens"] for row in rows),
        "successor_prompt_tokens": distribution(row["successor_prompt_tokens"] for row in rows),
        "successor_output_at_least": {
            str(boundary): sum(value >= boundary for value in output)
            for boundary in (128, 256, 512, 1024, 2048, 4096)
        },
        "first_service_cache_counters": {
            "with_full_host_hit": sum(
                row["first_service_cache_evidence"]["cached_tokens_host"] > 0 for row in rows
            ),
            "with_mamba_host_hit": sum(
                row["first_service_cache_evidence"]["mamba_host_hit_slots"] > 0 for row in rows
            ),
            "uncached_prompt_tokens": sum(
                row["first_service_cache_evidence"]["uncached_prompt_tokens"] for row in rows
            ),
        },
    }


def audit_arm(arm, *, short_gap_ms):
    reader = SealedRecords()
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    starts = Counter(row["workflow_id"] for row in events if row["kind"] == "workflow_start")
    ends = Counter(row["workflow_id"] for row in events if row["kind"] == "workflow_end")
    if not starts or starts != ends or set(starts.values()) != {1}:
        raise ValueError("complete workflow event population required")
    requests = validated_requests(reader(arm / "replay/native_requests.jsonl"))
    submissions = {
        row["attributes"]["request_id"] for row in events if row["kind"] == "llm_submit"
    }
    if submissions != requests.keys():
        raise ValueError("request work differs from the workflow event population")
    episodes, _, diagnostics = tool_reentries(events)
    rows = continuation_rows(
        episodes, requests, reader(arm / "server/runtime_events.sglang.jsonl"),
        short_gap_ms=short_gap_ms,
    )
    short = [row for row in rows if row["short_return_identity_candidate"]]
    prior_buckets = defaultdict(list)
    for row in short:
        value = row["prior_output_tokens"]
        prior_buckets[
            "lt_128" if value < 128 else "128_to_511" if value < 512 else "ge_512"
        ].append(row)
    work = {
        rid: (
            row["prompt_tokens"], row["output_tokens"],
            row["prompt_sha256"], row["output_sha256"],
            row["native_finish_reason"], row["native_receipt"]["sampling_parameters"],
            row["native_receipt"]["identity"],
        ) for rid, row in requests.items()
    }
    return {
        "arm": str(arm.resolve()), "workflow_count": len(starts),
        "exact_request_count": len(requests), "tool_rounds": len(episodes),
        "tool_round_status_counts": dict(Counter(row["status"] for row in episodes)),
        "tool_round_diagnostics": diagnostics,
        "all_proven_successors": summarize(rows),
        "short_return_identity_candidates": summarize(short),
        "short_candidates_by_observed_prior_output": {
            bucket: summarize(values) for bucket, values in prior_buckets.items()
        },
        "exclusion_counts": dict(Counter(
            reason for row in rows for reason in row["exclusion_reasons"]
        )),
        "input_jsonl_prefixes": reader.prefixes, "rows": rows,
    }, work


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True, help="NAME=ARM_PATH")
    parser.add_argument("--short-gap-ms", type=float, default=1000.)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve existing evidence")
    arms, reference = {}, None
    for value in args.arm:
        name, path = value.split("=", 1)
        if not name or name in arms:
            raise ValueError("unique arm name required")
        result, work = audit_arm(Path(path).resolve(), short_gap_ms=args.short_gap_ms)
        if reference is not None and work != reference:
            raise ValueError("arms have different exact request work or stopping")
        reference = work
        arms[name] = result
    report = {
        "scope": [
            "Observed tool interruptions and exact native request work; all complete workflows retained.",
            "The short boundary is descriptive, not a calibrated scheduling threshold.",
            "Output tokens are work labels, not measured GPU executor service.",
            "Native clocks measure completion-to-submit only; client and native clocks are not subtracted.",
            "Cache counters are observed at first service after any restoration; not residency at tool end or admission.",
            "Prior output is observable before successor submission; successor work is evaluation-only future information.",
            "No counterfactual ordering, avoided migration, measured CPU overhead or JCT gain is inferred.",
        ],
        "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "short_gap_ms": args.short_gap_ms,
        "exact_receipt_work_verified": True,
        "same_request_work_verified": len(arms) > 1,
        "performance_accepted": False, "arms": arms,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        name: result["short_return_identity_candidates"] for name, result in arms.items()
    }, indent=2))


if __name__ == "__main__":
    main()
