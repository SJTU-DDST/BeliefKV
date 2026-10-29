"""Measure unrelated prefill intervals between continuing decode forwards."""

from collections import Counter
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.arrival_mean120_20261029.queue_pair import complete_work
from scripts.audit_decode_transfer_envelopes import EnvelopeIndex, finite
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def owner(row):
    fields = ("request_id", "workflow_id", "invocation_id", "context_id", "context_epoch")
    value = tuple(row.get(name) for name in fields)
    if (
        any(type(item) is not str or not item for item in value[:4])
        or type(value[4]) is not int or value[4] < 0
    ):
        raise ValueError("prefill/decode member lacks observed logical ownership")
    return value


def progressing(batch):
    result = {}
    for row in batch["request_samples"]:
        if row.get("phase") != "decode":
            continue
        before, delta = row.get("output_tokens_before"), row.get("token_delta")
        if (
            row.get("token_delta_semantics") != "observed_output_ids_delta"
            or any(type(value) is not int or value < 0 for value in (before, delta))
        ):
            raise ValueError("decode gap lacks observed output progress")
        if delta:
            result[owner(row)] = before, before + delta
    return result


def inspect(batches):
    counts, previous, pending, domain = Counter(), None, [], None
    intervals, rows, owners, samples, forwards = [], [], {}, set(), set()
    last_start = -1.
    for batch in batches:
        if batch.get("event") != "gpu_service_sample":
            continue
        start, end = batch.get("service_start_monotonic_ms"), batch.get("complete_monotonic_ms")
        phase, members = batch.get("phase"), batch.get("request_samples")
        sample, forward = batch.get("sample_id"), batch.get("forward_iter")
        clock = batch.get("monotonic_clock_domain")
        if (
            not all(finite(value) for value in (start, end))
            or not 0 <= start <= end or start < last_start
            or phase not in ("prefill", "decode")
            or type(members) is not list or not members or len(members) != batch.get("batch_size")
            or type(sample) is not str or not sample or sample in samples
            or type(forward) is not int or forward in forwards
            or type(clock) is not str or not clock or domain is not None and clock != domain
        ):
            raise ValueError("invalid native sample, membership or clock in decode gap audit")
        domain, last_start = clock, start
        samples.add(sample)
        forwards.add(forward)
        identities = [owner(row) for row in members]
        if len({key[0] for key in identities}) != len(identities):
            raise ValueError("native batch repeats one request")
        for key in identities:
            if key[0] in owners and owners[key[0]] != key:
                raise ValueError("one native request changed logical ownership")
            owners[key[0]] = key
        counts[phase + "_batches"] += 1
        if phase == "prefill":
            intervals.append((start, end))
            counts["decode_members_in_prefill_batches"] += sum(
                row.get("phase") == "decode" for row in members
            )
            if previous is not None:
                pending.append(batch)
            else:
                counts["prefill_batches_before_first_decode"] += 1
            continue
        current = progressing(batch)
        if previous is not None and pending:
            before = progressing(previous)
            continuing = {
                key for key in before.keys() & current.keys()
                if current[key][0] >= before[key][1]
            }
            touched = {owner(row) for item in pending for row in item["request_samples"]}
            quiet = continuing - touched
            counts["prefill_runs_between_decode_batches"] += 1
            counts["continuing_decode_requests_observed"] += len(continuing)
            counts["continuing_requests_serviced_inside_prefill_run"] += len(continuing & touched)
            begin, finish = previous["complete_monotonic_ms"], start
            if quiet and finish > begin:
                spans = [
                    (max(begin, item["service_start_monotonic_ms"]),
                     min(finish, item["complete_monotonic_ms"]))
                    for item in pending
                    if item["service_start_monotonic_ms"] < finish
                    and item["complete_monotonic_ms"] > begin
                ]
                covered = EnvelopeIndex(spans).prefix_ms[-1]
                rows.append({
                    "previous_decode_sample_id": previous["sample_id"],
                    "next_decode_sample_id": sample,
                    "first_prefill_sample_id": pending[0]["sample_id"],
                    "last_prefill_sample_id": pending[-1]["sample_id"],
                    "prefill_batches": len(pending),
                    "decode_bookend_gap_ms": finish - begin,
                    "prefill_worker_interval_union_ms": covered,
                    "outside_prefill_intervals_ms": max(0., finish - begin - covered),
                    "quiet_continuing_requests": [list(key) for key in sorted(quiet)],
                    "quiet_continuing_workflows": len({key[1] for key in quiet}),
                })
        previous, pending = batch, []
    counts["trailing_prefill_batches_without_decode_bookend"] = len(pending)
    return {
        "counts": dict(counts),
        "prefill_worker_interval_union_ms": EnvelopeIndex(intervals).prefix_ms[-1],
        "quiet_decode_gaps": len(rows),
        "prefill_worker_ms_in_quiet_decode_gaps": sum(
            row["prefill_worker_interval_union_ms"] for row in rows
        ),
        "quiet_request_prefill_interval_ms": sum(
            len(row["quiet_continuing_requests"]) * row["prefill_worker_interval_union_ms"]
            for row in rows
        ),
        "decode_bookend_gap_ms": distribution([row["decode_bookend_gap_ms"] for row in rows]),
        "rows": rows,
        "semantics": [
            "Both decode bookends show positive observed output progress for the same logical request.",
            "Quiet requests appear in neither prefill nor mixed decode members inside the gap.",
            "This proves a recorded generation-service gap containing unrelated prefill worker intervals, not continuous native admission or absence of migration.",
            "Worker intervals include CPU, waits and compute; they are not exclusive CUDA kernel time.",
            "Per-request interval sums count a shared gap once per continuing request and are not elapsed wall time.",
            "Intervals before the first decode and after the last decode remain in coverage counters and are not evidence of a bookended gap.",
            "Mixed chunk can add compute and capacity costs; these gaps are not removable JCT or a mixed-chunk performance counterfactual.",
        ],
        "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve earlier prefill/decode gap evidence")
    plan = causal.load(args.registration)
    causal.comparison.verify_sources(plan)
    if len(plan["order"]) != 1:
        raise ValueError("gap audit requires one explicitly registered method")
    if args.complete:
        if complete_work(args.registration) is None:
            raise ValueError("gap audit requires complete exact work, fresh drain and actual exit")
    else:
        state = causal.load(args.registration.with_suffix(".state.json"))
        if not all(causal.reservation.alive(state.get(name)) for name in ("watcher", "server", "driver")):
            raise ValueError("collecting gap audit requires actual live owned processes")
    arm, reader = Path(plan["run_root"]) / plan["order"][0], SealedRecords()
    result = inspect(reader(arm / "server/runtime_audit.jsonl"))
    if args.complete and any(
        Path(path).stat().st_size != evidence["complete_prefix_bytes"]
        for path, evidence in reader.prefixes.items()
    ):
        raise ValueError("complete gap journal is still changing")
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        registration=str(args.registration.resolve()),
        registration_sha256=sha256_file(args.registration),
        complete_work_verified=args.complete, input_jsonl_prefixes=reader.prefixes,
        analysis_sources_sha256={
            name: sha256_file(ROOT / name) for name in (
                "scripts/audit_prefill_decode_gaps.py",
                "scripts/audit_decode_transfer_envelopes.py",
                "scripts/audit_native_system_costs.py",
                "scripts/audit_sealed_wait_prefetch.py",
            )
        },
        GPU_experiment_started=False,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: result[key] for key in (
        "counts", "quiet_decode_gaps", "prefill_worker_interval_union_ms",
        "prefill_worker_ms_in_quiet_decode_gaps", "decode_bookend_gap_ms",
    )}, indent=2))


if __name__ == "__main__":
    main()
