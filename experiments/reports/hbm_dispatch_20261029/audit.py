"""Explain complete mean60 fitting waits without attributing them to GPU idleness."""

from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.layer_free_extent_20261029.run_bound_cpu import write
from scripts.audit_sealed_wait_prefetch import SealedRecords


def main():
    prior = ROOT / "experiments/reports/layer_free_extent_20261029/capacity_and_tick_audit_r2.json"
    basis = json.loads(prior.read_bytes())
    operator = prior.with_name("capacity_and_tick_audit.py")
    if sha256_file(operator) != basis["operator_sha256"]:
        raise ValueError("preserve the original capacity audit operator")
    arm = Path(basis["arms"]["FULL"]["arm"])
    reader = SealedRecords()
    requests, by_invocation = {}, defaultdict(list)
    for row in reader(arm / "server/runtime_events.sglang.jsonl"):
        if row["kind"] != "llm_submit":
            continue
        rid = row["attributes"]["request_id"]
        if rid in requests:
            raise ValueError("native submission is not unique")
        requests[rid] = {
            "request_id": rid, "invocation_id": row["invocation_id"],
            "submit_ts_ms": row["ts_ms"], "prompt_tokens": row["attributes"]["prompt_tokens"],
        }
        by_invocation[row["invocation_id"]].append((row["ts_ms"], rid))
    first, complete = {}, {}
    for row in reader(arm / "server/runtime_audit.jsonl"):
        if row.get("event") != "gpu_service_sample":
            continue
        for sample in row["request_samples"]:
            rid = sample["request_id"]
            if rid in requests:
                first[rid] = min(first.get(rid, float("inf")), row["service_start_ts_ms"])
                complete[rid] = max(complete.get(rid, 0.), row["complete_ts_ms"])
    if len(first) != len(requests) or len(requests) != 3674:
        raise ValueError("require complete native service for all original requests")
    ordinals = {}
    for rows in by_invocation.values():
        rows.sort()
        ordinals.update({rid: ordinal for ordinal, (_when, rid) in enumerate(rows, start=1)})
    states, join_deficits, join_outcomes = [], [], Counter()
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if row["event"] == "admission_runtime_state":
            states.append(row)
        elif row["event"] == "agent_join_physical_plan_result":
            join_outcomes[row["outcome"]] += 1
            free, needed, missing = (
                row.get("observed_device_free_tokens"), row.get("required_device_tokens"),
                row.get("missing_full_tokens"),
            )
            if type(free) is int and type(needed) is int and missing and needed > free:
                join_deficits.append(row)
    fits, categories = {}, defaultdict(set)
    union_ms = 0.
    for state, following in zip(states, states[1:]):
        timestamp = state["ts_ms"]
        stop = min(following["ts_ms"], timestamp + basis["maximum_sample_age_ms"])
        if stop <= timestamp:
            raise ValueError("nonincreasing runtime snapshot clock")
        admission = state["agent_admission"]
        available = admission["capacity_tokens"] - admission["logical_active_tokens"]
        marked = set(admission["marked_for_pause_invocation_ids"])
        live = sorted(
            (request["submit_ts_ms"], rid) for rid, request in requests.items()
            if request["submit_ts_ms"] <= timestamp < complete[rid]
        )
        positions = {rid: position for position, (_when, rid) in enumerate(live)}
        latest_stop = timestamp
        for invocation in admission["paused_pending_invocation_ids"]:
            if invocation in marked:
                continue
            arrivals = by_invocation[invocation]
            index = bisect_right(arrivals, (timestamp, "\uffff")) - 1
            if index < 0:
                continue
            rid = arrivals[index][1]
            request = requests[rid]
            if not request["submit_ts_ms"] <= timestamp < first[rid]:
                continue
            if request["prompt_tokens"] + 100 > available:
                continue
            duration = min(stop, first[rid]) - timestamp
            if duration <= 0:
                continue
            reason = (
                "first_call_excluded_by_legacy_continuation_filter" if ordinals[rid] == 1 else
                "continuation_beyond_first_eight_live_requests" if positions.get(rid, 512) >= 8 else
                "continuation_in_scan_window"
            )
            record = fits.setdefault(rid, {
                **request, "ordinal": ordinals[rid], "fitting_sample_ms": 0.,
                "first_fitting_sample_ts_ms": timestamp, "sample_categories": Counter(),
            })
            record["fitting_sample_ms"] += duration
            record["sample_categories"][reason] += duration
            categories[reason].add(rid)
            latest_stop = max(latest_stop, timestamp + duration)
        union_ms += latest_stop - timestamp
    expected = basis["arms"]["FULL"]["paused_admission"]["complete"]
    if len(fits) != expected["distinct_requests_with_a_logically_fitting_sample"]:
        raise ValueError("candidate wait population differs from the complete independent audit")
    write(HERE / "audit_r1.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "arm": str(arm),
        "basis": str(prior), "basis_sha256": sha256_file(prior),
        "memory": basis["arms"]["FULL"]["memory"],
        "actual_backed_releases": basis["arms"]["FULL"]["actual_allocation_shortfall_backed_releases"],
        "new_demand_backups": basis["arms"]["FULL"]["demand_aware_new_D2H_backups"],
        "join_physical_attempt_outcomes": dict(join_outcomes),
        "join_positive_capacity_deficit_attempts": len(join_deficits),
        "join_positive_capacity_deficit_requests": len({
            row["target_key"]["request_id"] for row in join_deficits
        }),
        "fitting_paused_requests": len(fits),
        "any_fitting_paused_request_sampled_seconds": union_ms / 1000.,
        "summed_overlapping_request_fit_seconds_not_JCT": sum(
            row["fitting_sample_ms"] for row in fits.values()
        ) / 1000.,
        "requests_by_sample_category_nonexclusive": {key: len(value) for key, value in categories.items()},
        "requests": sorted(fits.values(), key=lambda row: -row["fitting_sample_ms"]),
        "input_jsonl_prefixes": reader.prefixes,
        "operator_sha256": sha256_file(Path(__file__)),
        "limitations": [
            "Raw logical fit does not establish physical allocation, safe cold donors or immediate GPU execution.",
            "Snapshot values are held for at most two seconds; unknown intervals remain unobserved.",
            "The first-eight reconstruction uses observed native worker completion, not exact dictionary removal timestamps; boundary positions are diagnostic.",
            "Reasons describe existing filters and windows, not the causal JCT effect of changing them.",
            "Backed releases and new-D2H backups are different events; neither enumerates all mandatory A-before-B dependencies.",
            "Native evictability is not useful-agent residency, coldness, or SM utilization.",
        ],
    })
    print({
        "fitting_requests": len(fits),
        "category_requests_nonexclusive": {key: len(value) for key, value in categories.items()},
        "join_shortfall_requests": len({row["target_key"]["request_id"] for row in join_deficits}),
    })


if __name__ == "__main__":
    main()
