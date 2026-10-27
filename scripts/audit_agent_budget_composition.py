#!/usr/bin/env python3
"""Partition Full logical budgets from snapshots or native request boundaries."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_outstanding_request_throughput import audit_arm
from scripts.audit_sealed_wait_prefetch import SealedRecords


def event_changes(events):
    submitted, changes = {}, []
    for event in events:
        kind = event.get("kind")
        if kind not in ("llm_submit", "llm_result"):
            continue
        attrs, when, invocation = event["attributes"], event["ts_ms"], event["invocation_id"]
        request = attrs["request_id"]
        if kind == "llm_submit":
            if request in submitted:
                raise ValueError("duplicate native request arrival")
            tokens = attrs["prompt_tokens"]
            submitted[request] = invocation, tokens
        else:
            if request not in submitted or submitted[request][0] != invocation:
                raise ValueError("native completion has no matching arrival")
            tokens = submitted[request][1] + attrs["output_tokens"]
        if type(tokens) is not int or tokens < 0 or not math.isfinite(when):
            raise ValueError("invalid native context observation")
        changes.append((when, kind == "llm_result", request, invocation, tokens))
    return sorted(changes)


def composition(state, latest, pending, *, buffer_tokens=100):
    admission = state.get("agent_admission") or {}
    active, blocked = admission.get("active_invocation_ids"), admission.get("blocked_active_invocation_ids")
    if (
        type(active) is not list or len(set(active)) != len(active)
        or type(blocked) is not list or not set(blocked) <= set(active)
        or admission.get("capacity_semantics") != "conservative_sum_of_observed_agent_contexts_plus_growth_buffer"
        or type(buffer_tokens) is not int or buffer_tokens < 0
    ):
        raise ValueError("unsupported agent budget observations")
    observed = admission.get("active_agent_budgets")
    if observed is not None:
        if type(observed) is not list:
            raise ValueError("invalid per-agent observed budgets")
        latest, pending = dict(latest), dict(pending)
        identities, observed_blocked = set(), set()
        for row in observed:
            if (
                type(row) is not dict or type(identity := row.get("invocation_id")) is not str
                or identity in identities or identity not in active
                or type(tokens := row.get("tokens")) is not int or tokens <= 0
                or type(row.get("buffer_tokens")) is not int or row["buffer_tokens"] != buffer_tokens
                or type(row.get("pending")) is not bool or type(row.get("in_flight")) is not bool
                or row.get("blocked") is not None and type(row["blocked"]) is not str
                or row["in_flight"] and not row["pending"]
            ):
                raise ValueError("invalid per-agent observed budgets")
            identities.add(identity)
            if row["blocked"]:
                observed_blocked.add(identity)
            latest[identity] = tokens
            if row["pending"]:
                pending.setdefault(identity, "_observed_snapshot")
            else:
                pending.pop(identity, None)
        if identities != set(active) or observed_blocked != set(blocked):
            raise ValueError("per-agent observed budget identities disagree")
    values = Counter()
    for invocation in active:
        if invocation not in latest:
            return None
        category = (
            "model_pending" if invocation in pending else
            "known_blocked" if invocation in blocked else "between_native_requests"
        )
        values[category + "_tokens"] += latest[invocation] + buffer_tokens
        values[category + "_agents"] += 1
    if sum(values[key + "_tokens"] for key in (
        "model_pending", "known_blocked", "between_native_requests",
    )) != admission.get("logical_active_tokens"):
        return None
    values["inactive_pending_agents"] = len(set(pending) - set(active))
    values["inactive_pending_tokens"] = sum(
        latest[invocation] + buffer_tokens for invocation in set(pending) - set(active)
    )
    return values


def inspect(states, events, *, buffer_tokens=100, maximum_age_ms=1500.):
    if not math.isfinite(maximum_age_ms) or maximum_age_ms <= 0:
        raise ValueError("invalid snapshot age limit")
    changes = event_changes(events)
    if not changes:
        raise ValueError("no native model requests")
    begin, end = changes[0][0], changes[-1][0]
    times = [row["ts_ms"] for row in states]
    if any(not math.isfinite(value) for value in times) or any(
        left >= right for left, right in zip(times, times[1:])
    ):
        raise ValueError("snapshot times must increase")
    cursor, latest, pending = 0, {}, {}
    weighted, counts = Counter(), Counter()
    for index, state in enumerate(states):
        when = state["ts_ms"]
        while cursor < len(changes) and changes[cursor][0] <= when:
            _time, done, request, invocation, tokens = changes[cursor]
            latest[invocation] = tokens
            if done:
                if pending.get(invocation) != request:
                    raise ValueError("native completion changed the current request")
                del pending[invocation]
            else:
                if invocation in pending:
                    raise ValueError("an agent has concurrent sequential requests")
                pending[invocation] = request
            cursor += 1
        right = min(end, when + maximum_age_ms, times[index + 1] if index + 1 < len(times) else end)
        duration = max(0., right - max(begin, when))
        if duration == 0:
            continue
        values = composition(state, latest, pending, buffer_tokens=buffer_tokens)
        counts["covered_ms"] += duration
        counts["snapshots_in_request_span"] += 1
        counts[
            "observed_budget_snapshots" if (state.get("agent_admission") or {}).get("active_agent_budgets") is not None
            else "boundary_reconstructed_snapshots"
        ] += 1
        if values is None:
            counts["unreconciled_ms"] += duration
            counts["unreconciled_snapshots"] += 1
            continue
        counts["reconciled_ms"] += duration
        counts["reconciled_snapshots"] += 1
        weighted.update({key: value * duration for key, value in values.items()})
    duration = counts["reconciled_ms"]
    active_token_ms = sum(weighted[key + "_tokens"] for key in (
        "model_pending", "known_blocked", "between_native_requests",
    ))
    return {
        "counts": dict(counts),
        "native_request_span_ms": end - begin,
        "uncovered_ms": max(0., end - begin - counts["covered_ms"]),
        "time_weighted_agents": {
            category: weighted[category + "_agents"] / duration if duration else None
            for category in (
                "model_pending", "known_blocked", "between_native_requests", "inactive_pending",
            )
        },
        "active_budget_token_time_fraction": {
            category: weighted[category + "_tokens"] / active_token_ms if active_token_ms else None
            for category in ("model_pending", "known_blocked", "between_native_requests")
        },
        "buffer_tokens_per_agent": buffer_tokens, "maximum_snapshot_age_ms": maximum_age_ms,
    }


def audit(arm):
    work = audit_arm(arm)
    reader = SealedRecords()
    states = [
        row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == "admission_runtime_state"
    ]
    if any(row.get("physical_disabled") is not False for row in states):
        raise ValueError("runtime physical control was disabled")
    path = arm / "server/runtime_events.sglang.jsonl"
    result = inspect(states, list(reader(path)))
    if reader.prefixes[str(path)]["complete_prefix_sha256"] != work["events"]["sha256"]:
        raise ValueError("native request population changed")
    return {**result, "work": work, "input_jsonl_prefixes": reader.prefixes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True, help="LABEL=ARM_PATH")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve prior budget evidence")
    arms = {}
    for value in args.arm:
        name, separator, path = value.partition("=")
        if not separator or not name or not path or name in arms:
            parser.error("--arm requires a unique LABEL=ARM_PATH")
        arms[name] = audit(Path(path))
    if len({row["work"]["request_identity_and_token_count_sha256"] for row in arms.values()}) != 1:
        raise ValueError("native request work differs")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(), "arms": arms,
        "same_native_request_identities_and_token_counts": True,
        "auditor_sha256": sha256_file(Path(__file__)),
        "scope": [
            "Logical budgets use same-snapshot per-agent observations when available; older results fall back to native arrival and completion counts. No future context is used.",
            "Each snapshot is held until the next sample or 1500ms, clipped to the native request span. This can miss transitions within that interval.",
            "Reported fractions use only snapshots whose reconstructed sum exactly matches the observed logical active budget; mismatches and sampling gaps remain explicit.",
            "Model pending includes both running and queued native model requests. Between native requests includes tools, client work and transport; it is not proven cold time.",
            "A logical token budget is neither physical exclusive occupancy, reclaimable capacity nor removable GPU waiting. No tail is removed from workflow JCT.",
        ],
        "performance_accepted": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        name: {key: value for key, value in row.items() if key not in ("work", "input_jsonl_prefixes")}
        for name, row in arms.items()
    }, indent=2))


if __name__ == "__main__":
    main()
