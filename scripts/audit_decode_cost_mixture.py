"""Separate observed decode cost differences from coarse batch composition."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_decode_batch_efficiency import padding_bucket
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_useful_token_cost import add_cost, context_bin, summarize
from scripts.review_predictive_thunder_cohort import matching_traffic
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def decode_strata(rows, window, *, buckets):
    groups, seen = defaultdict(Counter), set()
    for row in rows:
        if row.get("event") != "gpu_service_sample" or row["phase"] != "decode":
            continue
        end = row["complete_monotonic_ms"]
        if not window[0] <= end < window[1]:
            continue
        members, width = row["request_samples"], row["batch_size"]
        if (
            row["forward_iter"] in seen or not members or len(members) != width
            or not math.isfinite(row["service_elapsed_ms"])
            or row["service_elapsed_ms"] < 0
            or any(member["output_token_delta"] < 0 for member in members)
        ):
            raise ValueError("invalid or duplicate decode batch")
        seen.add(row["forward_iter"])
        mean_context = sum(member["sequence_tokens_before"] for member in members) / width
        observed = {
            **row, "configured_bucket": padding_bucket(
                width, buckets, graph=row["cuda_graph_replayed"],
            ),
        }
        add_cost(groups[(width, context_bin(mean_context))], observed)
    return dict(groups)


def decompose(reference, candidate, *, minimum_forwards=30):
    if minimum_forwards < 1:
        raise ValueError("minimum_forwards must be positive")
    totals = [sum(values.values(), Counter()) for values in (reference, candidate)]
    if any(total["output_tokens"] <= 0 for total in totals):
        raise ValueError("both methods need positive decode output")
    rows, within, mixture, residual = [], 0., 0., 0.
    supported_output = [0, 0]
    for key in sorted(reference.keys() | candidate.keys()):
        values = [mapping.get(key, Counter()) for mapping in (reference, candidate)]
        supported = all(
            value["forwards"] >= minimum_forwards and value["output_tokens"] > 0
            for value in values
        )
        contribution = [
            value["worker_ms"] / total["output_tokens"]
            for value, total in zip(values, totals)
        ]
        row = {
            "width": key[0], "mean_context_bin": key[1],
            "reference": summarize(values[0]), "candidate": summarize(values[1]),
            "common_support": supported,
            "observed_cost_difference_ms_per_token": contribution[1] - contribution[0],
        }
        if supported:
            probabilities = [
                value["output_tokens"] / total["output_tokens"]
                for value, total in zip(values, totals)
            ]
            costs = [value["worker_ms"] / value["output_tokens"] for value in values]
            row["within_stratum_component_ms_per_token"] = (
                .5 * sum(probabilities) * (costs[1] - costs[0])
            )
            row["composition_component_ms_per_token"] = (
                .5 * sum(costs) * (probabilities[1] - probabilities[0])
            )
            within += row["within_stratum_component_ms_per_token"]
            mixture += row["composition_component_ms_per_token"]
            for index, value in enumerate(values):
                supported_output[index] += value["output_tokens"]
        else:
            residual += contribution[1] - contribution[0]
        rows.append(row)
    observed = (
        totals[1]["worker_ms"] / totals[1]["output_tokens"]
        - totals[0]["worker_ms"] / totals[0]["output_tokens"]
    )
    if not math.isclose(observed, within + mixture + residual, abs_tol=1e-10):
        raise ValueError("cost decomposition does not reconstruct observed difference")
    pooled = sum(supported_output)
    standardized = None
    if pooled:
        standardized = {"reference": 0., "candidate": 0.}
        for row in rows:
            if not row["common_support"]:
                continue
            weight = sum(
                row[method]["output_tokens"] for method in ("reference", "candidate")
            ) / pooled
            for method in standardized:
                standardized[method] += weight * row[method]["worker_ms_per_output_token"]
    return {
        "minimum_forwards_per_method_per_stratum": minimum_forwards,
        "strata": rows,
        "reference": summarize(totals[0]), "candidate": summarize(totals[1]),
        "common_support_output_fraction": {
            method: count / total["output_tokens"]
            for method, count, total in zip(
                ("reference", "candidate"), supported_output, totals,
            )
        },
        "observed_candidate_minus_reference_ms_per_token": observed,
        "within_stratum_component_ms_per_token": within,
        "composition_component_ms_per_token": mixture,
        "unsupported_strata_component_ms_per_token": residual,
        "pooled_output_standardized_ms_per_token": standardized,
    }


def inspect_arm(registration, cohort_path, report_path):
    execution = verify_execution(registration, cohort_path)
    plan, cohort, report = (
        json.loads(path.read_bytes()) for path in (registration, cohort_path, report_path)
    )
    if report.get("complete_measured_execution_verified") is not True:
        raise ValueError("require a complete measured-execution cost report")
    for path in (registration, cohort_path):
        if report["inputs_sha256"].get(str(path.resolve())) != sha256_file(path):
            raise ValueError("cost report belongs to different traffic or cohort")
    for name in ("measured_requests", "measured_output_tokens"):
        if report["output_conservation"][name] != execution[name]:
            raise ValueError("cost report changed the complete measured work")
    if report["common_window_offset_seconds"] != cohort["throughput_window_offset_seconds"]:
        raise ValueError("cost report changed the common window")
    prefixes = report["input_jsonl_prefixes"]
    arm = Path(plan["run_root"]) / plan["order"][0]
    events, audit = (
        arm / "replay/runtime_events.replay.jsonl", arm / "server/runtime_audit.jsonl"
    )
    for path in (events, audit):
        if (
            str(path) not in prefixes
            or path.stat().st_size != prefixes[str(path)]["complete_prefix_bytes"]
        ):
            raise ValueError("require the complete unchanged journals")
    reader = SealedRecords()
    reader.prefixes.update(prefixes)
    offsets = {
        row["workflow_id"]: row["offset_seconds"]
        for row in plan["workflow_arrivals"]["arrival_schedule"]
    }
    origins = [
        row["ts_ms"] - 1000 * offsets[row["workflow_id"]]
        for row in reader(events) if row["kind"] == "workflow_start"
    ]
    if not origins or max(origins) - min(origins) > .00001:
        raise ValueError("arrival origin changed")
    window = tuple(
        origins[0] + 1000 * value for value in cohort["throughput_window_offset_seconds"]
    )
    groups = decode_strata(
        reader(audit), window, buckets=report["engine"]["cuda_graph_config"]["decode"]["bs"],
    )
    totals = sum(groups.values(), Counter())
    recorded = report["phase_costs"]["window:decode"]
    for name in ("forwards", "request_steps", "output_tokens", "context_tokens"):
        if totals[name] != recorded[name]:
            raise ValueError("strata do not reproduce recorded decode " + name)
    if not math.isclose(totals["worker_ms"], recorded["worker_ms"], abs_tol=.001):
        raise ValueError("strata do not reproduce recorded decode worker time")
    return plan, execution, groups, {
        "report": str(report_path.resolve()), "method": plan["order"][0],
        "common_window_offset_seconds": report["common_window_offset_seconds"],
        "input_jsonl_prefixes": {str(path): prefixes[str(path)] for path in (events, audit)},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("reference", "candidate", "reference-cost", "candidate-cost", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--minimum-forwards", type=int, default=30)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve evidence and use hidden-CUDA nice19")
    inputs = (
        args.reference, args.candidate, args.reference_cost, args.candidate_cost,
        args.cohort, Path(__file__), ROOT / "scripts/audit_useful_token_cost.py",
        ROOT / "scripts/audit_sealed_wait_prefetch.py",
        ROOT / "scripts/verify_measured_execution.py",
        ROOT / "scripts/review_predictive_thunder_cohort.py",
    )
    pins = {str(path.resolve()): sha256_file(path) for path in inputs}
    reference = inspect_arm(args.reference, args.cohort, args.reference_cost)
    candidate = inspect_arm(args.candidate, args.cohort, args.candidate_cost)
    matching_traffic(candidate[0], reference[0])
    if reference[1]["work"] != candidate[1]["work"]:
        raise ValueError("the complete measured work differs")
    result = decompose(reference[2], candidate[2], minimum_forwards=args.minimum_forwards)
    if any(sha256_file(Path(name)) != digest for name, digest in pins.items()):
        raise ValueError("an analysis input changed")
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        arms={"reference": reference[3], "candidate": candidate[3]},
        inputs_sha256=pins,
        negative_difference_means_lower_candidate_cost=True,
        semantics=[
            "Each stratum uses actual decode width and the existing mean-context bin.",
            "The symmetric arithmetic decomposition reconstructs observed worker ms/output differences.",
            "Rare, absent and zero-output-only strata remain in the unsupported residual.",
            "Standardization uses identical pooled output-token weights on common support only.",
            "Coarse strata do not control exact contexts, NUMA placement, transfers or other batch costs.",
            "Worker time includes GPU work, dependency waits and CPU submission.",
            "This is descriptive accounting, not causal kernel attribution, a JCT model or an attainable speedup.",
        ],
        GPU_action=False, automatic_GPU_followup=False, performance_accepted=False,
    )
    write(args.output, result)
    print(json.dumps({key: value for key, value in result.items() if key != "strata"}))


if __name__ == "__main__":
    main()
