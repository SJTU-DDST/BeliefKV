"""Reconcile mixed-forward output prefixes without treating open work as complete."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.arrival_mean120_20261029.queue_pair import complete_work
from scripts.audit_decode_batch_efficiency import OutputConservation
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal

IDENTITY = ("workflow_id", "invocation_id", "context_id", "context_epoch")


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def identity(row):
    result = tuple(row.get(key) for key in IDENTITY)
    if (
        any(type(value) is not str or not value for value in result[:3])
        or type(result[3]) is not int or result[3] < 0
    ):
        raise ValueError("mixed output lacks logical request ownership")
    return result


def inspect(batches, events, receipts, *, complete=False):
    output, counts, workers = OutputConservation(), Counter(), Counter()
    owners, samples, forwards = {}, set(), set()
    frontier, domain = -1., None
    for row in batches:
        if row.get("event") != "gpu_service_sample":
            continue
        phase, members = row.get("phase"), row.get("request_samples")
        sample, forward = row.get("sample_id"), row.get("forward_iter")
        end, elapsed = row.get("complete_monotonic_ms"), row.get("service_elapsed_ms")
        clock = row.get("monotonic_clock_domain")
        if (
            phase not in ("prefill", "decode") or type(members) is not list or not members
            or type(row.get("batch_size")) is not int or len(members) != row["batch_size"]
            or type(sample) is not str or not sample or sample in samples
            or type(forward) is not int or forward in forwards
            or not finite(end) or end < 0 or end < frontier or not finite(elapsed) or elapsed < 0
            or type(clock) is not str or not clock or domain is not None and clock != domain
        ):
            raise ValueError("invalid mixed forward membership, clock or sample")
        frontier, domain = end, clock
        samples.add(sample)
        forwards.add(forward)
        request_ids = []
        for member in members:
            rid, owner = member.get("request_id"), identity(member)
            if type(rid) is not str or not rid or member.get("phase") not in ("prefill", "decode"):
                raise ValueError("mixed forward has invalid request identity or service role")
            if rid in owners and owners[rid] != owner:
                raise ValueError("mixed request changed logical ownership")
            owners[rid] = owner
            request_ids.append(rid)
        if len(set(request_ids)) != len(request_ids):
            raise ValueError("mixed forward repeats a request")
        decoding = [member for member in members if member["phase"] == "decode"]
        if (
            type(row.get("decode_batch_size")) is not int
            or row["decode_batch_size"] != len(decoding)
            or phase == "decode" and len(decoding) != len(members)
        ):
            raise ValueError("mixed decode membership differs from native observation")
        output.observe(row)
        category = "mixed_prefill" if phase == "prefill" and decoding else phase
        counts[category + "_forwards"] += 1
        workers[category + "_worker_ms"] += elapsed
        if category == "mixed_prefill":
            counts["decode_output_tokens_inside_prefill"] += sum(
                member["output_token_delta"] for member in decoding
            )
        counts["zero_output_member_observations"] += sum(
            member["output_token_delta"] == 0 for member in members
        )
    if not forwards:
        raise ValueError("mixed prefix has no observed native forwards")
    completed, all_native = {}, set()
    for event in events:
        if event.get("kind") != "llm_result":
            continue
        attributes = event["attributes"]
        rid, end = attributes.get("request_id"), attributes.get("complete_monotonic_ms")
        if (
            type(rid) is not str or not rid or rid in all_native
            or attributes.get("monotonic_clock_domain") != domain or not finite(end) or end < 0
            or type(attributes.get("output_tokens")) is not int or attributes["output_tokens"] < 0
        ):
            raise ValueError("mixed native completion is duplicated or has an invalid clock/count")
        all_native.add(rid)
        if end > frontier:
            counts["native_completions_after_forward_frontier"] += 1
            continue
        if owners.get(rid) != identity(event) or output.ends.get(rid) != attributes["output_tokens"]:
            raise ValueError("mixed output ordinals disagree with completed native request")
        completed[rid] = attributes["output_tokens"]
    observed_receipts, matched = {}, {}
    for row in receipts:
        rid, native = row.get("request_id"), row.get("native_receipt") or {}
        if (
            type(rid) is not str or not rid or rid in observed_receipts
            or row.get("exact_tokens_verified") is not True
            or native.get("native_sampler_preserved") is not True
            or native.get("sampling_path") != "position"
            or native.get("request_id") != rid or native.get("clock_domain") != domain
            or type(row.get("output_tokens")) is not int or row["output_tokens"] < 0
            or type(native.get("output_token_ids")) is not list
            or len(native["output_token_ids"]) != row["output_tokens"]
        ):
            raise ValueError("mixed prefix lacks unique exact native-sampled receipts")
        observed_receipts[rid] = row
        if rid not in completed:
            counts["receipts_outside_common_completion_prefix"] += 1
            continue
        if (
            tuple(native.get("identity", ())) != owners[rid]
            or row["output_tokens"] != completed[rid]
        ):
            raise ValueError("mixed client receipt disagrees with native ownership/output")
        matched[rid] = row
    if complete:
        if completed.keys() != observed_receipts.keys() or all_native != completed.keys():
            raise ValueError("complete mixed output has unmatched native/client completions")
        output.verify(observed_receipts)
    return {
        "counts": dict(counts), "worker_intervals": dict(workers),
        "native_forward_frontier_ms": frontier, "monotonic_clock_domain": domain,
        "observed_requests": len(output.ends), "native_completions_within_frontier": len(completed),
        "exact_receipts_in_common_prefix": len(matched),
        "native_completions_without_client_receipts": len(completed.keys() - matched.keys()),
        "requests_without_observed_native_completion": len(output.ends.keys() - completed.keys()),
        "observed_output_tokens_by_service_role": dict(output.tokens),
        "observed_total_output_tokens": sum(output.tokens.values()),
        "common_completed_output_tokens": sum(row["output_tokens"] for row in matched.values()),
        "all_observed_output_ordinals_contiguous": True, "complete_output_conservation": complete,
        "scope": (
            "Sealed output ordinal/identity checks. Open requests and cross-stream prefix differences "
            "remain explicit. Worker intervals include waits and compute; no hidden-time, pure-kernel, "
            "complete JCT or performance improvement follows from a collecting prefix."
        ),
        "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve earlier mixed output evidence")
    plan = causal.load(args.registration)
    causal.comparison.verify_sources(plan)
    if len(plan["order"]) != 1:
        raise ValueError("mixed prefix requires one explicitly registered arm")
    if args.complete:
        if complete_work(args.registration) is None:
            raise ValueError("complete mixed audit requires exact work, fresh drain and actual owned exit")
    else:
        state = causal.load(args.registration.with_suffix(".state.json"))
        if not all(causal.reservation.alive(state.get(key)) for key in ("watcher", "server", "driver")):
            raise ValueError("collecting mixed audit requires actually live owned processes")
    arm, reader = Path(plan["run_root"]) / plan["order"][0], SealedRecords()
    if causal.load(arm / "server/server_info.json").get("enable_mixed_chunk") is not True:
        raise ValueError("mixed prefix requires the actual mixed serving profile")
    result = inspect(
        reader(arm / "server/runtime_audit.jsonl"),
        reader(arm / "server/runtime_events.sglang.jsonl"),
        reader(arm / "replay/native_requests.jsonl"), complete=args.complete,
    )
    if args.complete and any(
        Path(path).stat().st_size != evidence["complete_prefix_bytes"]
        for path, evidence in reader.prefixes.items()
    ):
        raise ValueError("complete mixed journals are incomplete or changing")
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        registration=str(args.registration.resolve()),
        registration_sha256=sha256_file(args.registration),
        input_jsonl_prefixes=reader.prefixes,
        analysis_sources_sha256={
            name: sha256_file(ROOT / name) for name in (
                "scripts/audit_mixed_output_prefix.py", "scripts/audit_decode_batch_efficiency.py",
                "scripts/audit_sealed_wait_prefetch.py",
            )
        }, GPU_experiment_started=False,
    )
    causal.reservation.write_json(args.output, result)
    print(json.dumps({key: result[key] for key in (
        "counts", "observed_requests", "exact_receipts_in_common_prefix",
        "observed_total_output_tokens", "complete_output_conservation",
    )}, indent=2))


if __name__ == "__main__":
    main()
