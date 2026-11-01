"""Audit complete-request normalized interactivity on a fixed measured cohort."""

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_native_system_costs import distribution, finite_number
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import stop_after_measured_cohort as stop
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def normalized_interactivity(rows):
    seconds_per_output, undefined = [], 0
    for row in rows:
        begin, end = row["submitted_monotonic_ms"], row["http_complete_monotonic_ms"]
        tokens = row["output_tokens"]
        if (
            not finite_number(begin) or not finite_number(end) or end < begin
            or type(tokens) is not int or tokens < 0
        ):
            raise ValueError("complete request timings or output count are invalid")
        if tokens == 0:
            undefined += 1
            continue
        elapsed = (end - begin) / 1000 / tokens
        if elapsed <= 0:
            raise ValueError("positive-output request requires a positive complete duration")
        seconds_per_output.append(elapsed)
    summary = distribution(seconds_per_output)
    return {
        "requests": len(rows), "positive_output_requests": len(seconds_per_output),
        "undefined_zero_output_requests": undefined,
        "complete_request_seconds_per_output_token": summary,
        "E2E_normalized_interactivity_tokens_per_second": (
            1 / summary["p90"] if seconds_per_output else None
        ),
    }


def audit(registration, cohort_path):
    execution = verify_execution(registration, cohort_path)
    plan, cohort = stop.causal.load(registration), stop.causal.load(cohort_path)
    arm = stop.replay_arm(plan)
    measured, required = set(cohort["measured_workflow_ids"]), set(execution["work"])
    reader, rows, by_workflow, first = SealedRecords(), {}, {key: [] for key in measured}, {}
    for row in reader(arm / "replay/native_requests.jsonl"):
        request = row["request_id"]
        if request not in required:
            continue
        workflow = row["native_receipt"]["identity"][0]
        if request in rows or workflow not in measured:
            raise ValueError("duplicate or foreign measured request identity")
        rows[request] = row
        by_workflow[workflow].append(row)
        observed = row.get("response_metrics") or {}
        when = observed.get("first_output_monotonic_ms")
        if when is not None and row["output_tokens"] > 0:
            if (
                not finite_number(when)
                or not row["submitted_monotonic_ms"] <= when <= row["http_complete_monotonic_ms"]
            ):
                raise ValueError("streaming first output is outside its complete request lifetime")
            first[workflow] = min(first.get(workflow, math.inf), when)
    if rows.keys() != required or any(not group for group in by_workflow.values()):
        raise ValueError("fixed measured request population is incomplete")
    starts, ends = {}, {}
    stop.boundaries(
        reader(arm / "replay/runtime_events.replay.jsonl"), starts, ends, set(plan["workflow_ids"]),
    )
    ttft = {}
    for workflow, when in first.items():
        if workflow not in starts or when < starts[workflow]:
            raise ValueError("first actual workflow output precedes its arrival")
        ttft[workflow] = when - starts[workflow]
    per_workflow = {
        key: normalized_interactivity(group) for key, group in sorted(by_workflow.items())
    }
    rates = [
        row["E2E_normalized_interactivity_tokens_per_second"]
        for row in per_workflow.values()
        if row["E2E_normalized_interactivity_tokens_per_second"] is not None
    ]
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "method": arm.name, "measured_workflows": len(measured),
        "pooled_requests": normalized_interactivity(list(rows.values())),
        "per_workflow": per_workflow,
        "equal_workflow_mean_of_per_workflow_normalized_interactivity": statistics.mean(rates) if rates else None,
        "workflow_TTFT_ms": {
            "coverage_workflows": len(ttft), "total_measured_workflows": len(measured),
            "distribution": distribution(ttft.values()), "by_workflow": dict(sorted(ttft.items())),
        },
        "semantics": [
            "Normalized interactivity is inverse P90 of complete HTTP request seconds divided by output token count.",
            "P90 uses linear interpolation; it is not P90 of token/s and does not subtract TTFT.",
            "The pooled population contains every complete request of the fixed arrival-index cohort.",
            "Per-workflow values use the same definition, then give each defined workflow equal aggregate weight.",
            "Workflow TTFT starts at original arrival and ends at the first actually streamed descendant output.",
            "Missing first-output timestamps are reported as missing coverage, never replaced by request launch.",
            "Zero-output requests keep their complete JCT but have undefined seconds/output token.",
            "This audit does not impose the former 50-token/s example as an SLO or acceptance gate.",
        ],
        "complete_measured_execution_verified": True,
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path) for path in (registration, cohort_path, Path(__file__))
        },
        "GPU_action": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve earlier interactivity evidence")
    result = audit(args.registration.resolve(), args.cohort.resolve())
    write(args.output, result)
    print(json.dumps({
        "method": result["method"], "pooled_requests": result["pooled_requests"],
        "equal_workflow_mean": result["equal_workflow_mean_of_per_workflow_normalized_interactivity"],
        "workflow_TTFT_coverage": result["workflow_TTFT_ms"]["coverage_workflows"],
    }))


if __name__ == "__main__":
    main()
