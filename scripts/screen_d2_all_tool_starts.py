"""Screen earlier D2 capacity using every tool start and native ingress in a sealed replay."""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_cohort_jct_paths import measured_plan
from scripts.audit_d2_tool_wait_trace import (
    Archive, CLOCK_MARGIN_MS, FREE_GRANTS, PAUSE_EVENT, PERIODIC_GRANT, PRESSURE_PAUSE,
    REPLAY_EVENTS, attempt, forecast_gate, matching_call, owner, replay_tools,
)
from scripts.audit_native_system_costs import distribution, finite_number
from scripts.audit_tool_reentry_service import tool_reentries
from scripts.d2_wait_dag import replay_request_shifts
from scripts.run_causal_gpu_replay import source_plan
from scripts.screen_d2_early_service import BYTES_PER_MS, CONTROL_MS, observed_node_times


AUDIT = "server/runtime_audit.jsonl"
QUEUE = "server/runtime_events.sglang.jsonl"
JOURNAL = "opportunities/admission_opportunities.jsonl"
MAX_SAMPLE_AGE_MS = 1000.
INITIAL_REGISTRATION = "agent_initial_registration_decision"
JOIN_GRANT = "agent_join_blocked_capacity_admitted"
TOOL_GRANT = "agent_predicted_tool_replacement"


@dataclass(frozen=True)
class Consumer:
    rid: str
    invocation: str
    ingress: float
    first: float
    first_chunk: int
    observed_host_tokens: int
    active_from: float | None = None


@dataclass(frozen=True)
class Candidate:
    call: object
    consumer: Consumer
    begin: float
    service: float
    restore: float
    release_tokens: int


class Samples:
    def __init__(self, rows):
        ordered = sorted(rows)
        self.times = tuple(row[0] for row in ordered)
        self.values = tuple(row[1:] for row in ordered)

    def at(self, when):
        index = bisect_right(self.times, when) - 1
        if index < 0 or when - self.times[index] > MAX_SAMPLE_AGE_MS:
            return None
        return self.values[index]

    def host_funding(self, start, end, tokens):
        start_index = bisect_right(self.times, start) - 1
        end_index = bisect_right(self.times, end) - 1
        if any(
            self.values[index][0] < tokens
            for index in range(max(0, start_index), end_index + 1)
        ):
            return "below_reserved_floor"
        if (
            self.at(start) is None or self.at(end) is None
            or any(b - a > 2 * MAX_SAMPLE_AGE_MS for a, b in zip(
                self.times[start_index:end_index], self.times[start_index + 1:end_index + 1],
            ))
        ):
            return "unobserved"
        return "funded"


class Lane:
    def __init__(self, intervals):
        self.busy = []
        for begin, end in sorted(intervals):
            if self.busy and begin <= self.busy[-1][1]:
                self.busy[-1] = (self.busy[-1][0], max(end, self.busy[-1][1]))
            else:
                self.busy.append((begin, end))
        self.ends = tuple(end for _, end in self.busy)

    def gap(self, start, length, deadline):
        if length:
            for index in range(bisect_right(self.ends, start), len(self.busy)):
                busy_start, busy_end = self.busy[index]
                if start + length <= busy_start:
                    break
                start = busy_end
                if start + length > deadline:
                    return None
        return (start, start + length) if start + length <= deadline else None


def observed_service(archive, native, max_running):
    first, wrong, completions, batches = {}, set(), {}, []
    for row in archive.records(AUDIT, {"gpu_service_sample"}, required=True):
        launch = row["launch_monotonic_ms"]
        batches.append(launch)
        for item in row["request_samples"]:
            if not (item.get("token_delta", 0) > 0 or item.get("output_token_delta", 0) > 0):
                continue
            rid = item.get("request_id")
            if rid not in native:
                continue
            key = attempt(item)
            if key is None or key[:4] != owner(native[rid]) or key[5] != 0:
                wrong.add(rid)
                continue
            if rid not in first or launch < first[rid][0]:
                first[rid] = launch, item
    # In-flight model requests count against the available-slot screen even
    # when they were absent from a particular sampled batch.
    for rid, row in native.items():
        complete = row.get("_native_complete")
        if complete is not None:
            completions[rid] = complete
    launched = sorted(launch for launch, _ in first.values())
    ended = sorted(
        done for rid, done in completions.items() if rid in first and done >= first[rid][0]
    )
    free_slot = [
        at for at in sorted(set(batches))
        if bisect_right(launched, at) - bisect_right(ended, at) < max_running
    ]
    return {rid: row for rid, row in first.items() if rid not in wrong}, free_slot


def read_state(archive, offset, max_running, host_reserve):
    native, ingresses = {}, {}
    for row in archive.records(QUEUE, {"llm_submit", "llm_result"}, field="kind", required=True):
        attrs = row["attributes"]
        rid = attrs["request_id"]
        if row["kind"] == "llm_submit":
            if rid in native:
                raise ValueError(f"duplicate native ingress: {rid}")
            native[rid] = row
            ingresses[rid] = row["ts_ms"] - offset
        elif rid in native and owner(row) == owner(native[rid]):
            native[rid]["_native_complete"] = attrs.get("complete_monotonic_ms")
    first, boundaries = observed_service(archive, native, max_running)
    pool, transfers = [], defaultdict(list)
    for row in archive.records("server/host_pool_telemetry.jsonl", {"host_pool_usage"}, required=True):
        host = row["pools"].get("full", {})
        device = row["device_pools"].get("full", {})
        if device.get("observable") is True and host:
            pool.append((
                row["ts_ms"] - offset, host["capacity_units"] - host["used_units"] - host_reserve,
                device["free_units"], device["evictable_units"],
            ))
    for row in archive.records("server/transfer_telemetry.jsonl", required=True):
        if row.get("status") != "completed" or row.get("telemetry_origin") != "native_hicache_ack_v0520":
            continue
        start, end, direction = row["submit_ts_ms"], row["complete_ts_ms"], row["direction"]
        if direction not in ("d2h", "h2d") or not all(map(finite_number, (start, end))) or start > end:
            raise ValueError("invalid archived transfer boundary")
        transfers[direction].append((start - offset - CLOCK_MARGIN_MS, end - offset + CLOCK_MARGIN_MS))
    return native, ingresses, first, boundaries, Samples(pool), {
        direction: Lane(transfers[direction]) for direction in ("d2h", "h2d")
    }


def queue_overlaps(calls, consumers, counts):
    """Assign a queue interval to each open tool without using a shortage record."""
    openings = iter(sorted(consumers, key=lambda item: item.ingress))
    opening = next(openings, None)
    pending = []
    for call in sorted(calls, key=lambda item: item.start):
        if call.end is None or call.origin is None or call.end <= call.start:
            counts["unmatched_start_end_or_origin"] += 1
            continue
        while opening is not None and opening.ingress < call.end:
            pending.append(opening)
            opening = next(openings, None)
        pending = [item for item in pending if item.first > call.start]
        invocation = call.origin["invocation_id"]
        matching = [
            item for item in pending
            if item.ingress < call.end and item.invocation != invocation
        ]
        counts["starts_with_another_native_queued_request"] += bool(matching)
        if matching:
            yield call, matching


def check_consumer(
    consumer, release, end, tokens, boundaries, samples, dma, *, rate, copy_ms,
    require_active=False,
):
    deadline = min(consumer.first, end - CONTROL_MS - copy_ms)
    h2d_ms = consumer.observed_host_tokens * rate
    start = max(release, consumer.ingress,
                consumer.active_from if require_active else consumer.ingress)
    while start < deadline:
        load = dma.gap(start, h2d_ms, deadline)
        if load is None:
            return None
        index = bisect_left(boundaries, load[1])
        if index == len(boundaries) or (service := boundaries[index]) >= deadline:
            return None
        pool = samples.at(service)
        if pool is not None:
            _, free, _ = pool
            deficit = consumer.first_chunk + consumer.observed_host_tokens - free
            if 0 < deficit <= tokens:
                return service
        next_sample = bisect_right(samples.times, service)
        if next_sample == len(samples.times):
            return None
        start = samples.times[next_sample]
    return None


def restored_cost(restore, end, h2d):
    # Add exposed donor restore even if it overlapped in the observed baseline.
    load = h2d.gap(end, restore, float("inf"))
    return load[1] - end


def candidates_for(
    calls, consumers, forecasts, evidence, *, fraction, mode, rate, survival, require_active,
):
    native, _, _, boundaries, samples, dma = evidence
    counts, candidates = Counter(), []
    for call, queue in queue_overlaps(calls, consumers, counts):
        origin = call.origin["attributes"]["request_id"]
        row = native.get(origin)
        if row is None or owner(row) != owner(call.origin):
            counts["no_bound_origin_native_request"] += 1
            continue
        total = int(row["attributes"]["prompt_tokens"] * fraction)
        if total <= 0:
            continue
        forecast = forecasts.get(call)
        start = call.start + CLOCK_MARGIN_MS
        predicted = mode in ("prediction", "after_ingress", "adaptive_prediction")
        if predicted or mode in ("oracle_same_publication", "adaptive_oracle_same_publication"):
            if forecast is None:
                counts["no_forecast_for_call"] += 1
                continue
            start = max(start, forecast[0])
        for consumer in sorted(queue, key=lambda item: (item.ingress, item.rid)):
            begin = start
            if mode == "after_ingress" or require_active and mode in (
                "adaptive_prediction", "adaptive_oracle_same_publication",
            ):
                begin = max(begin, consumer.ingress)
            target = total
            if "adaptive" in mode:
                pool = samples.at(max(begin, consumer.ingress))
                if pool is None:
                    continue
                target = consumer.first_chunk + consumer.observed_host_tokens - pool[1]
                if not 0 < target <= total:
                    continue
            missing_copy = (
                0 if mode == "oracle_already_backed"
                else math.ceil(target / 4) if mode == "oracle_three_quarters_backed"
                else target
            )
            restore = target * rate
            copy_ms = missing_copy * rate
            backup = dma["d2h"].gap(begin, copy_ms, call.end - restore - CONTROL_MS)
            if backup is None:
                counts["no_round_trip_transfer_gap"] += 1
                if mode != "after_ingress" and "adaptive" not in mode:
                    break
                continue
            horizon = backup[1] - begin + restore + CONTROL_MS
            if predicted:
                if forecast_gate({**forecast[1], "tool_offload_wait_ms": horizon}, begin, survival) != "passed":
                    # Non-adaptive tool-start modes hold the same quote for
                    # every receiver in this episode.
                    counts["forecast_residual_rejected"] += 1
                    if mode == "prediction":
                        break
                    continue
            if missing_copy:
                funding = samples.host_funding(backup[0], call.end, missing_copy)
                if funding != "funded":
                    counts[f"host_{funding}"] += 1
                    if mode != "after_ingress" and "adaptive" not in mode:
                        break
                    continue
            service = check_consumer(
                consumer, backup[1], call.end, target, boundaries, samples, dma["h2d"],
                rate=rate, copy_ms=restore, require_active=require_active,
            )
            if service is None:
                counts["no_sampled_service_slot_and_extent_fit"] += 1
                continue
            counts["feasible_pairs"] += 1
            candidates.append(Candidate(
                call, consumer, backup[0], service, restored_cost(restore, call.end, dma["h2d"]),
                target,
            ))
            # One receiver per tool episode, in native registration order.
            break
    return candidates, counts


def select(candidates):
    receivers, origins, result = set(), set(), []
    busy_until = -1.
    for item in sorted(candidates, key=lambda row: (row.begin, row.consumer.ingress, row.consumer.rid)):
        origin = item.call.origin["attributes"]["request_id"]
        if item.begin < busy_until or item.consumer.rid in receivers or origin in origins:
            continue
        if item.consumer.first - item.service <= item.restore:
            continue
        busy_until = item.call.end + item.restore
        receivers.add(item.consumer.rid)
        origins.add(origin)
        result.append(item)
    return result


def admission_observations(row):
    kind = row["event"]
    if kind == PERIODIC_GRANT:
        for program in row["programs"]:
            if program["pending_before"]:
                active = (
                    program["active_after_pressure"] and not program["marked_after_pressure"]
                    and program["application_ready"] and not program["first_materialization_held"]
                )
                yield program.get("key"), active
    elif kind == INITIAL_REGISTRATION:
        yield row["key"], row["active_after_arrival"]
    elif kind in (PAUSE_EVENT, PRESSURE_PAUSE):
        if row.get("key") is not None:
            yield row["key"], False
    elif kind == TOOL_GRANT:
        yield row["consumer"], True
    else:
        for program in row["programs"]:
            yield program.get("key"), True


def active_suffix(observations, ingress):
    """Earliest recorded grant in the final active interval before first service."""
    active = None
    for at, allowed in sorted(observations):
        if not allowed:
            active = None
        elif active is None:
            active = max(at, ingress)
    return active


def evaluate(archive, paths, audit, *, chunk, token_bytes, max_running):
    if Path(paths["arm"]).resolve() != archive.arm.resolve() or paths["group"] != "measured_plus_warmup":
        raise ValueError("require the fixed-38 path audit for this native archive")
    if not audit["wall_to_monotonic"]["usable_offset"]:
        raise ValueError("require a paired monotonic and wall-clock binding")
    offset = audit["wall_to_monotonic"]["wall_offset_ms"]["p50"]
    survival = audit["policy"]["tool_offload_min_survival"]
    host_reserve = (2 * 1024**3 + token_bytes - 1) // token_bytes
    events = list(archive.records(
        "replay/runtime_events.replay.jsonl", REPLAY_EVENTS | {
            "join_satisfied", "join_create", "join_timeout", "call", "spawn",
            "invocation_create", "structured_action", "context_advance",
        },
        field="kind", required=True,
    ))
    calls, by_context, submits, replay_counts = replay_tools(events)
    native, ingress, first, _, _, _ = evidence = read_state(
        archive, offset, max_running, host_reserve,
    )
    forecast_rows, quote_requests, h2d, admissions = {}, set(), defaultdict(dict), defaultdict(list)
    admission_events = FREE_GRANTS | {
        PERIODIC_GRANT, INITIAL_REGISTRATION, PAUSE_EVENT, PRESSURE_PAUSE, JOIN_GRANT, TOOL_GRANT,
    }
    for row in archive.records(JOURNAL, admission_events | {
        "tool_wait_forecast", "selected_consumer_capacity_shortfall", "native_demand_restore_staged",
    }, required=True):
        if row["event"] in admission_events:
            at = row.get("monotonic_s")
            at = at * 1000. if at is not None else row["ts_ms"] - offset + CLOCK_MARGIN_MS
            for key, allowed in admission_observations(row):
                rid = key.get("request_id") if key else None
                if rid in first and attempt(key) == attempt(first[rid][1]) and at < first[rid][0]:
                    admissions[rid].append((at, allowed))
        elif row["event"] == "selected_consumer_capacity_shortfall":
            quote_requests.add(row["request_id"])
        elif row["event"] == "native_demand_restore_staged":
            rid = row["request_id"]
            if (
                rid in first and row["native_batch_size"] == 1
                and attempt(row) == attempt(first[rid][1])
                and ingress[rid] <= row["monotonic_ms"] <= first[rid][0]
            ):
                h2d[rid][row["native_submit_ts_ms"]] = row["native_full_tokens"]
        else:
            at = row["ts_ms"] - offset
            call, _ = matching_call(by_context, row, at)
            if call is not None and call.start <= row["observed_monotonic_ms"] <= at + CLOCK_MARGIN_MS:
                if call not in forecast_rows or at < forecast_rows[call][0]:
                    forecast_rows[call] = at, row
    consumers = [
        Consumer(rid, item["invocation_id"], ingress[rid] + CLOCK_MARGIN_MS, at,
                 min(chunk, item["token_delta"]), max(h2d[rid].values(), default=0),
                 active_suffix(admissions[rid], ingress[rid] + CLOCK_MARGIN_MS))
        for rid, (at, item) in first.items()
        if rid in submits and owner(submits[rid]) == owner(native[rid])
        and ingress[rid] + CLOCK_MARGIN_MS < at
        and item["phase"] == "prefill" and item.get("token_delta", 0) > 0
    ]
    rounds, _, _ = tool_reentries(events)
    by_origin = {
        row["origin_request_id"]: row["request_id"] for row in rounds
        if row.get("request_id") and row.get("observed_tool_starts") == 1
    }
    selected_calls = [
        call for call in calls if call.origin is None or len([
            sibling for sibling in by_context[(call.origin["context_id"], call.origin["context_epoch"])]
            if sibling.origin is call.origin and sibling.start <= (call.end or call.start)
            and (sibling.end is None or sibling.end >= call.start)
        ]) == 1
    ]
    raw_plan, _, _ = source_plan(Path(paths["source_evidence"]["source_arm"]))
    fixed18 = paths["cohort"]["measured_workflow_ids"]
    fixed38 = fixed18 + paths["cohort"]["warmup_workflow_ids"]
    plan = measured_plan(raw_plan, fixed38)
    original_starts = {row["workflow_id"]: row["ts_ms"] for row in events if row["kind"] == "workflow_start"}
    times = observed_node_times(plan, events, paths["requests"], original_starts)
    reference = replay_request_shifts(plan, times, {})
    if any(value != 0. for value in reference.values()):
        raise ValueError("zero-displacement replay changed fixed-cohort JCT")
    modes = {}
    arms = [
        (.5, "after_ingress", False), (.5, "prediction", False),
        (.5, "oracle_same_publication", False), (.5, "oracle_at_start", False),
        (1., "oracle_at_start", False), (.5, "adaptive_prediction", False),
        (.5, "adaptive_oracle_same_publication", False), (.5, "adaptive_oracle_at_start", False),
        (.5, "oracle_three_quarters_backed", False), (.5, "oracle_already_backed", False),
        (.5, "adaptive_prediction", True), (.5, "adaptive_oracle_same_publication", True),
        (.5, "adaptive_oracle_at_start", True), (.5, "oracle_already_backed", True),
    ]
    active_consumers = [item for item in consumers if item.active_from is not None]
    for fraction, mode, require_active in arms:
        label = f"{fraction:g}:{mode}" + (":observed_active" if require_active else "")
        print(f"  {label}", flush=True)
        candidates, counts = candidates_for(
            selected_calls, active_consumers if require_active else consumers,
            forecast_rows, evidence, fraction=fraction, mode=mode,
            rate=token_bytes / BYTES_PER_MS, survival=survival, require_active=require_active,
        )
        pairs = select(candidates)
        shifts, donor_cost_unknown = Counter(), 0
        for pair in pairs:
            if pair.consumer.rid in plan.request_usage:
                shifts[pair.consumer.rid] += pair.consumer.first - pair.service
            origin = pair.call.origin["attributes"]["request_id"]
            successor = by_origin.get(origin)
            if successor in plan.request_usage:
                shifts[successor] -= pair.restore
            elif pair.call.origin["workflow_id"] in fixed38:
                # Missing/mixed successor has an unpriced cost. The resulting
                # estimate is explicitly optimistic, even for the fixed group.
                donor_cost_unknown += 1
        savings = replay_request_shifts(plan, times, shifts)
        groups = {}
        for name, members in (("fixed18", fixed18), ("fixed38", fixed38)):
            mean = statistics.mean(savings[key] for key in members)
            baseline = statistics.mean(paths["workflows"][key]["jct_ms"] for key in members)
            groups[name] = {"mean_saved_s": mean / 1000., "percent_saved": mean / baseline * 100.}
        modes[label] = {
            "counts": dict(counts), "chosen": len(pairs), "fixed_group_unpriced_donors": donor_cost_unknown,
            "chosen_requests_without_native_capacity_quote": sum(p.consumer.rid not in quote_requests for p in pairs),
            "released_tokens": distribution(p.release_tokens for p in pairs),
            "consumer_advance_ms": distribution(p.consumer.first - p.service for p in pairs),
            "donor_restore_ms": distribution(p.restore for p in pairs),
            "groups": groups,
            "actions": [{
                "consumer_request_id": p.consumer.rid,
                "consumer_had_selected_capacity_quote": p.consumer.rid in quote_requests,
                "origin_request_id": p.call.origin["attributes"]["request_id"],
                "tool_call_identity": p.call.identity,
                "tool_start_monotonic_ms": p.call.start,
                "planned_release_tokens": p.release_tokens,
                "planned_service_advance_ms": p.consumer.first - p.service,
            } for p in pairs],
        }
    for name, observed in archive.inputs.items():
        if name in audit["input_prefixes"] and observed != audit["input_prefixes"][name]:
            raise ValueError(f"archive differs from the earlier bound audit: {name}")
    return {
        "tool_starts": len(calls), "overlapping_same_origin_tool_starts": len(calls) - len(selected_calls),
        "tool_pair_diagnostics": dict(replay_counts), "tool_starts_with_matched_forecast": len(forecast_rows),
        "native_ingresses": len(native), "native_ingresses_with_earlier_service_screen": len(consumers),
        "ingresses_with_observed_pre_service_logical_grant": len(active_consumers),
        "screened_ingresses_without_logged_quote": sum(c.rid not in quote_requests for c in consumers),
        "input_prefixes": archive.inputs, "scenarios": modes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--paths", type=Path, required=True)
    parser.add_argument("--chunk", type=int, default=2048)
    parser.add_argument("--token-bytes", type=int, default=98304)
    parser.add_argument("--slots", type=int, default=24)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.chunk, args.token_bytes, args.slots) <= 0 or args.output.exists():
        parser.error("require positive replay geometry and a fresh output")
    audit = json.loads(args.audit.read_bytes())["arms"]
    if len(audit) != 1:
        parser.error("require one sealed D2 audit arm")
    audit = next(iter(audit.values()))
    paths = json.loads(args.paths.read_bytes())
    result = evaluate(
        Archive(Path(audit["arm"])), paths, audit,
        chunk=args.chunk, token_bytes=args.token_bytes, max_running=args.slots,
    )
    result["scope"] = (
        "Fixed observed queue and batch boundaries, including background contenders. "
        "Hypothetical exclusive extents, sampled Host funding, observed DMA occupancy and approximate "
        "request slots. Only matched single-consumer staged H2D is priced on the receiver. "
        "Adaptive size uses a sampled token deficit; an exact native prefix may be larger. "
        "Observed-active estimates restrict service to the final observed logical grant interval; "
        "other estimates permit service anywhere after native ingress. "
        "No native lock inventory, evictable-set counterfactual, D3/grant counterfactual, GPU batch "
        "displacement, or oracle-timed donor prefetch. Estimates are conditional, not performance measurements. "
        "Early completion rejects a pair in every mode; false-positive backup cost is not replayed."
    )
    with args.output.open("x", encoding="ascii") as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write("\n")


if __name__ == "__main__":
    main()
