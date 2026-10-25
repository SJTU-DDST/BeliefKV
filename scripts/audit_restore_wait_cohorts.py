#!/usr/bin/env python3
"""Separate observed GPU restore waits by actual successor and invocation roles."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_causal_jct_paths import request_cohorts
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.run_causal_source_capture import reservation


def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def classify(values):
    unique = sorted(set(values))
    return unique[0] if len(unique) == 1 else "mixed:" + "+".join(unique)


def observed_wait(row, cohorts, roles, prompts):
    ids = row.get("request_ids")
    layers = row.get("layers")
    total = row.get("gpu_layer_dependency_wait_ms")
    interval = row.get("sampling_interval")
    if (
        not isinstance(ids, list) or not ids or any(type(rid) is not str or not rid for rid in ids)
        or len(ids) != len(set(ids))
        or type(row.get("batch_size")) is not int or row["batch_size"] != len(ids)
        or not isinstance(layers, list) or not layers or not finite(total)
        or type(interval) is not int or interval < 1
    ):
        raise ValueError("invalid restore sample membership or aggregate")
    seen, state, full = set(), [], []
    for layer in layers:
        index, wait = layer.get("layer"), layer.get("gpu_wait_ms")
        if type(index) is not int or index < -1 or index in seen or not finite(wait):
            raise ValueError("invalid or duplicate observed dependency layer")
        seen.add(index)
        (state if index == -1 else full).append(wait)
    if not math.isclose(sum(state) + sum(full), total, rel_tol=1e-6, abs_tol=1e-5):
        raise ValueError("observed layer waits do not reconstruct the sample")
    span = row.get("gpu_observed_layer_span_ms")
    if span is not None and (not finite(span) or span + .01 < total):
        raise ValueError("observed GPU span does not contain its dependency waits")
    offset_fields = [
        (layer.get("gpu_wait_start_offset_ms"), layer.get("gpu_wait_end_offset_ms"))
        for layer in layers
    ]
    offsets_available = all(start is not None and end is not None for start, end in offset_fields)
    if any(start is not None or end is not None for start, end in offset_fields):
        if not offsets_available or span is None:
            raise ValueError("partial layer offset evidence")
        previous = 0.
        for layer, (start, end) in zip(layers, offset_fields):
            if (
                not finite(start) or not finite(end)
                or start + .01 < previous or end + .01 < start or end > span + .01
                or not math.isclose(end - start, layer["gpu_wait_ms"], rel_tol=1e-5, abs_tol=.01)
            ):
                raise ValueError("layer offsets changed the compute-stream chronology")
            previous = end
    known_prompts = [prompts.get(rid) for rid in ids]
    prompt_class = (
        "unknown" if any(type(value) is not int or value < 0 for value in known_prompts)
        else "all_ge_100k" if min(known_prompts) >= 100_000
        else "some_ge_100k" if max(known_prompts) >= 100_000 else "all_lt_100k"
    )
    return {
        "forward_iter": row.get("forward_iter"), "request_ids": ids,
        "successor_cohort": classify(cohorts.get(rid, "unknown") for rid in ids),
        "invocation_role": classify(roles.get(rid, "unknown") for rid in ids),
        "prompt_class": prompt_class, "sampling_interval": interval,
        "total_wait_ms": total, "state_wait_ms": sum(state),
        "first_observed_full_wait_ms": full[0] if full else 0.,
        "later_observed_full_wait_ms": sum(full[1:]),
        "observed_gpu_span_ms": span,
        "between_recorded_waits_ms": max(0., span - total) if span is not None else None,
        "compute_stream_layer_offsets_available": offsets_available,
        "layers": layers,
    }


def summarize(rows):
    fields = (
        "total_wait_ms", "state_wait_ms", "first_observed_full_wait_ms",
        "later_observed_full_wait_ms", "observed_gpu_span_ms", "between_recorded_waits_ms",
    )
    return {
        "sampled_batches": len(rows),
        "distinct_sampled_requests": len({rid for row in rows for rid in row["request_ids"]}),
        "sampling_intervals": dict(Counter(row["sampling_interval"] for row in rows)),
        "batches_with_layer_offsets": sum(
            row["compute_stream_layer_offsets_available"] for row in rows
        ),
        "batches_with_later_full_wait_over_50ms": sum(
            row["later_observed_full_wait_ms"] > 50. for row in rows
        ),
        **{
            name: distribution(row[name] for row in rows if row[name] is not None)
            for name in fields
        },
    }


def analyze(rows, cohorts, roles, prompts):
    samples = [
        observed_wait(row, cohorts, roles, prompts)
        for row in rows if row.get("event") == "gpu_restore_dependency_wait"
    ]
    groups = {}
    for field in ("successor_cohort", "invocation_role", "prompt_class"):
        grouped = defaultdict(list)
        for row in samples:
            grouped[row[field]].append(row)
        groups["by_" + field] = {
            name: summarize(members) for name, members in sorted(grouped.items())
        }
    return {"summary": summarize(samples), **groups, "samples": samples}


def audit(arm):
    sealed = SealedRecords()
    replay = arm / "replay/runtime_events.replay.jsonl"
    paths = (
        [replay] if replay.is_file()
        else sorted(arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl"))
    )
    if not paths:
        raise ValueError("missing workflow events")
    if any(not (arm / "server" / name).is_file() for name in (
        "runtime_events.sglang.jsonl", "runtime_audit.jsonl",
    )):
        raise ValueError("missing native restore observation input")
    events = [row for path in paths for row in sealed(path)]
    invocations, request_invocations = {}, {}
    for row in events:
        if row["kind"] == "invocation_create":
            role = (
                "child" if row.get("parent_invocation_id")
                else "root" if row.get("relation_type") == "root" else "unknown"
            )
            invocations[(row["workflow_id"], row["invocation_id"])] = role
        elif row["kind"] == "llm_submit":
            request_invocations[row["attributes"]["request_id"]] = (
                row["workflow_id"], row["invocation_id"],
            )
    cohorts = request_cohorts(events)
    roles = {
        rid: invocations.get(invocation, "unknown") for rid, invocation in request_invocations.items()
    }
    prompts = {
        row["attributes"]["request_id"]: row["attributes"].get("prompt_tokens")
        for row in sealed(arm / "server/runtime_events.sglang.jsonl")
        if row.get("kind") == "llm_submit"
    }
    result = analyze(sealed(arm / "server/runtime_audit.jsonl"), cohorts, roles, prompts)
    result["arm"] = str(arm.resolve())
    result["input_jsonl_prefixes"] = sealed.prefixes
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    arms = {}
    for value in args.arm:
        label, separator, path = value.partition("=")
        if not separator or not label or label in arms:
            parser.error("provide distinct label=arm paths")
        arms[label] = audit(Path(path))
    report = {
        "observed_at": datetime.now(timezone.utc).isoformat(), "arms": arms,
        "scope": (
            "One record per observed sampled GPU batch, including mixed cohorts once. "
            "Native per-layer/pre-forward waits include instrumentation overhead. "
            "First FULL means first observed FULL dependency, not global layer zero. "
            "GPU spans include kernels and CPU launch gaps, not exclusive computation. "
            "Offsets are relative compute-stream events, not absolute H2D positions "
            "or load-fence wait. No sample expansion, per-request stall allocation, "
            "oracle prefetch lead, removable JCT, or isolated speedup is inferred."
        ),
        "auditor_source_sha256": {
            name: sha256_file(ROOT / name) for name in (
                "scripts/audit_restore_wait_cohorts.py", "scripts/audit_causal_jct_paths.py",
                "scripts/audit_tool_reentry_service.py", "scripts/audit_join_reentry_service.py",
                "scripts/audit_sealed_wait_prefetch.py", "scripts/audit_native_system_costs.py",
            )
        },
        "performance_accepted": False, "gpu_used": False,
    }
    if args.output.exists():
        raise FileExistsError("preserve existing audit")
    reservation.write_json(args.output, report)
    print({
        label: {
            "sampled_batches": result["summary"]["sampled_batches"],
            "total_wait_ms": result["summary"]["total_wait_ms"],
            "by_successor": {
                cohort: {
                    "batches": data["sampled_batches"],
                    "total_wait_ms": data["total_wait_ms"]["sum"],
                }
                for cohort, data in result["by_successor_cohort"].items()
            },
        }
        for label, result in arms.items()
    })


if __name__ == "__main__":
    main()
