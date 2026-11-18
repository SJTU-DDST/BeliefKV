"""Match native Host-block loss intervals to actual FULL prefill input ranges."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.analyze_materialization_ready_supply import request_epochs
from scripts.audit_previous_prefix_prefill import union_tokens
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.stop_after_measured_cohort import replay_arm
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


OWNER_FIELDS = ("workflow_id", "invocation_id", "context_id", "context_epoch")


def cohort_membership(cohort, workflow_ids):
    names = ("measured", "warmup", "background")
    if cohort.get("kind") != "fixed_arrival_index_workflow_cohort" or any(
        type(cohort.get(name + "_workflow_ids")) is not list for name in names
    ) or not cohort["measured_workflow_ids"]:
        raise ValueError("--cohort requires the registered fixed-arrival measurement cohort")
    members = [
        (workflow, name) for name in names for workflow in cohort[name + "_workflow_ids"]
    ]
    if any(not isinstance(workflow, str) or not workflow for workflow, _ in members) or (
        len({workflow for workflow, _ in members}) != len(members)
        or {workflow for workflow, _ in members} != set(workflow_ids)
    ):
        raise ValueError("measurement cohort differs from the registered workflow population")
    return dict(members)


def _count(value):
    return type(value) is int and value >= 0


def _merged(intervals):
    result = []
    for left, right in sorted(intervals):
        if result and left <= result[-1][1]:
            result[-1] = result[-1][0], max(right, result[-1][1])
        else:
            result.append((left, right))
    return result


def inspect(attributions, batches):
    counts, cases, losses, observed, attempts = Counter(), {}, {}, {}, {}
    evictions, sample_ids, probes, namespaces, fingerprints = set(), set(), {}, {}, {}
    for row in attributions:
        if row.get("event") != "host_block_reaccess_attributed":
            continue
        if row.get("pool") != "full":
            counts["nonfull_attributions_excluded"] += 1
            continue
        counts["full_reaccess_records"] += 1
        end, units, device, host, lost = (row.get(name) for name in (
            "prefix_tokens", "evicted_units", "full_device_hit_units",
            "full_host_hit_units", "full_recomputed_units",
        ))
        if (
            any(not _count(value) for value in (end, units, device, host, lost))
            or end == 0 or units == 0 or device + host + lost != min(end, units)
            or row.get("attribution_semantics") != "exact_radix_prefix_and_full_token_interval"
        ):
            raise ValueError("Host attribution has invalid FULL interval geometry")
        identity = row.get("eviction_ids") or {}
        first, last = identity.get("first"), identity.get("last")
        if not _count(first) or not _count(last) or not 0 < first <= last or last in evictions:
            raise ValueError("Host attribution repeats or lacks an eviction identity")
        evictions.add(last)
        if not lost:
            continue
        if row.get("outcome") != (
            "full_partial_hit_and_recompute" if device + host else "full_recompute"
        ):
            raise ValueError("Host attribution outcome differs from its interval")
        request = row.get("request_id")
        if (
            any(not isinstance(row.get(name), str) or not row[name] for name in (
                "request_id", "workflow_id", "invocation_id", "context_id",
            ))
            or not _count(row.get("context_epoch"))
            or type(row.get("ts_ms")) not in (int, float) or not math.isfinite(row["ts_ms"])
        ):
            raise ValueError("Host loss lacks its exact request owner or probe clock")
        namespace = row.get("cache_namespace_hmac_blake2b128")
        fingerprint = row.get("prefix_hmac_blake2b128")
        if any(
            not isinstance(value, str) or len(value) != 32
            or any(character not in "0123456789abcdef" for character in value)
            for value in (namespace, fingerprint)
        ):
            raise ValueError("Host loss lacks exact namespaced prefix fingerprints")
        if namespaces.setdefault(request, namespace) != namespace or fingerprints.setdefault(
            (request, end), fingerprint,
        ) != fingerprint:
            raise ValueError("one request has conflicting prefix identities")
        if request not in cases:
            cases[request] = {
                "request_id": request, **{name: row[name] for name in OWNER_FIELDS},
                "attribution_records": 0, "attributed_tokens_including_overlaps": 0,
                "sampled_prefill_extend_tokens": 0, "sampled_lost_prefix_tokens": 0,
                "invalid_attempt_samples": 0, "invalid_geometry_samples": 0,
            }
            losses[request], observed[request], attempts[request], probes[request] = [], [], set(), set()
        case = cases[request]
        if any(case[name] != row[name] for name in OWNER_FIELDS):
            raise ValueError("one Host-loss request has different owners")
        case["attribution_records"] += 1
        case["attributed_tokens_including_overlaps"] += lost
        # Native cached device and Host intervals form a contiguous prefix.
        losses[request].append((end - lost, end))
        probes[request].add(row["ts_ms"])
    losses = {request: _merged(intervals) for request, intervals in losses.items()}
    affected = []
    for batch in batches:
        if batch.get("event") != "gpu_service_sample" or batch.get("phase") != "prefill":
            continue
        sample_id = batch.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id or sample_id in sample_ids:
            raise ValueError("prefill batches lack unique native sample identities")
        sample_ids.add(sample_id)
        touched = set()
        for member in batch.get("request_samples", ()):
            request = member.get("request_id")
            case = cases.get(request)
            if case is None:
                continue
            if any(case[name] != member.get(name) for name in OWNER_FIELDS):
                raise ValueError("native prefill and Host loss have different request ownership")
            attempt = member.get("attempt_id"), member.get("session_id"), member.get("session_generation")
            if not (
                _count(attempt[0]) and isinstance(attempt[1], str) and attempt[1]
                and _count(attempt[2])
            ):
                case["invalid_attempt_samples"] += 1
                continue
            attempts[request].add(attempt)
            start, length = member.get("sequence_tokens_before"), member.get("token_delta")
            if (
                member.get("phase", "prefill") != "prefill"
                or member.get("token_delta_semantics") != "prefill_extend_input_len"
                or not _count(start) or not _count(length)
            ):
                case["invalid_geometry_samples"] += 1
                continue
            case["sampled_prefill_extend_tokens"] += length
            for left, right in losses[request]:
                lo, hi = max(start, left), min(start + length, right)
                if hi > lo:
                    observed[request].append((lo, hi))
                    case["sampled_lost_prefix_tokens"] += hi - lo
                    touched.add(request)
        if touched:
            affected.append((touched, batch.get("service_elapsed_ms")))
    for request, case in cases.items():
        attributed, executed = union_tokens(losses[request]), union_tokens(observed[request])
        supported = (
            len(attempts[request]) == len(probes[request]) == 1
            and not case["invalid_attempt_samples"] and not case["invalid_geometry_samples"]
        )
        status = (
            "unknown_identity_or_geometry" if not supported else
            "fully_observed_Host_loss_prefill" if executed == attributed else
            "partially_observed_Host_loss_prefill" if executed else
            "Host_loss_prefill_not_observed"
        )
        case.update(
            distinct_attributed_tokens=attributed, distinct_observed_prefill_tokens=executed,
            observed_attempts=sorted(attempts[request]),
            attribution_probe_clocks=len(probes[request]),
            identity_and_geometry_supported=supported, status=status,
        )
        counts["positive_loss_requests"] += 1
        counts["attributed_tokens_including_overlaps"] += case["attributed_tokens_including_overlaps"]
        counts["distinct_attributed_tokens"] += attributed
        counts[status] += 1
        if supported:
            counts["supported_distinct_executed_prefill_tokens"] += executed
            counts["supported_executed_tokens_including_repeated_ranges"] += case[
                "sampled_lost_prefix_tokens"
            ]
    supported = {request for request, case in cases.items() if case["identity_and_geometry_supported"]}
    supported_batches = [duration for members, duration in affected if members & supported]
    durations = [
        value for value in supported_batches
        if type(value) in (int, float) and math.isfinite(value) and value >= 0
    ]
    return {
        "counts": dict(counts), "cases": list(cases.values()),
        "unique_prefill_batches": len(sample_ids),
        "affected_prefill_batches": len(supported_batches),
        "affected_batches_without_valid_worker_duration": len(supported_batches) - len(durations),
        "affected_whole_batch_worker_intervals_ms": sum(durations),
        "scope": {
            "lost_intervals_derived_from_native_contiguous_cached_prefix_semantics": True,
            "actual_native_prefill_input_intersections_required": True,
            "one_native_attempt_session_and_attribution_probe_clock_required": True,
            "overlapping_Host_losses_and_repeated_prefill_ranges_reported_separately": True,
            "unknown_or_unobserved_native_service_is_not_zero_cost": True,
            "whole_batch_intervals_are_not_token_cost_pure_GPU_time_or_avoidable_JCT": True,
            "live_prefix_counts_are_not_complete_experiment_results": True,
            "counterfactual_D3_benefit_proven": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--live-prefix", action="store_true")
    args = parser.parse_args()
    if args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("use a fresh output with CUDA hidden")
    plan = causal.load(args.registration)
    cohort = causal.load(args.cohort)
    groups = cohort_membership(cohort, plan["workflow_ids"])
    proof, live_owners = None, None
    if args.live_prefix:
        state = causal.load(args.registration.with_suffix(".state.json"))
        if state.get("status") != "running_full" or not all(
            causal.reservation.alive(state.get(name)) for name in ("watcher", "server", "driver")
        ):
            raise ValueError("live-prefix analysis requires the actual serving owners")
        live_owners = {
            name: causal.reservation.process(state[name]["pid"])
            for name in ("watcher", "server", "driver")
        }
    else:
        proof = verify_execution(args.registration, args.cohort)
    arm = replay_arm(plan)
    status_path = arm / "server/native_telemetry_status.json"
    status = causal.load(status_path)
    if status.get("writer_error") or any(
        status.get(name) != 0 for name in ("failed_records", "dropped_records")
    ):
        raise ValueError("native observation writer has failed or dropped records")
    capacity_path = arm / "server/native_capacity_census.json"
    capacity = causal.load(capacity_path)["capacity"]
    writer = ROOT / "beliefkv/runtime/v0520_native_telemetry.py"
    if (
        capacity["device_mamba_slots"] or capacity["host_mamba_slots"]
        or plan["source_server_parameters"].get("enable_mixed_chunk") is not False
        or sha256_file(writer) != plan["source_sha256"].get("beliefkv/runtime/v0520_native_telemetry.py")
    ):
        raise ValueError("require FULL-only, mixed-off and the exact native observation source")
    paths = (arm / "server/eviction_attribution.jsonl", arm / "server/runtime_audit.jsonl")
    if any(not path.is_file() for path in paths):
        raise ValueError("native attribution or service observations are missing")
    reader = SealedRecords()
    result = inspect(reader(paths[0]), reader(paths[1]))
    native_attribution = status.get("host_block_eviction_attribution") or {}
    if not args.live_prefix and result["counts"].get(
        "attributed_tokens_including_overlaps", 0,
    ) != native_attribution.get("recomputed_full_units"):
        raise ValueError("complete Host-loss records do not reconcile with native counters")
    expected_path = Path(plan["source_arm"]) / "expected.json"
    expected = causal.load(expected_path)
    first = request_epochs(expected)
    grouped = {name: Counter() for name in ("measured", "warmup", "background", "unknown")}
    for case in result["cases"]:
        source = expected.get(case["request_id"])
        identity = [case[name] for name in OWNER_FIELDS]
        if source is not None and source["llm_submit"][0] != identity:
            raise ValueError("Host-loss request differs from the registered source identity")
        group = groups.get(case["workflow_id"], "unknown") if source else "unknown"
        case["workflow_group"] = group
        case["source_call_kind"] = (
            "unknown" if source is None else
            "first_call" if identity[3] == first[tuple(identity[:3])] else "continuation"
        )
        counter = grouped[group]
        counter["positive_loss_requests"] += 1
        counter["distinct_attributed_tokens"] += case["distinct_attributed_tokens"]
        counter[case["source_call_kind"] + "_loss_requests"] += 1
        counter[case["status"]] += 1
        if case["identity_and_geometry_supported"]:
            counter["supported_distinct_executed_prefill_tokens"] += case[
                "distinct_observed_prefill_tokens"
            ]
    if not args.live_prefix and any(
        Path(path).stat().st_size != binding["complete_prefix_bytes"]
        for path, binding in reader.prefixes.items()
    ):
        raise ValueError("completed observation file has an unsealed tail")
    result.update(
        registration=str(args.registration.resolve()), live_prefix=args.live_prefix,
        serving_owners_verified_at_analysis_start=live_owners,
        complete_measured_execution_verified=not args.live_prefix,
        measured_execution_proof=proof, groups={name: dict(value) for name, value in grouped.items()},
        native_attribution_status_at_analysis_start=native_attribution,
        native_status_source=str(status_path),
        native_observation_health_at_analysis_start={
            name: status.get(name) for name in ("writer_error", "failed_records", "dropped_records")
        },
        native_status_counter_is_not_a_live_prefix_denominator=args.live_prefix,
        observed_at=datetime.now(timezone.utc).isoformat(), input_jsonl_prefixes=reader.prefixes,
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in (
            args.registration, args.cohort, capacity_path, expected_path, writer, Path(__file__),
            ROOT / "scripts/audit_previous_prefix_prefill.py",
            ROOT / "scripts/analyze_materialization_ready_supply.py",
        )},
        GPU_action=False, performance_accepted=False,
    )
    write(args.output, result)
    print({
        "output": str(args.output), "counts": result["counts"], "groups": result["groups"],
        "affected_prefill_batches": result["affected_prefill_batches"],
    })


if __name__ == "__main__":
    main()
