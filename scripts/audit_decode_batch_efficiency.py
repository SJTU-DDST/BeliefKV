#!/usr/bin/env python3
"""Audit complete decode batches without treating batch size as GPU utilization."""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
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


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def padding_bucket(size, buckets, *, graph):
    if type(size) is not int or size < 1:
        raise ValueError("decode batch must have a positive integer size")
    if type(graph) is not bool:
        raise ValueError("decode graph observation is required")
    if (
        not buckets or any(type(value) is not int or value < 1 for value in buckets)
        or list(buckets) != sorted(set(buckets))
    ):
        raise ValueError("graph buckets must be unique increasing positive integers")
    if not graph:
        return None
    index = bisect_left(buckets, size)
    if index == len(buckets):
        raise ValueError("observed graph batch exceeds configured buckets")
    return buckets[index]


def decode_view(row, *, mixed_enabled=False):
    """Keep a mixed forward's decode rows and charge its shared interval once."""
    if row["phase"] == "decode":
        return row
    samples = row["request_samples"]
    if mixed_enabled and (
        row.get("output_token_accounting")
        != "observed_output_ids_delta_all_forward_modes_v1"
    ):
        raise ValueError("mixed serving requires all-mode native output accounting")
    decoded = [sample for sample in samples if sample.get("phase") == "decode"]
    if not decoded:
        return None
    if row.get("decode_batch_size") != len(decoded):
        raise ValueError("mixed decode membership differs from the native batch")
    return {
        **row, "phase": "decode", "forward_phase": "prefill",
        "native_batch_size": row["batch_size"], "batch_size": len(decoded),
        "request_samples": decoded,
        # Prefill/mixed graph selection does not use ordinary decode buckets.
        "cuda_graph_replayed": False,
    }


class OutputConservation:
    """Reconcile measured output ordinals with every exact request receipt."""

    def __init__(self):
        self.ends, self.tokens, self.rows = {}, Counter(), 0

    def observe(self, row):
        if row.get("output_token_accounting") != "observed_output_ids_delta_all_forward_modes_v1":
            raise ValueError("all-mode output accounting is missing")
        for sample in row["request_samples"]:
            identity = sample["request_id"]
            before = sample.get("output_tokens_reported_before")
            after = sample.get("output_tokens_after")
            delta = sample.get("output_token_delta")
            if (
                any(type(value) is not int or value < 0 for value in (before, after, delta))
                or before != self.ends.get(identity, 0)
                or after - before != delta
            ):
                raise ValueError("native output ordinals are missing, duplicated or discontinuous")
            self.ends[identity] = after
            self.tokens[sample["phase"]] += delta
        self.rows += 1

    def verify(self, requests):
        if self.ends != {identity: row["output_tokens"] for identity, row in requests.items()}:
            raise ValueError("all-mode output does not match the exact replay receipts")
        return {"forwards": self.rows, "output_tokens_by_service_role": dict(self.tokens),
                "total_output_tokens": sum(self.tokens.values()),
                "exact_request_output_conserved": True}


def accumulate(counter, row, buckets, *, disable_padding=False):
    size, elapsed = row["batch_size"], row["service_elapsed_ms"]
    requests = row["request_samples"]
    if (
        not finite(elapsed) or elapsed < 0 or type(requests) is not list
        or len(requests) != size
    ):
        raise ValueError("invalid decode geometry or worker duration")
    padded = padding_bucket(size, buckets, graph=row["cuda_graph_replayed"])
    if disable_padding and padded is not None and padded != size:
        raise ValueError("graph batch has no exact bucket with padding disabled")
    identities = [item["request_id"] for item in requests]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate request in decode batch")
    for request in requests:
        if (
            request.get("token_delta_semantics") != "observed_output_ids_delta"
            or any(type(request.get(key)) is not int or request[key] < 0 for key in (
                "token_delta", "sequence_tokens_before",
            ))
        ):
            raise ValueError("decode requires observed output and context")
    counter["batches"] += 1
    counter["request_steps"] += size
    counter["worker_ms"] += elapsed
    counter["request_step_ms"] += size * elapsed
    counter["output_tokens"] += sum(item["token_delta"] for item in requests)
    counter["context_tokens"] += sum(item["sequence_tokens_before"] for item in requests)
    if padded is not None:
        counter["graph_batches"] += 1
        counter["graph_request_steps"] += size
        counter["configured_bucket_rows"] += padded
        counter["batches_needing_padding"] += padded != size
        counter["worker_ms_in_batches_needing_padding"] += elapsed * (padded != size)


def summarize(counter):
    batches, worker_ms = counter["batches"], counter["worker_ms"]
    return {
        **dict(counter),
        "mean_batch": counter["request_steps"] / batches if batches else None,
        "worker_weighted_mean_batch": (
            counter["request_step_ms"] / worker_ms if worker_ms else None
        ),
        "worker_interval_sum_s": worker_ms / 1000.,
        "mean_worker_interval_ms": worker_ms / batches if batches else None,
        "output_tokens_per_worker_interval_second": (
            counter["output_tokens"] * 1000. / worker_ms if worker_ms else None
        ),
        "mean_observed_request_context_tokens": (
            counter["context_tokens"] / counter["request_steps"]
            if counter["request_steps"] else None
        ),
        "graph_batch_fraction": counter["graph_batches"] / batches if batches else None,
        "configured_padding_rows_per_real_graph_row": (
            counter["configured_bucket_rows"] / counter["graph_request_steps"] - 1.
            if counter["graph_request_steps"] else None
        ),
    }


def eligibility_at(when, states, times, *, maximum_age_ms=1500.):
    index = bisect_right(times, when) - 1
    if index < 0 or when - times[index] > maximum_age_ms:
        return None
    admission = states[index].get("agent_admission")
    active = admission.get("active_invocation_ids") if type(admission) is dict else None
    return set(active) if type(active) is list else None


def audit(arm):
    work = audit_arm(arm)
    reader = SealedRecords()
    status_path = arm / "server/native_telemetry_status.json"
    status = json.loads(status_path.read_bytes())
    if status.get("writer_queue_depth") != 0:
        raise ValueError("native writer has not drained")
    info_path = arm / "server/server_info.json"
    info = json.loads(info_path.read_bytes())
    if (
        info.get("tp_size") != 1
        or info.get("dp_size") != 1
        or info.get("speculative_algorithm") is not None
    ):
        raise ValueError("bucket inference supports single-device ordinary decode only")
    buckets = info["cuda_graph_config"]["decode"]["bs"]
    padding_bucket(1, buckets, graph=False)
    event_path = arm / "server/runtime_events.sglang.jsonl"
    changes = []
    for event in reader(event_path):
        if event.get("kind") in ("llm_submit", "llm_result"):
            changes.append((
                event["ts_ms"], event["kind"] == "llm_result",
                event["attributes"]["request_id"], event["invocation_id"],
            ))
    if reader.prefixes[str(event_path)]["complete_prefix_sha256"] != work["events"]["sha256"]:
        raise ValueError("native request evidence changed")
    changes.sort()
    state_path = arm / "opportunities/admission_opportunities.jsonl"
    states, aging_counts = [], Counter()
    for row in reader(state_path):
        if row.get("event") != "admission_runtime_state":
            continue
        states.append(row)
        admission, paper = row.get("agent_admission") or {}, row.get("paper_baseline") or {}
        for key, count in (admission.get("policy_counts") or {}).items():
            if "starvation" in key or key.startswith("capacity_switch:"):
                aging_counts[key] = max(count, aging_counts[key])
        for key, count in (paper.get("counts") or {}).items():
            if "timeout" in key:
                aging_counts[key] = max(count, aging_counts[key])
    times = [row["ts_ms"] for row in states]
    if any(not finite(value) for value in times) or any(
        left >= right for left, right in zip(times, times[1:])
    ):
        raise ValueError("eligibility snapshot clock is not increasing")
    totals, by_batch, by_backlog = Counter(), defaultdict(Counter), defaultdict(Counter)
    phase_counts, phases = Counter(), Counter()
    seen_forwards, domains, offsets = set(), set(), []
    conservation = OutputConservation() if info["enable_mixed_chunk"] else None
    cursor, pending, last_start = 0, {}, -math.inf
    audit_path = arm / "server/runtime_audit.jsonl"
    for row in reader(audit_path):
        if row.get("event") != "gpu_service_sample":
            continue
        forward, phase = row["forward_iter"], row["phase"]
        when = row["service_start_ts_ms"]
        if (
            forward in seen_forwards or phase not in ("prefill", "decode")
            or not finite(when) or when < last_start
            or not finite(row["service_elapsed_ms"]) or row["service_elapsed_ms"] < 0
        ):
            raise ValueError("invalid or unordered native forward")
        seen_forwards.add(forward)
        last_start = when
        domains.add(row["monotonic_clock_domain"])
        offsets.append(row["complete_ts_ms"] - row["complete_monotonic_ms"])
        phases[phase] += row["service_elapsed_ms"]
        phase_counts[phase] += 1
        if conservation is not None:
            conservation.observe(row)
        row = decode_view(row, mixed_enabled=info["enable_mixed_chunk"])
        if row is None:
            continue
        while cursor < len(changes) and changes[cursor][0] <= when:
            _time, done, identity, invocation = changes[cursor]
            if done:
                pending.pop(identity, None)
            else:
                pending[identity] = invocation
            cursor += 1
        options = {"disable_padding": info["disable_cuda_graph_padding"]}
        accumulate(totals, row, buckets, **options)
        accumulate(by_batch[row["batch_size"]], row, buckets, **options)
        active = eligibility_at(when, states, times)
        selected = {item["request_id"] for item in row["request_samples"]}
        other = {identity: invocation for identity, invocation in pending.items()
                 if identity not in selected}
        category = (
            "eligibility_unknown" if active is None
            else "sampled_inactive_outstanding_request" if any(
                invocation not in active for invocation in other.values()
            )
            else "other_outstanding_only_sampled_active" if other
            else "no_other_outstanding_request"
        )
        accumulate(by_backlog[category], row, buckets, **options)
    expected = status["background_writer_timing"]["categories"]["audit:gpu_service_sample"]["records_seen"]
    if len(seen_forwards) != expected or len(domains) != 1 or max(offsets) - min(offsets) > 5.:
        raise ValueError("incomplete native forward capture or incompatible clocks")
    return {
        "arm": str(arm.resolve()), "work": work,
        "decode": summarize(totals),
        "by_batch": {str(size): summarize(value) for size, value in sorted(by_batch.items())},
        "by_sampled_eligibility": {key: summarize(value) for key, value in by_backlog.items()},
        "phase_worker_interval_sum_ms": dict(phases), "phase_batches": dict(phase_counts),
        "engine": {
            "max_running_requests": info["max_running_requests"],
            "chunked_prefill_size": info["chunked_prefill_size"],
            "enable_mixed_chunk": info["enable_mixed_chunk"],
            "configured_decode_graph_buckets": buckets,
            "disable_cuda_graph_padding": info["disable_cuda_graph_padding"],
        },
        "aging_and_capacity_switch_maximum_counts": dict(aging_counts),
        "input_jsonl_prefixes": reader.prefixes,
        "all_mode_output_accounting": (
            conservation.verify({
                row["request_id"]: row
                for row in reader(arm / "replay/native_requests.jsonl")
            }) if conservation else None
        ),
        "source_sha256": {str(path): sha256_file(path) for path in (status_path, info_path)},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True, help="LABEL=ARM_PATH")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve existing decode evidence")
    arms = {}
    for specification in args.arm:
        label, separator, path = specification.partition("=")
        if not separator or not label or not path or label in arms:
            parser.error("--arm requires a unique LABEL=ARM_PATH")
        arms[label] = audit(Path(path))
    if len({row["work"]["request_identity_and_token_count_sha256"] for row in arms.values()}) != 1:
        raise ValueError("native request identities or token counts differ")
    report = {
        "observed_at": datetime.now(timezone.utc).isoformat(), "arms": arms,
        "same_native_request_identities_and_token_counts": True,
        "analysis_source_sha256": {
            path: sha256_file(ROOT / path) for path in (
                "scripts/audit_decode_batch_efficiency.py",
                "scripts/audit_outstanding_request_throughput.py",
                "scripts/audit_sealed_wait_prefetch.py",
            )
        },
        "semantics": [
            "Complete native and replay work only. All recorded decode batches and normal tails remain included.",
            "Worker intervals include runtime and waits; their token rate is not whole-run throughput or pure CUDA throughput.",
            "Token deltas are observed output, including zero-output terminal steps; batch size counts request steps.",
            "Mixed forwards include only their decode rows in decode geometry, charging the whole shared worker interval once. This time is not additive to prefill time.",
            "Configured graph bucket rows are inferred from single-device non-speculative bucket selection. Extra rows are not proportional GPU time or attainable speedup.",
            "Eligibility is the preceding snapshot held for at most 1500ms. Outstanding means submitted and not completed; it does not prove ready compute or physical headroom.",
            "Pending inactive demand is a diagnostic co-occurrence, not a causal admission attribution or removable JCT. Missing eligibility remains unknown.",
            "Matching request identities and counts alone does not prove token contents, numerical KV correctness or isolated module gains.",
        ],
        "performance_accepted": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        label: {"decode": row["decode"], "aging": row["aging_and_capacity_switch_maximum_counts"]}
        for label, row in arms.items()
    }, indent=2))


if __name__ == "__main__":
    main()
