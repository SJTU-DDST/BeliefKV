"""Match missing previously served FULL prefixes to observed prefill input ranges."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def union_tokens(intervals):
    end, total = 0, 0
    for left, right in sorted(intervals):
        total += max(0, right - max(end, left))
        end = max(end, right)
    return total


def inspect(probes, batches):
    counts, cases = Counter(), {}
    seen_probes, seen_batches, intervals, attempts = set(), set(), {}, {}
    for probe in probes:
        if probe.get("event") != "context_prefix_reuse_probe":
            continue
        request = probe["request_id"]
        if type(request) is not str or not request or request in seen_probes:
            raise ValueError("require unique native prefix probes")
        seen_probes.add(request)
        common, cached, lost = (probe[name] for name in (
            "common_previously_served_input_tokens", "native_cached_input_tokens",
            "previously_served_prefix_recompute_proxy_tokens",
        ))
        if any(type(value) is not int or value < 0 for value in (common, cached, lost)) or (
            lost != max(0, common - cached)
        ):
            raise ValueError("invalid previously served prefix geometry")
        counts["prefix_probes"] += 1
        counts["proxy_missing_input_tokens"] += lost
        if not lost:
            continue
        counts["positive_prefix_probes"] += 1
        cases[request] = {
            "request_id": request,
            **{name: probe[name] for name in (
                "workflow_id", "invocation_id", "context_id", "context_epoch",
            )},
            "cached_tokens": cached, "previous_input_common_tokens": common,
            "proxy_missing_tokens": lost, "sampled_prefill_extend_tokens": 0,
            "sampled_previous_prefix_extend_tokens": 0, "invalid_geometry_samples": 0,
            "invalid_attempt_samples": 0,
        }
        intervals[request], attempts[request] = [], set()
    affected = []
    for batch in batches:
        if batch.get("event") != "gpu_service_sample" or batch.get("phase") != "prefill":
            continue
        identity = batch.get("sample_id")
        if type(identity) is not str or not identity or identity in seen_batches:
            raise ValueError("require unique native prefill batches")
        seen_batches.add(identity)
        touched = set()
        for row in batch.get("request_samples", ()):
            case = cases.get(row.get("request_id"))
            if case is None:
                continue
            if any(case[name] != row.get(name) for name in (
                "workflow_id", "invocation_id", "context_id", "context_epoch",
            )):
                raise ValueError("prefill and prefix probe have different request ownership")
            attempt = row.get("attempt_id"), row.get("session_id"), row.get("session_generation")
            if (
                type(attempt[0]) is not int or attempt[0] < 0
                or type(attempt[1]) is not str or not attempt[1]
                or type(attempt[2]) is not int or attempt[2] < 0
            ):
                case["invalid_attempt_samples"] += 1
                continue
            attempts[row["request_id"]].add(attempt)
            start, length = row.get("sequence_tokens_before"), row.get("token_delta")
            if row.get("token_delta_semantics") != "prefill_extend_input_len" or (
                type(start) is not int or start < 0 or type(length) is not int or length < 0
            ):
                case["invalid_geometry_samples"] += 1
                continue
            case["sampled_prefill_extend_tokens"] += length
            left, right = max(start, case["cached_tokens"]), min(
                start + length, case["previous_input_common_tokens"],
            )
            if right > left:
                case["sampled_previous_prefix_extend_tokens"] += right - left
                intervals[row["request_id"]].append((left, right))
                touched.add(row["request_id"])
        if touched:
            affected.append((touched, batch.get("service_elapsed_ms")))
    for request, case in cases.items():
        observed = union_tokens(intervals[request])
        supported = (
            len(attempts[request]) == 1 and not case["invalid_attempt_samples"]
            and not case["invalid_geometry_samples"]
        )
        case.update(
            distinct_previous_prefix_tokens_in_valid_ranges=observed,
            observed_attempts=sorted(attempts[request]),
            identity_and_geometry_supported=supported,
            status=(
                "unknown_identity_or_geometry" if not supported
                else "fully_observed_old_prefix_extend" if observed == case["proxy_missing_tokens"]
                else "partially_observed_old_prefix_extend" if observed
                else "old_prefix_extend_not_observed"
            ),
        )
        counts[case["status"]] += 1
        if supported:
            counts["supported_distinct_old_prefix_extend_tokens"] += observed
            counts["supported_old_prefix_extend_tokens_including_repeated_ranges"] += case[
                "sampled_previous_prefix_extend_tokens"
            ]
    supported_ids = {request for request, case in cases.items() if case["identity_and_geometry_supported"]}
    supported_batches = [(members, duration) for members, duration in affected if members & supported_ids]
    worker_ms = sum(
        duration for _members, duration in supported_batches
        if type(duration) in (int, float) and math.isfinite(duration) and duration >= 0
    )
    return {
        "counts": dict(counts), "cases": list(cases.values()),
        "unique_prefill_batches": len(seen_batches),
        "batches_with_old_prefix_extend": len(supported_batches),
        "affected_whole_batch_worker_intervals_ms": worker_ms,
        "scope": {
            "actual_native_prefill_extend_geometry_not_only_cache_counters": True,
            "one_exact_request_attempt_and_session_required_for_supported_totals": True,
            "overlapping_ranges_counted_once_in_distinct_token_totals": True,
            "missing_geometry_or_multiple_attempts_remain_unknown": True,
            "batch_worker_intervals_not_allocated_to_tokens_or_pure_GPU_time": True,
            "Host_eviction_cause_or_avoidable_JCT_not_proven": True,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve previous prefix-work evidence")
    plan = causal.load(args.registration)
    causal.comparison.verify_sources(plan)
    execution = verify_execution(args.registration, args.cohort)
    from scripts.stop_after_measured_cohort import replay_arm

    arm = replay_arm(plan)
    capacity_path = arm / "server/native_capacity_census.json"
    capacity = causal.load(capacity_path)["capacity"]
    writer = ROOT / "beliefkv/runtime/v0520_native_telemetry.py"
    if capacity["device_mamba_slots"] != 0 or capacity["host_mamba_slots"] != 0 or (
        plan["source_server_parameters"].get("enable_mixed_chunk") is not False
        or sha256_file(writer) != plan["source_sha256"][str(writer.relative_to(ROOT))]
    ):
        raise ValueError("require FULL-only, mixed-off and the exact executed observation semantics")
    reader = SealedRecords()
    result = inspect(
        reader(arm / "server/eviction_attribution.jsonl"),
        reader(arm / "server/runtime_audit.jsonl"),
    )
    cohort = causal.load(args.cohort)
    members = {
        workflow: group for group in ("measured", "warmup", "background")
        for workflow in cohort[group + "_workflow_ids"]
    }
    for case in result["cases"]:
        case["workflow_group"] = members[case["workflow_id"]]
    if any(Path(name).stat().st_size != prefix["complete_prefix_bytes"]
           for name, prefix in reader.prefixes.items()):
        raise ValueError("the completed observation file contains an unsealed tail")
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        registration=str(args.registration.resolve()),
        complete_measured_execution_verified=True, complete_background_work_not_claimed=True,
        measured_requests=execution["measured_requests"],
        measured_output_tokens=execution["measured_output_tokens"],
        input_jsonl_prefixes=reader.prefixes,
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in (
            args.registration, args.cohort, capacity_path, Path(__file__), writer,
        )},
        GPU_action=False, performance_accepted=False,
    )
    write(args.output, result)
    print({"counts": result["counts"], "affected_prefill_batches": result["batches_with_old_prefix_extend"]})


if __name__ == "__main__":
    main()
