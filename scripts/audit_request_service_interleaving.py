#!/usr/bin/env python3
"""Partition request lifetimes using recorded member worker intervals."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_causal_jct_paths import request_cohorts
from scripts.audit_decode_transfer_envelopes import EnvelopeIndex, finite
from scripts.audit_outstanding_request_throughput import audit_arm, request_identity
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.run_causal_comparison import receipt_work


def account(request_rows, samples, *, expected_samples):
    requests, clocks = {}, set()
    for row in request_rows:
        rid = row["request_id"]
        receipt = row["native_receipt"]
        submitted, complete = row["submitted_monotonic_ms"], row["native_complete_monotonic_ms"]
        identity = receipt["identity"]
        if (
            not isinstance(rid, str) or not rid or rid in requests
            or receipt.get("request_id") != rid or row.get("exact_tokens_verified") is not True
            or len(identity) != 4
            or not all(isinstance(value, str) and value for value in identity[:3])
            or type(identity[3]) is not int or identity[3] < 0
            or not all(finite(value) for value in (submitted, complete))
            or not 0 <= submitted <= complete
            or receipt.get("complete_monotonic_ms") != complete
            or not isinstance(receipt.get("clock_domain"), str) or not receipt["clock_domain"]
            or any(type(row.get(key)) is not int or row[key] < 0
                   for key in ("prompt_tokens", "output_tokens"))
        ):
            raise ValueError("invalid or duplicate exact native request receipt")
        requests[rid] = row
        clocks.add(receipt["clock_domain"])
    if not requests or len(clocks) != 1:
        raise ValueError("request population requires one observed clock domain")
    clock = next(iter(clocks))
    intervals = defaultdict(lambda: defaultdict(list))
    first, counts, forwards = {}, defaultdict(Counter), set()
    seen = 0
    last_launch = -math.inf
    for row in samples:
        if row.get("event") != "gpu_service_sample":
            continue
        seen += 1
        launch, begin, end = (
            row["launch_monotonic_ms"], row["service_start_monotonic_ms"],
            row["complete_monotonic_ms"],
        )
        phase, members, forward = row["phase"], row["request_samples"], row["forward_iter"]
        elapsed = row["service_elapsed_ms"]
        if (
            row.get("sample_id") != f"native-service-{seen:09d}"
            or type(forward) is not int or forward in forwards
            or row.get("monotonic_clock_domain") != clock
            or phase not in ("prefill", "decode")
            or not all(finite(value) for value in (launch, begin, end, elapsed))
            or not 0 <= launch <= begin <= end or launch < last_launch
            or not math.isclose(end - begin, elapsed, rel_tol=1e-9, abs_tol=.01)
            or type(members) is not list or type(row.get("batch_size")) is not int
            or row["batch_size"] < 1 or len(members) != row["batch_size"]
        ):
            raise ValueError("invalid, duplicate or missing native worker sample")
        forwards.add(forward)
        last_launch = launch
        member_ids = set()
        for member in members:
            rid = member["request_id"]
            if rid not in requests or rid in member_ids:
                raise ValueError("unknown or duplicate request member in native sample")
            member_ids.add(rid)
            request = requests[rid]
            if (
                request_identity(member) != tuple(request["native_receipt"]["identity"])
                or member.get("phase") != phase
                or type(member.get("token_delta")) is not int or member["token_delta"] < 0
                or launch < request["submitted_monotonic_ms"]
            ):
                raise ValueError("native worker sample changed request identity or chronology")
            complete = request["native_complete_monotonic_ms"]
            if launch >= complete and phase == "decode" and member["token_delta"] > 0:
                raise ValueError("positive output observed after native request completion")
            first[rid] = min(first.get(rid, launch), launch)
            count = counts[rid]
            count[phase + "_member_samples"] += 1
            count["member_worker_interval_sum_ms"] += elapsed
            left, right = max(begin, request["submitted_monotonic_ms"]), min(end, complete)
            clipped_ms = max(0., right - left)
            if end > complete:
                count["member_samples_crossing_or_after_completion"] += 1
            count["member_interval_ms_outside_request_lifetime"] += elapsed - clipped_ms
            if clipped_ms:
                intervals[rid][phase].append((left, right))
                count["lifetime_member_ms_times_batch_size"] += clipped_ms * row["batch_size"]
    if seen != expected_samples or first.keys() != requests.keys():
        raise ValueError("native request member coverage or sample count is incomplete")
    result = {}
    for rid, request in requests.items():
        start, complete = first[rid], request["native_complete_monotonic_ms"]
        if start > complete:
            raise ValueError("native request has no member launch before completion")
        own = intervals[rid]
        unions = {phase: EnvelopeIndex(own[phase]).prefix_ms[-1] for phase in ("prefill", "decode")}
        covered = EnvelopeIndex([span for spans in own.values() for span in spans]).prefix_ms[-1]
        processing = complete - start
        outside = processing - covered
        if outside < -.01:
            raise ValueError("member intervals exceed native request lifetime")
        identity = request["native_receipt"]["identity"]
        result[rid] = {
            "request_id": rid, "workflow_id": identity[0], "invocation_id": identity[1],
            "prompt_tokens": request["prompt_tokens"], "output_tokens": request["output_tokens"],
            "submit_to_first_launch_ms": start - request["submitted_monotonic_ms"],
            "first_launch_to_complete_ms": processing,
            "member_worker_interval_union_ms": covered,
            "outside_member_worker_intervals_ms": max(0., outside),
            "prefill_member_interval_union_ms": unions["prefill"],
            "decode_member_interval_union_ms": unions["decode"],
            "cross_phase_member_overlap_ms": sum(unions.values()) - covered,
            **dict(counts[rid]),
        }
    return result


def aggregate(rows):
    rows = list(rows)
    sums = {key: sum(row[key] for row in rows) for key in (
        "submit_to_first_launch_ms", "first_launch_to_complete_ms",
        "member_worker_interval_union_ms", "outside_member_worker_intervals_ms",
        "prefill_member_interval_union_ms", "decode_member_interval_union_ms",
        "cross_phase_member_overlap_ms",
    )}
    diagnostics = {
        key: sum(row.get(key, 0) for row in rows) for key in (
            "prefill_member_samples", "decode_member_samples",
            "member_worker_interval_sum_ms",
            "member_samples_crossing_or_after_completion",
            "member_interval_ms_outside_request_lifetime",
            "lifetime_member_ms_times_batch_size",
        )
    }
    clipped_sum = (
        diagnostics["member_worker_interval_sum_ms"]
        - diagnostics["member_interval_ms_outside_request_lifetime"]
    )
    diagnostics["request_member_interval_weighted_batch_size"] = (
        diagnostics["lifetime_member_ms_times_batch_size"] / clipped_sum if clipped_sum else None
    )
    return {
        "requests": len(rows), "output_tokens": sum(row["output_tokens"] for row in rows),
        "sum_ms": sums,
        "mean_ms": {key: value / len(rows) if rows else None for key, value in sums.items()},
        "membership_diagnostics": diagnostics,
    }


def path_partition(rows, workflows):
    if not workflows:
        raise ValueError("critical path workflow population is empty")
    result = {}
    for workflow, expected in workflows.items():
        ids = expected["critical_path_requests"]
        if not ids or len(set(ids)) != len(ids) or any(
            rid not in rows or rows[rid]["workflow_id"] != workflow for rid in ids
        ):
            raise ValueError("critical path request identity or population changed")
        partition = aggregate(rows[rid] for rid in ids)["sum_ms"]
        for ours, theirs in (
            ("submit_to_first_launch_ms", "request_submit_to_first_launch"),
            ("first_launch_to_complete_ms", "request_first_launch_to_native_complete"),
        ):
            if not math.isclose(partition[ours], expected["components_ms"][theirs], abs_tol=1e-4):
                raise ValueError("member evidence does not reproduce observed critical path timing")
        if not math.isclose(
            partition["first_launch_to_complete_ms"],
            partition["member_worker_interval_union_ms"] + partition["outside_member_worker_intervals_ms"],
            abs_tol=1e-4,
        ):
            raise ValueError("critical path lifetime partition is inconsistent")
        result[workflow] = {"jct_ms": expected["jct_ms"], "request_count": len(ids), **partition}
    return {
        "workflows": result,
        "workflow_equal_weight_mean_s": {
            key.removesuffix("_ms") + "_s": sum(row[key] for row in result.values()) / len(result) / 1000.
            for key in next(iter(result.values())) if key.endswith("_ms")
        },
    }


def audit(arm, expected):
    if Path(expected["arm"]).resolve() != arm.resolve():
        raise ValueError("critical path report describes another arm")
    work, reader = audit_arm(arm), SealedRecords()
    status_path = arm / "server/native_telemetry_status.json"
    status = json.loads(status_path.read_bytes())
    if status["writer_queue_depth"] != 0:
        raise ValueError("native worker telemetry must be drained")
    count = status["background_writer_timing"]["categories"]["audit:gpu_service_sample"]["records_seen"]
    requests_path = arm / "replay/native_requests.jsonl"
    rows = account(
        reader(requests_path), reader(arm / "server/runtime_audit.jsonl"), expected_samples=count,
    )
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    if len(rows) != work["requests"]:
        raise ValueError("native request and receipt populations differ")
    if sha256_file(requests_path) != json.loads((arm / "replay/summary.json").read_bytes())["native_requests_sha256"]:
        raise ValueError("request receipts changed after exact-work replay")
    prior_prefixes = {str(Path(path).resolve()): value for path, value in expected["input_jsonl_prefixes"].items()}
    for path, prefix in reader.prefixes.items():
        if prefix != prior_prefixes.get(str(Path(path).resolve())):
            raise ValueError("critical path input prefixes changed")
    cohorts = request_cohorts(events)
    grouped = defaultdict(list)
    for rid, row in rows.items():
        row["cohort"] = cohorts[rid]
        grouped[row["cohort"]].append(row)
    return {
        "arm": str(arm.resolve()), "all_requests": aggregate(rows.values()),
        "by_cohort": {key: aggregate(value) for key, value in grouped.items()},
        "critical_path": path_partition(rows, expected["workflows"]),
        "requests": rows, "work": work, "input_jsonl_prefixes": reader.prefixes,
        "native_status": {"path": str(status_path.resolve()), "sha256": sha256_file(status_path)},
    }, receipt_work(requests_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous request service audits")
    paths = json.loads(args.paths.read_bytes())
    if paths.get("same_request_work_verified") is not True:
        parser.error("complete exact-work critical path report is required")
    arms, work = {}, None
    for label, expected in paths["arms"].items():
        arms[label], observed = audit(Path(expected["arm"]), expected)
        if work is not None and observed != work:
            raise ValueError("request tokens, identities or stopping conditions differ")
        work = observed
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(), "arms": arms,
        "same_exact_request_work_verified": True,
        "critical_path_report": {"path": str(args.paths.resolve()), "sha256": sha256_file(args.paths)},
        "source_sha256": {
            name: sha256_file(ROOT / name) for name in (
                "scripts/audit_request_service_interleaving.py",
                "scripts/audit_causal_jct_paths.py",
                "scripts/audit_decode_transfer_envelopes.py",
                "scripts/audit_outstanding_request_throughput.py",
                "scripts/audit_sealed_wait_prefetch.py",
                "scripts/run_causal_comparison.py",
                "beliefkv/runtime/v0520_native_telemetry.py",
            )
        },
        "scope": [
            "Every request, native worker sample and ordinary workflow tail is retained. Equal work includes exact receipt hashes and stopping conditions.",
            "First launch is scheduler.run_batch entry. Member worker intervals use service_start_monotonic_ms, not CUDA kernel timing.",
            "Membership intervals are clipped to actual submission/completion and unioned per request; overlapped stale member snapshots outside that lifetime remain counted separately.",
            "Outside-member intervals include all unaccounted gaps after launch, including overlap accounting and scheduler/runtime waiting; they are not measured GPU stalls, preemption or removable JCT.",
            "Independent prefill/decode unions may overlap and are not additive. Total member union plus outside-member time reproduces the observed request lifetime.",
            "Critical path sums reproduce the previously observed arm-specific paths; differing batch shapes and paths prevent causal policy or speedup attribution.",
        ],
        "performance_accepted": False, "gpu_experiment_started": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        label: row["critical_path"]["workflow_equal_weight_mean_s"]
        for label, row in arms.items()
    }, indent=2))


if __name__ == "__main__":
    main()
