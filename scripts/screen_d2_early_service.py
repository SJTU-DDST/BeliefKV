"""Screen early tool-wait offload against observed native capacity shortfalls."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_cohort_jct_paths import measured_plan
from scripts.audit_d2_tool_wait_trace import (
    Archive, CLOCK_MARGIN_MS, FREE_GRANTS, PERIODIC_GRANT, RELEASE_EVENTS, attempt,
    forecast_gate, matching_call, owner, positive_full_release, replay_tools,
)
from scripts.audit_native_system_costs import distribution
from scripts.run_causal_gpu_replay import source_plan


SHORTFALL = "selected_consumer_capacity_shortfall"
FORECAST = "tool_wait_forecast"
PHYSICAL_SCREENS = ("at_shortfall", "at_ingress", "at_forecast", "at_forecast_min_chunk")
SCREENS = (
    *PHYSICAL_SCREENS, "logical_pressure_zero_copy",
    "logical_pressure_with_observed_chunk", "logical_pressure_with_sampled_raw_fit",
)
# The same cold service rate as DemandAwareToolOffload, with the candidate's control allowance.
BYTES_PER_MS = 8_000_000
CONTROL_MS = 100.


@dataclass(frozen=True)
class Consumer:
    rid: str
    owner: tuple
    queued: float
    launch: float
    quote: float
    deficit: int
    missing: int


def first_cover(rows, target):
    total, seen = 0, set()
    for when, tokens, node in rows:
        if node is not None:
            if node in seen:
                continue
            seen.add(node)
        total += tokens
        if total >= target:
            return when, total
    return None


def modelled_advance(consumer, call, quotes, cover, *, release_reference, token_bytes, survival):
    if cover is None:
        return 0.
    _, tokens = cover
    copy_ms = tokens * token_bytes / BYTES_PER_MS
    consumer_h2d_ms = consumer.missing * token_bytes / BYTES_PER_MS
    # Charge an entire donor D2H even when the archived release eventually had a Host copy.
    donor_round_trip = CONTROL_MS + 2. * copy_ms
    # At ingress only the most recently published forecast is live in the controller.
    prior = bisect_right(quotes, consumer.queued, key=lambda item: item[0])
    for published, row in quotes[max(0, prior - 1):]:
        if published > consumer.quote:
            break
        start = max(published, consumer.queued)
        if (
            start + donor_round_trip >= call.end - CLOCK_MARGIN_MS
            or forecast_gate({**row, "tool_offload_wait_ms": donor_round_trip}, start, survival) != "passed"
        ):
            continue
        return max(0., release_reference
                   - (start + CONTROL_MS + copy_ms + consumer_h2d_ms))
    return 0.


def screen_consumers(consumers, releases, forecasts, *, chunk_tokens, token_bytes, survival):
    donors = sorted(releases, key=lambda call: call.start)
    candidates = defaultdict(list)
    counts = Counter()
    active, cursor = [], 0
    for consumer in sorted(consumers, key=lambda item: item.quote):
        while cursor < len(donors) and donors[cursor].start <= consumer.quote - CLOCK_MARGIN_MS:
            active.append(donors[cursor])
            cursor += 1
        active = [call for call in active if call.end > consumer.quote + CLOCK_MARGIN_MS]
        counts["quotes_before_first_launch"] += 1
        best_volume = False
        for call in active:
            if owner(call.origin)[:2] == consumer.owner[:2]:
                continue
            after = [row for row in releases[call] if row[0] > consumer.quote]
            cover = first_cover(after, consumer.deficit)
            if cover is None:
                continue
            best_volume = True
            old_release, _ = cover
            end = min(old_release, consumer.launch)
            donor = (call.origin["attributes"]["request_id"], call.identity, call.start)
            counts["quote_donor_pairs_with_enough_later_volume"] += 1
            counts["pairs_covering_before_original_first_launch"] += old_release < consumer.launch
            chunk_cover = cover if consumer.deficit >= chunk_tokens else first_cover(after, chunk_tokens)
            advances = (
                end - consumer.quote,
                end - max(consumer.queued, call.start),
                modelled_advance(consumer, call, forecasts[call], cover, release_reference=end,
                                 token_bytes=token_bytes, survival=survival),
                modelled_advance(consumer, call, forecasts[call], chunk_cover, release_reference=end,
                                 token_bytes=token_bytes, survival=survival),
            )
            for name, advance in zip(PHYSICAL_SCREENS, advances, strict=True):
                if advance > 0.:
                    candidates[name].append((advance, consumer.rid, donor))
        counts["quotes_with_enough_later_volume_from_one_donor"] += best_volume
    return candidates, counts


def per_request_advances(candidates, *, spend_donor_once):
    advances, donors = {}, set()
    for advance, request, donor in sorted(candidates, key=lambda item: -item[0]):
        if request not in advances and (not spend_donor_once or donor not in donors):
            advances[request] = advance
            donors.add(donor)
    return advances


def logical_pressure_candidates(controls, grants, launches, device_samples, releases, *, chunk_tokens):
    candidates, counts = defaultdict(list), Counter()
    sample_times = [when for when, _ in device_samples]
    for now, ready, donors in controls:
        sample_index = bisect_right(sample_times, now)
        free = None
        if 0 < sample_index < len(device_samples):
            before, after = device_samples[sample_index - 1], device_samples[sample_index]
            if now - before[0] <= 20. and after[0] - now <= 20.:
                free = min(before[1], after[1])
        for key, deficit, prompt in ready:
            launch = launches[key[4]]
            earlier_grants = grants[key]
            i = bisect_right(earlier_grants, now)
            limit = min(launch, earlier_grants[i]) if i < len(earlier_grants) else launch
            if limit <= now:
                continue
            counts["paused_ready_logical_shortfall_observations"] += 1
            fit = [item for item in donors if item[0] != key[1] and item[1] >= deficit]
            counts["observations_with_one_fitting_tool_donor_credit"] += bool(fit)
            counts["logical_shortfall_with_nearby_device_samples"] += free is not None
            # One full prompt and a one-token-per-decode-slot growth allowance.
            raw_fit = free is not None and free >= prompt + 24
            counts["logical_shortfall_with_nearby_raw_free_fit"] += raw_fit
            for _, credit, call in fit:
                deadline = min(limit, call.end - CLOCK_MARGIN_MS)
                if deadline <= now:
                    continue
                donor = (call.origin["attributes"]["request_id"], call.identity, call.start)
                pair = (deadline - now, key[4], donor)
                candidates["logical_pressure_zero_copy"].append(pair)
                if raw_fit:
                    candidates["logical_pressure_with_sampled_raw_fit"].append(pair)
                volume = [item for item in releases.get(call, ()) if item[0] > now]
                cover = first_cover(volume, min(chunk_tokens, credit))
                if cover is not None and cover[0] < deadline:
                    candidates["logical_pressure_with_observed_chunk"].append(
                        (cover[0] - now, key[4], donor)
                    )
    return candidates, counts


def observed_node_times(plan, events, requests, starts):
    by_id = {row["event_id"]: row["ts_ms"] for row in events}
    return {
        node.node_id: (
            by_id[node.event.event_id] if node.event is not None else
            starts[node.workflow_id] + 1000. * requests[node.request_id]["complete_offset_s"]
        )
        for node in plan.nodes
    }


def read_json(path):
    body = path.read_bytes()
    return json.loads(body), hashlib.sha256(body).hexdigest()


def read_evidence(audit, paths):
    archive = Archive(Path(audit["arm"]))
    if Path(paths["arm"]).resolve() != archive.arm.resolve() or paths["group"] != "measured_plus_warmup":
        raise ValueError("require both archive audits for the same fixed-38 replay")
    requests = paths["requests"]
    events = list(archive.records("replay/runtime_events.replay.jsonl", required=True))
    _, by_context, submits, _ = replay_tools(events)
    starts = {row["workflow_id"]: row["ts_ms"] for row in events if row["kind"] == "workflow_start"}
    if not audit["wall_to_monotonic"]["usable_offset"]:
        raise ValueError("require the archived journal clock binding")
    offset = audit["wall_to_monotonic"]["wall_offset_ms"]["p50"]
    # Fixed membership and first launches come from the complete receipt/worker audit.
    ingress = {
        rid: row for row in archive.records("server/runtime_events.sglang.jsonl", {"llm_submit"}, field="kind")
        if (rid := row["attributes"]["request_id"]) in requests
    }
    launches = {
        rid: starts[row["workflow_id"]] + 1000. * row["first_launch_offset_s"]
        for rid, row in requests.items()
    }
    releases, forecasts, observations = defaultdict(list), defaultdict(list), defaultdict(list)
    controls, grants = [], defaultdict(list)
    counts = Counter()
    for row in archive.records(
        "opportunities/admission_opportunities.jsonl",
        RELEASE_EVENTS | FREE_GRANTS | {FORECAST, SHORTFALL, PERIODIC_GRANT, "agent_join_blocked_capacity_admitted"},
    ):
        event = row["event"]
        if event == PERIODIC_GRANT:
            now = row["monotonic_s"] * 1000.
            free = max(0, row["remaining_after_pressure_tokens"])
            ready, donors = [], []
            for item in row["programs"]:
                key_row = item.get("key") or {}
                key = attempt(key_row)
                if key is None:
                    continue
                active = item["active_after_pressure"]
                credit = item["complete_budget_tokens"]
                rid = key[4]
                if rid in launches and launches[rid] > now and key[5] == 0:
                    if active:
                        grants[key].append(now)
                    elif (
                        item["application_ready"] and item["pending_before"]
                        and item.get("first_materialization_held") is False
                        and not item["marked_after_pressure"] and credit > free
                        and rid in ingress and owner(ingress[rid]) == key[:4]
                        and ingress[rid]["ts_ms"] - offset <= now
                    ):
                        ready.append((key, credit - free, requests[rid]["prompt_tokens"]))
                if active and item["acting_before"] and not item["pending_before"]:
                    call, _ = matching_call(by_context, key_row, now)
                    if call is not None and owner(call.origin) == key[:4]:
                        donors.append((key[1], credit, call))
            if ready:
                controls.append((now, ready, donors))
            continue
        if event in FREE_GRANTS or event == "agent_join_blocked_capacity_admitted":
            for item in row["programs"]:
                key = attempt(item["key"])
                if key is not None and key[4] in launches:
                    grants[key].append(row["monotonic_s"] * 1000.)
            continue
        if event == SHORTFALL:
            rid = row["request_id"]
            if rid in requests:
                observations[rid].append(row)
            continue
        if event == "parent_pressure_demoted" and row.get("source") != "tool_wait":
            continue
        when = row["ts_ms"] - offset
        call, _ = matching_call(by_context, row, when)
        if call is None:
            continue
        if event == FORECAST:
            if call.start <= row["observed_monotonic_ms"] <= when:
                forecasts[call].append((when, row))
        elif units := positive_full_release(row):
            creation = row.get("creation_time")
            node = (row["node_id"], creation) if creation is not None else None
            releases[call].append((when, units, node))
    consumers = []
    for rid, rows in observations.items():
        counts["fixed38_quotes"] += len(rows)
        counts["fixed38_requests_with_quote"] += 1
        if {row["attempt_id"] for row in rows} != {0}:
            counts["unverified_first_attempt_requests"] += 1
            continue
        if rid not in ingress or owner(ingress[rid]) != owner(submits[rid]):
            counts["unverified_native_ingress_requests"] += 1
            continue
        queued = ingress[rid]["ts_ms"] - offset
        launched = launches[rid]
        for row in rows:
            if owner(row) != owner(submits[rid]):
                raise ValueError("capacity quote belongs to a foreign request owner")
            quote = row["ts_ms"] - offset
            deficit = row["deficit_tokens"]
            missing = row["missing_full_tokens"]
            if not (
                type(deficit) is int and deficit > 0 and type(missing) is int and missing > 0
                and deficit == row["required_device_tokens"] - row["device_free_tokens"]
            ):
                raise ValueError("invalid native capacity shortfall")
            if queued <= quote < launched - CLOCK_MARGIN_MS:
                consumers.append(Consumer(rid, owner(row), queued, launched, quote, deficit, missing))
            else:
                counts["quotes_outside_prelaunch_interval"] += 1
    for stream in (forecasts, releases):
        for rows in stream.values():
            rows.sort(key=lambda row: row[0])
    device_samples = []
    for row in archive.records("server/host_pool_telemetry.jsonl", {"host_pool_usage"}, required=True):
        full = (row.get("device_pools") or {}).get("full") or {}
        if full.get("observable") and type(free := full.get("free_units")) is int:
            device_samples.append((row["ts_ms"] - offset, free))
    device_samples.sort()
    for name, evidence in archive.inputs.items():
        if name in audit["input_prefixes"] and evidence != audit["input_prefixes"][name]:
            raise ValueError(f"archived log changed: {name}")
    for rows in grants.values():
        rows.sort()
    logical = controls, grants, launches, device_samples
    return events, starts, consumers, forecasts, releases, counts, archive.inputs, logical


def arm(plan, saved_audit, paths, *, chunk_tokens, token_bytes):
    from scripts.d2_wait_dag import replay_wait_savings

    events, starts, consumers, forecasts, releases, counts, inputs, logical = read_evidence(saved_audit, paths)
    cohort = paths["cohort"]
    fixed18, warmup = cohort["measured_workflow_ids"], cohort["warmup_workflow_ids"]
    fixed38 = fixed18 + warmup
    selected = measured_plan(plan, fixed38)
    times = observed_node_times(selected, events, paths["requests"], starts)
    for node in selected.nodes:
        if node.event is not None and node.event.kind.value == "workflow_end" and abs(
            times[node.node_id] - starts[node.workflow_id] - paths["workflows"][node.workflow_id]["jct_ms"]
        ) > 1.e-5:
            raise ValueError("fixed workflow does not reproduce archived JCT")
    candidates, screened = screen_consumers(
        consumers, releases, forecasts, chunk_tokens=chunk_tokens, token_bytes=token_bytes,
        survival=saved_audit["policy"]["tool_offload_min_survival"],
    )
    logical_rows, logical_counts = logical_pressure_candidates(*logical, releases, chunk_tokens=chunk_tokens)
    candidates.update(logical_rows)
    views = {}
    for name in SCREENS:
        for spend_once in (True, False) if name.startswith("at_forecast") else (False,):
            advances = per_request_advances(candidates[name], spend_donor_once=spend_once)
            savings = replay_wait_savings(selected, times, advances)
            groups = {}
            for label, members in (("fixed18", fixed18), ("fixed38", fixed38)):
                original = statistics.mean(paths["workflows"][key]["jct_ms"] for key in members)
                mean = statistics.mean(savings[key] for key in members)
                groups[label] = {
                    "workflows": len(members),
                    "with_positive_jct_change": sum(savings[key] > 1.e-6 for key in members),
                    "mean_savings_s": mean / 1000.,
                    "percent_savings": mean / original * 100.,
                }
            views[name + ("_one_per_donor" if spend_once else "")] = {
                "candidate_edges": len(candidates[name]),
                "advanced_requests": len(advances),
                "request_advance_ms": distribution(advances.values()),
                "groups": groups,
            }
    return {
        "counts": counts + screened + logical_counts, "input_prefixes": inputs,
        "quoted_deficit_tokens": distribution(c.deficit for c in consumers),
        "quote_to_first_launch_ms": distribution(c.launch - c.quote for c in consumers),
        "scenarios": views,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", nargs=3, metavar=("LABEL", "AUDIT", "PATHS"), required=True)
    parser.add_argument("--chunk-tokens", type=int, required=True)
    parser.add_argument("--token-bytes", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.chunk_tokens <= 0 or args.token_bytes <= 0:
        parser.error("provide a fresh output and positive capacity geometry")
    plan, source = None, None
    out = {
        "scope": [
            "Closed fixed cohorts, complete archived launches, and only recorded native consumer deficits.",
            "Credit requires a tool call already open at the quote and enough later recorded releases within it.",
            "Earlier private residency, bus scheduling, and causal service eligibility are assumptions.",
            "The quote and ingress scenarios have zero copy cost and independent request credit.",
            "The forecast scenario charges cold-rate donor D2H, consumer H2D, and 100 ms control.",
            "Logical pressure uses observed pause and donor budgets. The zero-copy variant assumes "
            "the receiver can use that budget immediately. The chunk variant additionally demands a later "
            "observed release from that donor before the original grant and tool return.",
            "The sampled raw-fit variant requires bracketing allocator samples within 20 ms on each side "
            "and enough free space for the full prompt and 24 growth tokens in both samples. "
            "This is sampled aggregate headroom, without physical allocator or slot authority.",
            "Only consumer queue intervals are changed. Decode, donor service, transfers of other requests "
            "and all non-target DAG delays keep their archived values.",
        ],
        "chunk_tokens": args.chunk_tokens, "token_bytes": args.token_bytes, "arms": {},
    }
    for label, audit_path, paths_path in args.arm:
        audit_doc, audit_sha = read_json(Path(audit_path))
        paths, paths_sha = read_json(Path(paths_path))
        if label in out["arms"] or len(audit_doc["arms"]) != 1:
            parser.error("provide one unique label per single-arm archive")
        bound_source = paths["source_evidence"]["source_arm"]
        if plan is None:
            source = bound_source
            plan, _, _ = source_plan(Path(source))
        elif source != bound_source:
            raise ValueError("same source workload required")
        out["arms"][label] = {
            **arm(plan, next(iter(audit_doc["arms"].values())), paths,
                  chunk_tokens=args.chunk_tokens, token_bytes=args.token_bytes),
            "inputs_sha256": {audit_path: audit_sha, paths_path: paths_sha},
        }
        print(f"Completed archive {label}", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="ascii") as stream:
        json.dump(out, stream, indent=2, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
